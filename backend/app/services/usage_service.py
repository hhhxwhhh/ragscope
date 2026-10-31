"""Token usage accounting and the daily budget guard.

Two questions this answers:

1. *Where did the tokens go?* — a daily series plus a per-knowledge-base
   breakdown, aggregated from the ``messages.token_usage`` JSONB that every
   answer already writes.
2. *Should this request be allowed to spend more?* — a configurable daily budget
   per knowledge base. Zero means unlimited (the default for local development).

Why aggregate on read instead of maintaining a counter table: the source of truth
is already stored per message, and a counter can drift after a crash or a manual
delete. At this data volume the group-by is milliseconds; if it ever isn't, the
fix is a materialised daily rollup, not a cache of an unverified number.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import QuotaExceededError
from app.models.conversation import Conversation
from app.models.knowledge_base import KnowledgeBase
from app.models.message import Message, MessageRole

TOTAL_TOKENS = Message.token_usage.op("->>")("total_tokens")


def _tokens_expr():
    # ``token_usage`` is nullable and may miss the key; coalesce both levels.
    return func.coalesce(cast(TOTAL_TOKENS, Integer), 0)


def budget_state(used: int, budget: int) -> dict[str, Any]:
    """Pure helper so the arithmetic (and its edges) is testable."""
    if budget <= 0:
        return {"used": used, "budget": 0, "remaining": None, "exceeded": False}
    remaining = max(0, budget - used)
    return {
        "used": used,
        "budget": budget,
        "remaining": remaining,
        "exceeded": used >= budget,
    }


async def tokens_since(db: AsyncSession, kb_id: UUID, since: datetime) -> int:
    value = await db.scalar(
        select(func.coalesce(func.sum(_tokens_expr()), 0))
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.kb_id == kb_id,
            Message.role == MessageRole.ASSISTANT,
            Message.created_at >= since,
        )
    )
    return int(value or 0)


async def today_tokens(db: AsyncSession, kb_id: UUID) -> int:
    now = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return await tokens_since(db, kb_id, start)


async def enforce_budget(db: AsyncSession, kb_id: UUID) -> dict[str, Any]:
    """Raise before generation when today's budget is exhausted.

    Deliberately checked *after* the semantic cache lookup (the caller decides
    the order): a cached answer costs no tokens and should stay available even
    when the budget is gone.
    """
    budget = get_settings().daily_token_budget
    used = await today_tokens(db, kb_id)
    state = budget_state(used, budget)
    if state["exceeded"]:
        raise QuotaExceededError(
            f"该知识库今日 token 预算已用尽（{used}/{budget}），"
            "可在 .env 调整 DAILY_TOKEN_BUDGET 或明天再试"
        )
    return state


async def summary(db: AsyncSession, owner_id: UUID, days: int = 14) -> dict[str, Any]:
    """Daily series, per-KB breakdown and today's budget state for one owner."""
    days = max(1, min(days, 90))
    now = datetime.now(UTC)
    since = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    day = func.date_trunc("day", Message.created_at).label("day")
    daily_rows = (
        await db.execute(
            select(day, func.coalesce(func.sum(_tokens_expr()), 0).label("tokens"))
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .join(KnowledgeBase, KnowledgeBase.id == Conversation.kb_id)
            .where(
                KnowledgeBase.owner_id == owner_id,
                Message.role == MessageRole.ASSISTANT,
                Message.created_at >= since,
            )
            .group_by(day)
            .order_by(day)
        )
    ).all()

    kb_rows = (
        await db.execute(
            select(
                KnowledgeBase.id,
                KnowledgeBase.name,
                func.coalesce(func.sum(_tokens_expr()), 0).label("tokens"),
            )
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .join(KnowledgeBase, KnowledgeBase.id == Conversation.kb_id)
            .where(KnowledgeBase.owner_id == owner_id, Message.role == MessageRole.ASSISTANT)
            .group_by(KnowledgeBase.id, KnowledgeBase.name)
            .order_by(func.coalesce(func.sum(_tokens_expr()), 0).desc())
            .limit(10)
        )
    ).all()

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_used = int(
        await db.scalar(
            select(func.coalesce(func.sum(_tokens_expr()), 0))
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .join(KnowledgeBase, KnowledgeBase.id == Conversation.kb_id)
            .where(
                KnowledgeBase.owner_id == owner_id,
                Message.role == MessageRole.ASSISTANT,
                Message.created_at >= today_start,
            )
        )
        or 0
    )

    daily_map = {row.day.date().isoformat(): int(row.tokens) for row in daily_rows}
    series = []
    for offset in range(days):
        date = (since + timedelta(days=offset)).date().isoformat()
        series.append({"date": date, "tokens": daily_map.get(date, 0)})

    return {
        "days": days,
        "daily": series,
        "by_kb": [
            {"id": str(row.id), "name": row.name, "tokens": int(row.tokens)} for row in kb_rows
        ],
        "today": budget_state(today_used, get_settings().daily_token_budget),
    }
