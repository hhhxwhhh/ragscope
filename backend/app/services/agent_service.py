"""Agentic RAG orchestration: turn preparation, persistence and non-streaming runs."""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.conversation import Conversation
from app.models.knowledge_base import KnowledgeBase
from app.models.message import Message, MessageRole, MessageStatus
from app.rag.agent.loop import AgentLoopResult, AgentStepRecord, stream_agent
from app.rag.llm import get_llm_client
from app.rag.pipeline import RetrievalConfig, RetrievalOutcome
from app.rag.prompt import build_llm_messages
from app.services import chat_service
from app.services.kb_service import get_owned_kb

logger = logging.getLogger(__name__)


@dataclass
class AgentTurn:
    conversation_id: UUID
    user_message_id: UUID
    kb_id: UUID
    question: str
    history: list[tuple[str, str]]
    config: RetrievalConfig


async def prepare_turn(
    kb_id: UUID,
    user_id: UUID,
    question: str,
    conversation_id: UUID | None,
) -> AgentTurn:
    """Ownership check, conversation creation and user turn persistence.

    Runs in a short session that is closed before any tool or token streaming,
    matching the single-shot path.
    """
    async with SessionLocal() as db:
        kb = await get_owned_kb(db, kb_id, user_id)
        conversation = await chat_service.ensure_conversation(
            db, kb.id, user_id, conversation_id, question
        )
        history = await chat_service.get_history(
            db, conversation.id, get_settings().history_messages
        )
        user_message = Message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content=question,
            status=MessageStatus.COMPLETED,
        )
        db.add(user_message)
        await db.flush()
        conversation.last_message_at = datetime.now(UTC)
        config = RetrievalConfig.from_kb(kb)
        await db.commit()
        return AgentTurn(
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            kb_id=kb.id,
            question=question,
            history=history,
            config=config,
        )


def to_outcome(question: str, result: AgentLoopResult) -> RetrievalOutcome:
    """Expose collected evidence through the same shape as single-shot retrieval."""
    return RetrievalOutcome(
        query=question,
        rewritten_query=question,
        candidates=result.evidence,
        trace=result.to_trace(question),
    )


def to_persistence(
    result: AgentLoopResult,
    *,
    token_usage: dict[str, Any] | None = None,
) -> chat_service.AgentPersistence:
    return chat_service.AgentPersistence(
        stopped_reason=result.stopped_reason,
        steps=[step.to_dict() for step in result.steps],
        step_count=len(result.steps),
        tool_calls=result.tool_calls,
        latency_ms=result.latency_ms,
        token_usage=token_usage if token_usage is not None else result.token_usage,
        error=result.error,
    )


def persistence_from_steps(
    steps: list[AgentStepRecord],
    *,
    stopped_reason: str,
    latency_ms: int,
    error: str | None = None,
    token_usage: dict[str, Any] | None = None,
) -> chat_service.AgentPersistence:
    """Build a run record when the loop was cancelled before yielding a result."""
    return chat_service.AgentPersistence(
        stopped_reason=stopped_reason,
        steps=[step.to_dict() for step in steps],
        step_count=len(steps),
        tool_calls=len(steps),
        latency_ms=latency_ms,
        token_usage=token_usage,
        error=error,
    )


def _merge_usage(total: dict[str, Any], usage: Any) -> None:
    if usage is None:
        return
    payload = usage.model_dump() if hasattr(usage, "model_dump") else usage
    if not isinstance(payload, dict):
        return
    for key, value in payload.items():
        if isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + value


async def answer_agent_once(
    db: AsyncSession,
    kb: KnowledgeBase,
    user_id: UUID,
    question: str,
    conversation_id: UUID | None,
) -> tuple[Conversation, chat_service.MessageBundle]:
    """Non-streaming agent path used by the JSON endpoint and tests."""
    started = time.perf_counter()
    settings = get_settings()
    client = get_llm_client()

    conversation = await chat_service.ensure_conversation(
        db, kb.id, user_id, conversation_id, question
    )
    history = await chat_service.get_history(db, conversation.id, settings.history_messages)
    db.add(
        Message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content=question,
            status=MessageStatus.COMPLETED,
        )
    )
    await db.flush()

    result: AgentLoopResult | None = None
    async for event in stream_agent(
        db,
        kb,
        question,
        history,
        client,
        RetrievalConfig.from_kb(kb),
        settings,
    ):
        if event.type == "result":
            result = event.result

    if result is None:  # pragma: no cover - the generator always yields a result
        raise RuntimeError("agent loop ended without a result")

    usage = dict(result.token_usage or {})
    if result.evidence:
        completion = await client.chat.completions.create(
            **chat_service.llm_payload(
                build_llm_messages(
                    question,
                    result.evidence,
                    history,
                    max_context_chars=settings.max_context_chars,
                ),
                stream=False,
            )
        )
        answer = completion.choices[0].message.content or chat_service.REFUSAL_MESSAGE
        _merge_usage(usage, completion.usage)
    else:
        answer = chat_service.REFUSAL_MESSAGE

    latency_ms = int((time.perf_counter() - started) * 1000)
    bundle = await chat_service.save_agent_answer_in_session(
        db,
        conversation,
        question=question,
        content=answer,
        outcome=to_outcome(question, result),
        agent=to_persistence(result, token_usage=usage or None),
        status=MessageStatus.COMPLETED,
        latency_ms=latency_ms,
        usage=usage or None,
    )
    await db.flush()
    return conversation, bundle
