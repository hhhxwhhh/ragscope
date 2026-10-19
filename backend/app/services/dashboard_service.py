"""Read-only aggregates for the dashboard.

All queries are scoped to the current user through the owning knowledge base,
so one account never sees another account's counts.
"""

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import Date, Float, Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AgentRun
from app.models.chunk import Chunk
from app.models.conversation import Conversation
from app.models.document import Document, DocumentStatus
from app.models.evaluation import EvalCase, EvalDataset, EvalRun
from app.models.feedback import MessageFeedback
from app.models.knowledge_base import KnowledgeBase
from app.models.message import Message, MessageRole, MessageStatus
from app.schemas.dashboard import DashboardSummary, DashboardTrend, RecentRunOut, TrendPoint


def _kb_ids(user_id: UUID):
    return select(KnowledgeBase.id).where(KnowledgeBase.owner_id == user_id)


async def summary(db: AsyncSession, user_id: UUID) -> DashboardSummary:
    kb_ids = _kb_ids(user_id)

    kb_count = await db.scalar(
        select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.owner_id == user_id)
    )
    document_count = await db.scalar(
        select(func.count()).select_from(Document).where(Document.kb_id.in_(kb_ids))
    )
    indexed_document_count = await db.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.kb_id.in_(kb_ids), Document.status == DocumentStatus.INDEXED)
    )
    chunk_count = await db.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.kb_id.in_(kb_ids))
    )
    conversation_count = await db.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id)
    )
    message_count = await db.scalar(
        select(func.count())
        .select_from(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == user_id)
    )
    agent_row = (
        await db.execute(
            select(
                func.count(AgentRun.id),
                func.count(AgentRun.id).filter(AgentRun.status == "completed"),
                func.avg(AgentRun.step_count),
                func.avg(AgentRun.tool_calls),
                func.avg(AgentRun.latency_ms),
                func.coalesce(
                    func.sum(cast(AgentRun.token_usage["total_tokens"].astext, Integer)),
                    0,
                ),
            ).where(AgentRun.kb_id.in_(kb_ids))
        )
    ).one()
    agent_run_count = int(agent_row[0] or 0)
    agent_completed_count = int(agent_row[1] or 0)

    eval_dataset_count = await db.scalar(
        select(func.count()).select_from(EvalDataset).where(EvalDataset.kb_id.in_(kb_ids))
    )
    eval_case_count = await db.scalar(
        select(func.count())
        .select_from(EvalCase)
        .join(EvalDataset, EvalCase.dataset_id == EvalDataset.id)
        .where(EvalDataset.kb_id.in_(kb_ids))
    )
    eval_run_count = await db.scalar(
        select(func.count()).select_from(EvalRun).where(EvalRun.kb_id.in_(kb_ids))
    )
    avg_latency = await db.scalar(
        select(func.avg(Message.latency_ms))
        .select_from(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.user_id == user_id,
            Message.role == MessageRole.ASSISTANT,
            Message.status == MessageStatus.COMPLETED,
            Message.latency_ms.is_not(None),
        )
    )

    feedback_positive = await db.scalar(
        select(func.count())
        .select_from(MessageFeedback)
        .where(MessageFeedback.user_id == user_id, MessageFeedback.rating == 1)
    )
    feedback_negative = await db.scalar(
        select(func.count())
        .select_from(MessageFeedback)
        .where(MessageFeedback.user_id == user_id, MessageFeedback.rating == -1)
    )
    feedback_positive = int(feedback_positive or 0)
    feedback_negative = int(feedback_negative or 0)
    feedback_total = feedback_positive + feedback_negative

    citation_rate_expr = Message.citation_stats["citation_rate"].astext.cast(Float)
    invalid_marker_expr = Message.citation_stats["invalid_markers"].astext.cast(Integer)
    avg_citation_rate = await db.scalar(
        select(func.avg(citation_rate_expr))
        .select_from(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.user_id == user_id,
            Message.citation_stats.is_not(None),
        )
    )
    invalid_citation_markers = await db.scalar(
        select(func.coalesce(func.sum(invalid_marker_expr), 0))
        .select_from(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.user_id == user_id,
            Message.citation_stats.is_not(None),
        )
    )

    recent_rows = (
        await db.execute(
            select(EvalRun, EvalDataset.name)
            .join(EvalDataset, EvalRun.dataset_id == EvalDataset.id)
            .where(EvalRun.kb_id.in_(kb_ids))
            .order_by(EvalRun.created_at.desc())
            .limit(5)
        )
    ).all()
    recent_runs = [
        RecentRunOut(
            id=run.id,
            dataset_id=run.dataset_id,
            dataset_name=name,
            status=run.status,
            recall_5=run.metrics.get("recall@5"),
            mrr=run.metrics.get("mrr"),
            cases_total=int(run.metrics.get("cases_total", 0)),
            created_at=run.created_at,
        )
        for run, name in recent_rows
    ]

    return DashboardSummary(
        kb_count=int(kb_count or 0),
        document_count=int(document_count or 0),
        indexed_document_count=int(indexed_document_count or 0),
        chunk_count=int(chunk_count or 0),
        conversation_count=int(conversation_count or 0),
        message_count=int(message_count or 0),
        agent_run_count=agent_run_count,
        agent_completed_count=agent_completed_count,
        agent_success_rate=(
            round(agent_completed_count / agent_run_count, 4) if agent_run_count else 0.0
        ),
        agent_avg_steps=round(float(agent_row[2] or 0), 2),
        agent_avg_tool_calls=round(float(agent_row[3] or 0), 2),
        agent_avg_latency_ms=round(float(agent_row[4] or 0), 1),
        agent_total_tokens=int(agent_row[5] or 0),
        eval_dataset_count=int(eval_dataset_count or 0),
        eval_case_count=int(eval_case_count or 0),
        eval_run_count=int(eval_run_count or 0),
        avg_latency_ms=round(float(avg_latency or 0), 1),
        feedback_positive=feedback_positive,
        feedback_negative=feedback_negative,
        feedback_positive_rate=(
            round(feedback_positive / feedback_total, 4) if feedback_total else 0.0
        ),
        avg_citation_rate=round(float(avg_citation_rate or 0), 4),
        invalid_citation_markers=int(invalid_citation_markers or 0),
        recent_runs=recent_runs,
    )


