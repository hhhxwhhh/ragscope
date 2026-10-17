"""Conversation orchestration: history, retrieval, grounded generation, citations."""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.db.session import SessionLocal
from app.models.agent import AgentRun, AgentStep
from app.models.citation import Citation
from app.models.conversation import Conversation
from app.models.feedback import MessageFeedback
from app.models.knowledge_base import KnowledgeBase
from app.models.message import Message, MessageRole, MessageStatus
from app.models.retrieval_trace import RetrievalTrace
from app.rag.answer import complete_answer
from app.rag.llm import get_chat_model
from app.rag.pipeline import RetrievalOutcome, retrieve
from app.rag.prompt import citation_stats, extract_citation_ranks
from app.schemas.chat import (
    AgentRunOut,
    AgentStepOut,
    CitationOut,
    FeedbackOut,
    MessageOut,
    RetrievalTraceOut,
)

logger = logging.getLogger(__name__)

REFUSAL_MESSAGE = "根据现有资料无法回答这个问题。"
DEFAULT_TITLE = "新对话"


@dataclass
class MessageBundle:
    message: Message
    citations: list[Citation]
    trace: RetrievalTrace | None
    feedback: MessageFeedback | None = None
    agent_run: AgentRun | None = None
    agent_steps: list[AgentStep] | None = None


@dataclass
class AgentPersistence:
    """Serializable agent outcome handed to the message writer."""

    stopped_reason: str | None
    steps: list[dict[str, Any]]
    step_count: int
    tool_calls: int
    latency_ms: int
    token_usage: dict[str, Any] | None
    error: str | None = None


def to_message_out(bundle: MessageBundle) -> MessageOut:
    out = MessageOut.model_validate(bundle.message)
    out.citations = [CitationOut.model_validate(citation) for citation in bundle.citations]
    out.trace = RetrievalTraceOut.model_validate(bundle.trace) if bundle.trace is not None else None
    out.feedback = FeedbackOut.model_validate(bundle.feedback) if bundle.feedback else None
    if bundle.agent_run is not None:
        out.agent = AgentRunOut(
            id=bundle.agent_run.id,
            status=bundle.agent_run.status,
            stopped_reason=bundle.agent_run.stopped_reason,
            step_count=bundle.agent_run.step_count,
            tool_calls=bundle.agent_run.tool_calls,
            latency_ms=bundle.agent_run.latency_ms,
            error=bundle.agent_run.error,
            steps=[
                AgentStepOut.model_validate(step) for step in (bundle.agent_steps or [])
            ],
        )
    return out


async def get_owned_message(
    db: AsyncSession,
    message_id: UUID,
    user_id: UUID,
) -> Message:
    message = await db.scalar(
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .join(KnowledgeBase, Conversation.kb_id == KnowledgeBase.id)
        .where(
            Message.id == message_id,
            Conversation.user_id == user_id,
            KnowledgeBase.owner_id == user_id,
        )
    )
    if message is None:
        raise NotFoundError("消息不存在")
    return message


async def get_previous_user_message(
    db: AsyncSession,
    message: Message,
) -> Message | None:
    """Find the question that produced an assistant answer."""
    return await db.scalar(
        select(Message)
        .where(
            Message.conversation_id == message.conversation_id,
            Message.role == MessageRole.USER,
            Message.created_at <= message.created_at,
            Message.id != message.id,
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )


async def get_owned_conversation(
    db: AsyncSession,
    conversation_id: UUID,
    user_id: UUID,
) -> Conversation:
    conversation = await db.scalar(
        select(Conversation)
        .join(KnowledgeBase, Conversation.kb_id == KnowledgeBase.id)
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            KnowledgeBase.owner_id == user_id,
        )
    )
    if conversation is None:
        raise NotFoundError("会话不存在")
    return conversation


