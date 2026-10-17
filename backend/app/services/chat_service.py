"""Conversation orchestration: history, retrieval, grounded generation, citations."""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from openai import AsyncOpenAI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.db.session import SessionLocal
from app.models.citation import Citation
from app.models.conversation import Conversation
from app.models.knowledge_base import KnowledgeBase
from app.models.message import Message, MessageRole, MessageStatus
from app.models.retrieval_trace import RetrievalTrace
from app.rag.llm import get_llm_client
from app.rag.pipeline import RetrievalOutcome, retrieve
from app.rag.prompt import build_llm_messages, extract_citation_ranks
from app.schemas.chat import CitationOut, MessageOut, RetrievalTraceOut

logger = logging.getLogger(__name__)

REFUSAL_MESSAGE = "根据现有资料无法回答这个问题。"
DEFAULT_TITLE = "新对话"


@dataclass
class MessageBundle:
    message: Message
    citations: list[Citation]
    trace: RetrievalTrace | None


def to_message_out(bundle: MessageBundle) -> MessageOut:
    out = MessageOut.model_validate(bundle.message)
    out.citations = [CitationOut.model_validate(citation) for citation in bundle.citations]
    out.trace = RetrievalTraceOut.model_validate(bundle.trace) if bundle.trace is not None else None
    return out


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
) -> tuple[list[Conversation], int]:
    condition = (Conversation.kb_id == kb_id) & (Conversation.user_id == user_id)
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

    citations_by_message: dict[UUID, list[Citation]] = {}
    for citation in citations:
        citations_by_message.setdefault(citation.message_id, []).append(citation)
    trace_by_message = {trace.message_id: trace for trace in traces}

    bundles = [
        MessageBundle(
            message=message,
            citations=citations_by_message.get(message.id, []),
            trace=trace_by_message.get(message.id),
        )
        for message in messages
    ]
    return bundles, int(total or 0)


def _build_citations(
    message_id: UUID,
    candidates: list[dict[str, Any]],
    answer: str,
) -> list[Citation]:
    cited_ranks = extract_citation_ranks(answer, len(candidates))
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
        citations = _build_citations(message.id, outcome.candidates, content)
        if citations:
            db.add_all(citations)
        trace = outcome.trace
        trace_row = RetrievalTrace(
            message_id=message.id,
            kb_id=conversation.kb_id,
            query=trace["query"],
            rewritten_query=trace["rewritten_query"],
            rewrite_applied=bool(trace.get("rewrite_applied")),
            reranked=bool(trace.get("reranked")),
            rerank_error=trace.get("rerank_error"),
            config=trace.get("config") or {},
            candidates=trace.get("candidates") or {},
            stage_timings=trace.get("stage_timings") or {},
        )
        db.add(trace_row)

    conversation.last_message_at = datetime.now(UTC)
    if conversation.title in {"", DEFAULT_TITLE}:
        conversation.title = question[:60]
    await db.flush()
    return MessageBundle(message=message, citations=citations, trace=trace_row)


def _llm_payload(messages: list[dict[str, str]], *, stream: bool) -> dict[str, Any]:
    settings = get_settings()
    payload: dict[str, Any] = {
        "model": settings.deepseek_model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": settings.max_answer_tokens,
        "stream": stream,
    }
    if stream:
        payload["stream_options"] = {"include_usage": True}
    return payload


async def answer_once(
    db: AsyncSession,
    kb: KnowledgeBase,
    user_id: UUID,
    question: str,
    conversation_id: UUID | None,
) -> tuple[Conversation, MessageBundle]:
    started = time.perf_counter()
    client = get_llm_client()

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
        completion = await client.chat.completions.create(
            **_llm_payload(
                build_llm_messages(
                    question,
                    outcome.candidates,
                    history,
                    max_context_chars=get_settings().max_context_chars,
                ),
                stream=False,
            )
        )
        answer = completion.choices[0].message.content or REFUSAL_MESSAGE
        if completion.usage is not None:
            usage = completion.usage.model_dump()
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
    llm_messages: list[dict[str, str]]
    client: AsyncOpenAI


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

    client = get_llm_client()
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
        llm_messages = build_llm_messages(
            question,
            outcome.candidates,
            history,
            max_context_chars=get_settings().max_context_chars,
        )
        conversation.last_message_at = datetime.now(UTC)
        await db.commit()
        return StreamPreparation(
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            outcome=outcome,
            llm_messages=llm_messages,
            client=client,
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


def llm_stream_payload(messages: list[dict[str, str]]) -> dict[str, Any]:
    return _llm_payload(messages, stream=True)
