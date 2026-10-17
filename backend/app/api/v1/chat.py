import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_current_user, get_current_user_stateless
from app.core.errors import ServiceUnavailableError
from app.db.session import SessionLocal, get_db
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.rag.agent.loop import AgentLoopResult, AgentStepRecord, stream_agent
from app.rag.llm import get_llm_client
from app.rag.prompt import build_llm_messages
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import agent_service, chat_service, kb_service

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
StreamUser = Annotated[User, Depends(get_current_user_stateless)]

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


def _merge_usage(total: dict[str, Any], usage: Any) -> None:
    if usage is None:
        return
    payload = usage.model_dump() if hasattr(usage, "model_dump") else usage
    if not isinstance(payload, dict):
        return
    for key, value in payload.items():
        if isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + value


@router.post("/kbs/{kb_id}/chat", response_model=ChatResponse)
async def chat(
    kb_id: UUID,
    payload: ChatRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> ChatResponse:
    kb = await kb_service.get_owned_kb(db, kb_id, current_user.id)
    if payload.mode == "agent":
        if not get_settings().agent_enabled:
            raise ServiceUnavailableError("Agent 模式未启用")
        conversation, bundle = await agent_service.answer_agent_once(
            db,
            kb,
            current_user.id,
            payload.question,
            payload.conversation_id,
        )
    else:
        conversation, bundle = await chat_service.answer_once(
            db,
            kb,
            current_user.id,
            payload.question,
            payload.conversation_id,
        )
    await db.commit()
    return ChatResponse(
        conversation_id=conversation.id,
        message=chat_service.to_message_out(bundle),
    )


@router.post("/kbs/{kb_id}/chat/stream")
async def chat_stream(
    kb_id: UUID,
    payload: ChatRequest,
    current_user: StreamUser,
) -> StreamingResponse:
    # Retrieval and the user turn are persisted before streaming starts, so the
    # generator holds no database session while tokens are being produced.
    if payload.mode == "agent":
        if not get_settings().agent_enabled:
            raise ServiceUnavailableError("Agent 模式未启用")
        # Resolve the client before persisting the user turn so a missing key
        # fails the request without leaving a dangling message.
        client = get_llm_client()
        turn = await agent_service.prepare_turn(
            kb_id,
            current_user.id,
            payload.question,
            payload.conversation_id,
        )
        return StreamingResponse(
            _agent_event_stream(turn, client),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    preparation = await chat_service.prepare_stream(
        kb_id,
        current_user.id,
        payload.question,
        payload.conversation_id,
    )
    return StreamingResponse(
        _event_stream(preparation, payload.question),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _agent_event_stream(
    turn: agent_service.AgentTurn,
    client: Any,
) -> AsyncIterator[str]:
    """Stream a bounded agent run: planning steps, trace, then the grounded answer.

    The planning loop runs inside a short session; that session is closed before
    the final completion starts streaming, so no connection is pinned while
    tokens are produced.
    """
    started = time.perf_counter()
    settings = get_settings()

    yield _sse(
        "meta",
        {
            "conversation_id": str(turn.conversation_id),
            "user_message_id": str(turn.user_message_id),
            "mode": "agent",
            "rewritten_query": turn.question,
            "rewrite_applied": False,
            "reranked": False,
        },
    )

    steps: list[AgentStepRecord] = []
    result: AgentLoopResult | None = None
    try:
        async with SessionLocal() as db:
            kb = await db.get(KnowledgeBase, turn.kb_id)
            if kb is None:
                yield _sse("error", {"detail": "知识库不存在"})
                return
            async for event in stream_agent(
                db,
                kb,
                turn.question,
                turn.history,
                client,
                turn.config,
                settings,
            ):
                if event.type == "step" and event.step is not None:
                    steps.append(event.step)
                    yield _sse("agent_step", event.step.to_dict())
                elif event.type == "result":
                    result = event.result
    except asyncio.CancelledError:
        await asyncio.shield(
            _persist_agent(
                turn,
                content="",
                result=None,
                steps=steps,
                status="cancelled",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        )
        raise
    except Exception:
        logger.exception("agent loop failed for conversation %s", turn.conversation_id)
        await _persist_agent(
            turn,
            content="",
            result=None,
            steps=steps,
            status="failed",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        yield _sse("error", {"detail": "Agent 执行失败，请稍后重试"})
        return

    if result is None:  # pragma: no cover - stream_agent always emits a result
        yield _sse("error", {"detail": "Agent 未返回结果"})
        return

    yield _sse("retrieval", result.to_trace(turn.question))

    if not result.evidence:
        latency_ms = int((time.perf_counter() - started) * 1000)
        bundle = await _persist_agent(
            turn,
            content=chat_service.REFUSAL_MESSAGE,
            result=result,
            steps=result.steps,
            status="completed",
            latency_ms=latency_ms,
        )
        yield _sse("token", {"delta": chat_service.REFUSAL_MESSAGE})
        yield _sse("done", _done_payload(turn, bundle, latency_ms))
        return

    messages = build_llm_messages(
        turn.question,
        result.evidence,
        turn.history,
        max_context_chars=settings.max_context_chars,
    )
    parts: list[str] = []
    usage: dict[str, Any] = dict(result.token_usage or {})
    try:
        stream = await client.chat.completions.create(
            **chat_service.llm_stream_payload(messages)
        )
        async for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                _merge_usage(usage, chunk.usage)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                parts.append(delta)
                yield _sse("token", {"delta": delta})
    except asyncio.CancelledError:
        # Client stopped the run: keep the partial answer together with the steps
        # that were already executed.
        await asyncio.shield(
            _persist_agent(
                turn,
                content="".join(parts),
                result=result,
                steps=result.steps,
                status="cancelled",
                latency_ms=int((time.perf_counter() - started) * 1000),
                usage=usage or None,
            )
        )
        raise
    except Exception:
        logger.exception("agent generation failed for conversation %s", turn.conversation_id)
        await _persist_agent(
            turn,
            content="".join(parts),
            result=result,
            steps=result.steps,
            status="failed",
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage=usage or None,
        )
        yield _sse("error", {"detail": "生成失败，请稍后重试"})
        return

    latency_ms = int((time.perf_counter() - started) * 1000)
    bundle = await _persist_agent(
        turn,
        content="".join(parts) or chat_service.REFUSAL_MESSAGE,
        result=result,
        steps=result.steps,
        status="completed",
        latency_ms=latency_ms,
        usage=usage or None,
    )
    yield _sse("done", _done_payload(turn, bundle, latency_ms))


def _done_payload(turn: agent_service.AgentTurn, bundle: Any, latency_ms: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "conversation_id": str(turn.conversation_id),
        "status": "completed",
        "mode": "agent",
        "latency_ms": latency_ms,
    }
    if bundle is not None:
        payload["message"] = chat_service.to_message_out(bundle).model_dump(mode="json")
    return payload


async def _persist_agent(
    turn: agent_service.AgentTurn,
    *,
    content: str,
    result: AgentLoopResult | None,
    steps: list[AgentStepRecord],
    status: str,
    latency_ms: int,
    usage: dict[str, Any] | None = None,
):
    agent = (
        agent_service.to_persistence(result, token_usage=usage)
        if result is not None
        else agent_service.persistence_from_steps(
            steps,
            stopped_reason=status,
            latency_ms=latency_ms,
            usage=usage,
        )
    )
    outcome = agent_service.to_outcome(turn.question, result) if result is not None else None
    try:
        return await chat_service.save_answer_with_agent(
            turn.conversation_id,
            question=turn.question,
            content=content,
            outcome=outcome,
            agent=agent,
            status=status,
            latency_ms=latency_ms,
            usage=usage,
        )
    except Exception:
        logger.exception("failed to persist agent answer")
        return None


async def _event_stream(
    preparation: chat_service.StreamPreparation,
    question: str,
) -> AsyncIterator[str]:
    started = time.perf_counter()
    outcome = preparation.outcome

    yield _sse(
        "meta",
        {
            "conversation_id": str(preparation.conversation_id),
            "user_message_id": str(preparation.user_message_id),
            "mode": "rag",
            "rewritten_query": outcome.rewritten_query,
            "rewrite_applied": outcome.trace.get("rewrite_applied", False),
            "reranked": outcome.trace.get("reranked", False),
        },
    )
    yield _sse("retrieval", outcome.trace)

    if not outcome.candidates:
        refusal_bundle = await _persist(
            preparation,
            question=question,
            content=chat_service.REFUSAL_MESSAGE,
            status="completed",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        yield _sse("token", {"delta": chat_service.REFUSAL_MESSAGE})
        refusal_done: dict[str, Any] = {
            "conversation_id": str(preparation.conversation_id),
            "status": "completed",
        }
        if refusal_bundle is not None:
            refusal_done["message"] = chat_service.to_message_out(refusal_bundle).model_dump(
                mode="json"
            )
        yield _sse("done", refusal_done)
        return

    parts: list[str] = []
    usage: dict[str, Any] | None = None
    try:
        stream = await preparation.client.chat.completions.create(
            **chat_service.llm_stream_payload(preparation.llm_messages)
        )
        async for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage.model_dump()
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                parts.append(delta)
                yield _sse("token", {"delta": delta})
    except asyncio.CancelledError:
        # Client hit "stop" or disconnected: keep the partial answer.
        await asyncio.shield(
            _persist(
                preparation,
                question=question,
                content="".join(parts),
                status="cancelled",
                latency_ms=int((time.perf_counter() - started) * 1000),
                usage=usage,
            )
        )
        raise
    except Exception:
        logger.exception(
            "streaming generation failed for conversation %s",
            preparation.conversation_id,
        )
        await _persist(
            preparation,
            question=question,
            content="".join(parts),
            status="failed",
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage=usage,
        )
        yield _sse("error", {"detail": "生成失败，请稍后重试"})
        return

    latency_ms = int((time.perf_counter() - started) * 1000)
    bundle = await _persist(
        preparation,
        question=question,
        content="".join(parts),
        status="completed",
        latency_ms=latency_ms,
        usage=usage,
    )
    done_payload: dict[str, Any] = {
        "conversation_id": str(preparation.conversation_id),
        "status": "completed",
        "latency_ms": latency_ms,
    }
    if bundle is not None:
        done_payload["message"] = chat_service.to_message_out(bundle).model_dump(mode="json")
    yield _sse("done", done_payload)


async def _persist(
    preparation: chat_service.StreamPreparation,
    *,
    question: str,
    content: str,
    status: str,
    latency_ms: int,
    usage: dict[str, Any] | None = None,
):
    try:
        return await chat_service.save_streamed_answer(
            preparation.conversation_id,
            question=question,
            content=content,
            outcome=preparation.outcome,
            status=status,
            latency_ms=latency_ms,
            usage=usage,
        )
    except Exception:
        logger.exception("failed to persist streamed answer")
        return None
