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

from app.core.deps import get_current_user, get_current_user_stateless
from app.db.session import get_db
from app.models.user import User
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import chat_service, kb_service

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
StreamUser = Annotated[User, Depends(get_current_user_stateless)]


def _sse(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


@router.post("/kbs/{kb_id}/chat", response_model=ChatResponse)
async def chat(
    kb_id: UUID,
    payload: ChatRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> ChatResponse:
    kb = await kb_service.get_owned_kb(db, kb_id, current_user.id)
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
    preparation = await chat_service.prepare_stream(
        kb_id,
        current_user.id,
        payload.question,
        payload.conversation_id,
    )
    return StreamingResponse(
        _event_stream(preparation, payload.question),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
