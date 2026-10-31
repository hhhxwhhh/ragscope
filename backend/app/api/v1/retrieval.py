from dataclasses import replace
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.rag.pipeline import RetrievalConfig, retrieve
from app.schemas.retrieval import DebugRequest, DebugResponse
from app.services import kb_service

router = APIRouter(tags=["retrieval"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("/kbs/{kb_id}/retrieval/debug", response_model=DebugResponse)
async def debug_retrieval(
    kb_id: UUID,
    payload: DebugRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> DebugResponse:
    kb = await kb_service.get_owned_kb(db, kb_id, current_user.id)

    config = RetrievalConfig.from_kb(kb)
    if payload.config is not None:
        config = replace(config, **payload.config.model_dump(exclude_none=True))

    outcome = await retrieve(db, kb, payload.query, config=config)
    trace = outcome.trace
    return DebugResponse(
        query=trace["query"],
        rewritten_query=trace["rewritten_query"],
        rewrite_applied=trace["rewrite_applied"],
        rewrite_error=trace["rewrite_error"],
        sub_queries=trace.get("sub_queries") or [],
        multi_query_applied=bool(trace.get("multi_query_applied")),
        multi_query_error=trace.get("multi_query_error"),
        cache_hit=bool(trace.get("cache_hit")),
        reranked=trace["reranked"],
        rerank_error=trace["rerank_error"],
        config=trace["config"],
        stage_timings=trace["stage_timings"],
        counts=trace["counts"],
        candidates=trace["candidates"],
        graph=trace.get("graph") or {},
        crag=trace.get("crag") or {},
        compress=trace.get("compress") or {},
    )