async def trend(db: AsyncSession, user_id: UUID, days: int) -> DashboardTrend:
    days = max(1, min(days, 90))
    today = datetime.now(UTC).date()
    start_day = today - timedelta(days=days - 1)
    start_at = datetime.combine(start_day, time.min, tzinfo=UTC)
    kb_ids = _kb_ids(user_id)

    message_day = cast(Message.created_at, Date)
    message_rows = (
        await db.execute(
            select(message_day, func.count())
            .select_from(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.user_id == user_id, Message.created_at >= start_at)
            .group_by(message_day)
        )
    ).all()
    conversation_day = cast(Conversation.created_at, Date)
    conversation_rows = (
        await db.execute(
            select(conversation_day, func.count())
            .where(Conversation.user_id == user_id, Conversation.created_at >= start_at)
            .group_by(conversation_day)
        )
    ).all()
    run_day = cast(EvalRun.created_at, Date)
    run_rows = (
        await db.execute(
            select(run_day, func.count())
            .where(EvalRun.kb_id.in_(kb_ids), EvalRun.created_at >= start_at)
            .group_by(run_day)
        )
    ).all()

    def to_map(rows) -> dict[date, int]:
        result: dict[date, int] = {}
        for value, count in rows:
            if value is None:
                continue
            key = value.date() if isinstance(value, datetime) else value
            result[key] = int(count)
        return result

    messages = to_map(message_rows)
    conversations = to_map(conversation_rows)
    runs = to_map(run_rows)

    points = []
    for offset in range(days):
        current = start_day + timedelta(days=offset)
        points.append(
            TrendPoint(
                date=current.isoformat(),
                messages=messages.get(current, 0),
                conversations=conversations.get(current, 0),
                eval_runs=runs.get(current, 0),
            )
        )
    return DashboardTrend(days=days, points=points)
