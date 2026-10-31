"""Read-only access to stored retrieval traces.

The chat view already shows the trace of the message you just sent; this API is
for the other direction — open any past retrieval and inspect it. That is what
makes the observability claim checkable rather than anecdotal: you can pull up a
slow or a refused answer from yesterday and see which stage produced it.

Only read endpoints exist. A trace is a record of what happened, never something
a client may edit.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.retrieval_trace import RetrievalTrace
from app.models.user import User
from app.schemas.common import Page
from app.schemas.trace import TraceDetailOut, TraceSummaryOut
from app.services import kb_service

router = APIRouter(tags=["traces"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def _stage_order() -> list[str]:
    """Fixed display order; stages are recorded as a dict, which is unordered."""
    return [
        "rewrite_ms",
        "cache_ms",
        "multi_query_ms",
        "embed_ms",
        "vector_ms",
        "bm25_ms",
        "fusion_ms",
        "rerank_ms",
        "crag_ms",
        "graph_ms",
        "compress_ms",
    ]


def _summarise(row: RetrievalTrace) -> TraceSummaryOut:
    timings = row.stage_timings or {}
    counts = row.counts or {}
    return TraceSummaryOut(
        id=row.id,
        message_id=row.message_id,
        query=row.query,
        rewritten_query=row.rewritten_query,
        rewrite_applied=row.rewrite_applied,
        cache_hit=row.cache_hit,
        reranked=row.reranked,
        created_at=row.created_at,
        total_ms=float(timings.get("total_ms") or 0.0),
        final_count=int(counts.get("final") or 0),
    )


@router.get("/kbs/{kb_id}/traces", response_model=Page[TraceSummaryOut])
async def list_traces(
    kb_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[TraceSummaryOut]:
    await kb_service.get_owned_kb(db, kb_id, current_user.id)

    rows = (
        await db.scalars(
            select(RetrievalTrace)
            .where(RetrievalTrace.kb_id == kb_id)
            .order_by(RetrievalTrace.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    # The list is a paging cursor, not a reporting query: a capped COUNT keeps
    # the response cheap on knowledge bases with a long history.
    total = min(offset + len(rows) + (1 if len(rows) == limit else 0), 10_000)
    return Page(
        items=[_summarise(row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/traces/{trace_id}", response_model=TraceDetailOut)
async def get_trace(
    trace_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> TraceDetailOut:
    row = await db.get(RetrievalTrace, trace_id)
    if row is None:
        from app.core.errors import NotFoundError

        raise NotFoundError("检索记录不存在")
    # Ownership is checked through the knowledge base, not the trace: the trace
    # id alone must not reveal whether another tenant's record exists.
    await kb_service.get_owned_kb(db, row.kb_id, current_user.id)

    timings = dict(row.stage_timings or {})
    ordered = {
        stage: float(timings.get(stage) or 0.0)
        for stage in _stage_order()
        if stage in timings
    }
    ordered.update(
        {key: float(value) for key, value in timings.items() if key not in ordered}
    )
    return TraceDetailOut(
        id=row.id,
        kb_id=row.kb_id,
        message_id=row.message_id,
        query=row.query,
        rewritten_query=row.rewritten_query,
        rewrite_applied=row.rewrite_applied,
        sub_queries=list(row.sub_queries or []),
        cache_hit=row.cache_hit,
        reranked=row.reranked,
        rerank_error=row.rerank_error,
        config=dict(row.config or {}),
        candidates=dict(row.candidates or {}),
        stage_timings=ordered,
        graph=row.graph,
        crag=row.crag,
        compress=row.compress,
        counts=row.counts,
        created_at=row.created_at,
    )