async def list_conversations(
    db: AsyncSession,
    kb_id: UUID,
    user_id: UUID,
    offset: int,
    limit: int,
    q: str | None = None,
) -> tuple[list[Conversation], int]:
    condition = (Conversation.kb_id == kb_id) & (Conversation.user_id == user_id)
    if q and q.strip():
        condition = condition & Conversation.title.ilike(f"%{q.strip()}%")
    items = (
        await db.scalars(
            select(Conversation)
            .where(condition)
            .order_by(Conversation.last_message_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    total = await db.scalar(select(func.count()).select_from(Conversation).where(condition))
    return list(items), int(total or 0)


async def create_conversation(
    db: AsyncSession,
    kb_id: UUID,
    user_id: UUID,
    title: str | None = None,
) -> Conversation:
    conversation = Conversation(
        kb_id=kb_id,
        user_id=user_id,
        title=(title or DEFAULT_TITLE)[:200],
    )
    db.add(conversation)
    await db.flush()
    return conversation


async def ensure_conversation(
    db: AsyncSession,
    kb_id: UUID,
    user_id: UUID,
    conversation_id: UUID | None,
    question: str,
) -> Conversation:
    if conversation_id is None:
        return await create_conversation(db, kb_id, user_id, question[:60])

    conversation = await get_owned_conversation(db, conversation_id, user_id)
    if conversation.kb_id != kb_id:
        raise NotFoundError("会话不存在")
    return conversation


async def delete_conversation(db: AsyncSession, conversation: Conversation) -> None:
    await db.delete(conversation)


async def export_conversation_markdown(db: AsyncSession, conversation: Conversation) -> str:
    """Render a conversation as Markdown for sharing / offline review."""
    bundles, _ = await list_messages(db, conversation.id, 0, 1000)
    lines = [f"# {conversation.title}", ""]
    for bundle in bundles:
        message = bundle.message
        role = "用户" if message.role == MessageRole.USER else "助手"
        lines.append(f"## {role}")
        lines.append("")
        lines.append(message.content or "（空）")
        lines.append("")
        if message.role == MessageRole.ASSISTANT and bundle.citations:
            lines.append("**引用来源**")
            lines.append("")
            for citation in bundle.citations:
                page = f" · 第 {citation.page_number} 页" if citation.page_number else ""
                marker = "" if citation.cited else "（未在回答中引用）"
                lines.append(
                    f"- [{citation.rank}] {citation.filename} · "
                    f"{citation.heading_path or '无标题'} · chunk #{citation.chunk_index}"
                    f"{page}{marker}"
                )
            lines.append("")
    return "\n".join(lines).strip() + "\n"


async def get_history(
    db: AsyncSession,
    conversation_id: UUID,
    limit: int,
) -> list[tuple[str, str]]:
    rows = (
        await db.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.status == MessageStatus.COMPLETED,
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        (message.role, message.content) for message in reversed(rows) if message.content.strip()
    ]


async def list_messages(
    db: AsyncSession,
    conversation_id: UUID,
    offset: int,
    limit: int,
) -> tuple[list[MessageBundle], int]:
    messages = list(
        (
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at, Message.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    total = await db.scalar(
        select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id)
    )
    if not messages:
        return [], int(total or 0)

    message_ids = [message.id for message in messages]
    citations = (
        await db.scalars(
            select(Citation)
            .where(Citation.message_id.in_(message_ids))
            .order_by(Citation.message_id, Citation.rank)
        )
    ).all()
    traces = (
        await db.scalars(select(RetrievalTrace).where(RetrievalTrace.message_id.in_(message_ids)))
    ).all()
    feedbacks = (
        await db.scalars(
            select(MessageFeedback).where(MessageFeedback.message_id.in_(message_ids))
        )
    ).all()
    agent_runs = (
        await db.scalars(select(AgentRun).where(AgentRun.message_id.in_(message_ids)))
    ).all()
    agent_steps: list[AgentStep] = []
    if agent_runs:
        agent_steps = list(
            (
                await db.scalars(
                    select(AgentStep)
                    .where(AgentStep.run_id.in_([run.id for run in agent_runs]))
                    .order_by(AgentStep.run_id, AgentStep.step_index)
                )
            ).all()
        )

    citations_by_message: dict[UUID, list[Citation]] = {}
    for citation in citations:
        citations_by_message.setdefault(citation.message_id, []).append(citation)
    trace_by_message = {trace.message_id: trace for trace in traces}
    feedback_by_message = {feedback.message_id: feedback for feedback in feedbacks}
    run_by_message = {run.message_id: run for run in agent_runs}
    steps_by_run: dict[UUID, list[AgentStep]] = {}
    for step in agent_steps:
        steps_by_run.setdefault(step.run_id, []).append(step)

    bundles = []
    for message in messages:
        run = run_by_message.get(message.id)
        bundles.append(
            MessageBundle(
                message=message,
                citations=citations_by_message.get(message.id, []),
                trace=trace_by_message.get(message.id),
                feedback=feedback_by_message.get(message.id),
                agent_run=run,
                agent_steps=steps_by_run.get(run.id, []) if run is not None else [],
            )
        )
    return bundles, int(total or 0)


def _build_citations(
    message_id: UUID,
    candidates: list[dict[str, Any]],
    cited_ranks: set[int],
) -> list[Citation]:
    rows: list[Citation] = []
    for rank, candidate in enumerate(candidates, start=1):
        score = candidate.get("rerank_score")
        if score is None:
            score = candidate.get("fused_score")
        rows.append(
            Citation(
                message_id=message_id,
                chunk_id=_as_uuid(candidate.get("chunk_id")),
                document_id=_as_uuid(candidate.get("document_id")),
                filename=candidate.get("filename") or "",
                chunk_index=int(candidate.get("chunk_index") or 0),
                heading_path=candidate.get("heading_path"),
                page_number=(
                    int(candidate["page_number"])
                    if candidate.get("page_number") is not None
                    else None
                ),
                rank=rank,
                score=float(score) if score is not None else None,
                snippet=(candidate.get("content") or "")[:600],
                cited=rank in cited_ranks,
            )
        )
    return rows


def _as_uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _save_assistant_message(
    db: AsyncSession,
    conversation: Conversation,
    *,
    question: str,
    content: str,
    outcome: RetrievalOutcome | None,
    status: str,
    latency_ms: int | None,
    usage: dict[str, Any] | None,
    agent: AgentPersistence | None = None,
) -> MessageBundle:
    message = Message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=content,
        status=status,
        latency_ms=latency_ms,
        token_usage=usage,
        rewrite_query=(outcome.rewritten_query if outcome is not None else None),
    )
    db.add(message)
    await db.flush()

    citations: list[Citation] = []
    trace_row: RetrievalTrace | None = None
    if outcome is not None:
        cited_ranks = extract_citation_ranks(content, len(outcome.candidates))
        message.citation_stats = citation_stats(content, len(outcome.candidates), cited_ranks)
        citations = _build_citations(message.id, outcome.candidates, cited_ranks)
        if citations:
            db.add_all(citations)
        trace = outcome.trace
        trace_row = RetrievalTrace(
            message_id=message.id,
            kb_id=conversation.kb_id,
            query=trace["query"],
            rewritten_query=trace["rewritten_query"],
            rewrite_applied=bool(trace.get("rewrite_applied")),
            sub_queries=list(trace.get("sub_queries") or []),
            cache_hit=bool(trace.get("cache_hit")),
            reranked=bool(trace.get("reranked")),
            rerank_error=trace.get("rerank_error"),
            config=trace.get("config") or {},
            candidates=trace.get("candidates") or {},
            stage_timings=trace.get("stage_timings") or {},
        )
        db.add(trace_row)

    agent_run: AgentRun | None = None
    agent_steps: list[AgentStep] = []
    if agent is not None:
        agent_run = AgentRun(
            message_id=message.id,
            kb_id=conversation.kb_id,
            question=question,
            status=status,
            stopped_reason=agent.stopped_reason,
            step_count=agent.step_count,
            tool_calls=agent.tool_calls,
            latency_ms=agent.latency_ms,
            token_usage=agent.token_usage,
            error=agent.error,
        )
        db.add(agent_run)
        await db.flush()
        for step in agent.steps:
            row = AgentStep(run_id=agent_run.id, **step)
            db.add(row)
            agent_steps.append(row)
        await db.flush()

    conversation.last_message_at = datetime.now(UTC)
    if conversation.title in {"", DEFAULT_TITLE}:
        conversation.title = question[:60]
    await db.flush()
    return MessageBundle(
        message=message,
        citations=citations,
        trace=trace_row,
        agent_run=agent_run,
        agent_steps=agent_steps,
    )


async def answer_once(
    db: AsyncSession,
    kb: KnowledgeBase,
    user_id: UUID,
    question: str,
    conversation_id: UUID | None,
) -> tuple[Conversation, MessageBundle]:
    started = time.perf_counter()
    # Fail before persisting a user turn when no API key is configured.
    get_chat_model()

    conversation = await ensure_conversation(db, kb.id, user_id, conversation_id, question)
    history = await get_history(db, conversation.id, get_settings().history_messages)
    db.add(
        Message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content=question,
            status=MessageStatus.COMPLETED,
        )
    )
    await db.flush()

    outcome = await retrieve(db, kb, question, history=history)
    usage: dict[str, Any] | None = None
    if outcome.candidates:
        answer, usage = await complete_answer(
            question,
            outcome.candidates,
            history,
            max_context_chars=get_settings().max_context_chars,
        )
        answer = answer or REFUSAL_MESSAGE
    else:
        answer = REFUSAL_MESSAGE

    latency_ms = int((time.perf_counter() - started) * 1000)
    bundle = await _save_assistant_message(
        db,
        conversation,
        question=question,
        content=answer,
        outcome=outcome,
        status=MessageStatus.COMPLETED,
        latency_ms=latency_ms,
        usage=usage,
    )
    await db.flush()
    return conversation, bundle


@dataclass
class StreamPreparation:
    conversation_id: UUID
    user_message_id: UUID
    outcome: RetrievalOutcome
    history: list[tuple[str, str]]


async def prepare_stream(
    kb_id: UUID,
    user_id: UUID,
    question: str,
    conversation_id: UUID | None,
) -> StreamPreparation:
    """Run retrieval and persist the user turn before the SSE stream starts.

    Opens its own session and closes it before returning, so no database
    connection is held while tokens are streamed to the client.
    """
    from app.services.kb_service import get_owned_kb

    # Resolve the cached model before writing anything: missing API key is a
    # request-level 503, not a half-written conversation.
    get_chat_model()
    async with SessionLocal() as db:
        kb = await get_owned_kb(db, kb_id, user_id)
        conversation = await ensure_conversation(db, kb.id, user_id, conversation_id, question)
        history = await get_history(db, conversation.id, get_settings().history_messages)
        user_message = Message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content=question,
            status=MessageStatus.COMPLETED,
        )
        db.add(user_message)
        await db.flush()

        outcome = await retrieve(db, kb, question, history=history)
        conversation.last_message_at = datetime.now(UTC)
        await db.commit()
        return StreamPreparation(
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            outcome=outcome,
            history=history,
        )


async def save_streamed_answer(
    conversation_id: UUID,
    *,
    question: str,
    content: str,
    outcome: RetrievalOutcome,
    status: str,
    latency_ms: int,
    usage: dict[str, Any] | None,
) -> MessageBundle | None:
    """Persist the streamed answer in a fresh session (request session is gone)."""
    async with SessionLocal() as db:
        conversation = await db.get(Conversation, conversation_id)
        if conversation is None:
            return None
        bundle = await _save_assistant_message(
            db,
            conversation,
            question=question,
            content=content,
            outcome=outcome,
            status=status,
            latency_ms=latency_ms,
            usage=usage,
        )
        await db.commit()
        return bundle


async def save_agent_answer_in_session(
    db: AsyncSession,
    conversation: Conversation,
    *,
    question: str,
    content: str,
    outcome: RetrievalOutcome | None,
    agent: AgentPersistence,
    status: str,
    latency_ms: int,
    usage: dict[str, Any] | None,
) -> MessageBundle:
    """Write an agent answer using the caller's transaction."""
    return await _save_assistant_message(
        db,
        conversation,
        question=question,
        content=content,
        outcome=outcome,
        status=status,
        latency_ms=latency_ms,
        usage=usage,
        agent=agent,
    )


async def save_answer_with_agent(
    conversation_id: UUID,
    *,
    question: str,
    content: str,
    outcome: RetrievalOutcome | None,
    agent: AgentPersistence,
    status: str,
    latency_ms: int,
    usage: dict[str, Any] | None,
) -> MessageBundle | None:
    """Persist a streamed agent answer in a fresh session."""
    async with SessionLocal() as db:
        conversation = await db.get(Conversation, conversation_id)
        if conversation is None:
            return None
        bundle = await _save_assistant_message(
            db,
            conversation,
            question=question,
            content=content,
            outcome=outcome,
            status=status,
            latency_ms=latency_ms,
            usage=usage,
            agent=agent,
        )
        await db.commit()
        return bundle
