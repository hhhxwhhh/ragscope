"""Machine-facing API: search and ask over an existing knowledge base.

This is the surface an integration uses — a Slack bot, an internal copilot, an
MCP client, a nightly job. It is deliberately **stateless**: no conversation is
created, no message is persisted, and the caller passes whatever context it
wants. Human-facing chat lives in ``/chat`` and keeps history; mixing the two
would make an integration inherit conversation semantics it never asked for.

Every endpoint here requires an API key with the matching scope, and is rate
limited per key so a broken client cannot exhaust the model budget.
"""

import time
from dataclasses import replace
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.config import get_settings
from app.core.deps import Principal, require_scope
from app.core.ratelimit import enforce as enforce_rate_limit
from app.db.session import get_db
from app.models.document import Document, DocumentStatus
from app.rag.answer import complete_answer
from app.rag.pipeline import RetrievalConfig, retrieve
from app.rag.prompt import REFUSAL_MESSAGE, extract_citation_ranks
from app.schemas.api_key import (
    PublicAskRequest,
    PublicAskResponse,
    PublicChunk,
    PublicSearchRequest,
    PublicSearchResponse,
)
from app.schemas.kb import KbOut
from app.services import kb_service

router = APIRouter(prefix="/public", tags=["public-api"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
ReadPrincipal = Annotated[Principal, Depends(require_scope("kb:read"))]
ChatPrincipal = Annotated[Principal, Depends(require_scope("chat"))]


async def _guard(principal: Principal) -> None:
    settings = get_settings()
    identity = principal.api_key_id or principal.user_id
    await enforce_rate_limit(
        f"public:{identity}",
        settings.public_rate_limit_per_minute,
        60,
    )


def _candidate_to_chunk(candidate: dict[str, Any]) -> PublicChunk:
    score = candidate.get("rerank_score")
    if score is None:
        score = candidate.get("fused_score")
    if score is None:
        score = candidate.get("vector_score")
    return PublicChunk(
        chunk_id=str(candidate.get("chunk_id")),
        document_id=str(candidate.get("document_id")),
        filename=candidate.get("filename") or "",
        chunk_index=int(candidate.get("chunk_index") or 0),
        heading_path=candidate.get("heading_path"),
        page_number=candidate.get("page_number"),
        content=candidate.get("content") or "",
        score=score,
        rerank_score=candidate.get("rerank_score"),
    )


def _trace_summary(trace: dict[str, Any]) -> dict[str, Any]:
    """The trace is large; integrations get the parts that explain a result."""
    return {
        "rewritten_query": trace.get("rewritten_query"),
        "cache_hit": trace.get("cache_hit"),
        "reranked": trace.get("reranked"),
        "counts": trace.get("counts"),
        "stage_timings": trace.get("stage_timings"),
        "config": trace.get("config"),
    }


@router.get("/kbs", response_model=list[KbOut])
async def list_kbs(
    db: DbSession,
    principal: ReadPrincipal,
    request: Request,
) -> list[KbOut]:
    await _guard(principal)
    items, _ = await kb_service.list_kbs(db, principal.user_id, 0, 100)
    return [KbOut.model_validate(item) for item in items]


@router.get("/kbs/{kb_id}/documents")
async def list_documents(
    kb_id: UUID,
    db: DbSession,
    principal: ReadPrincipal,
    request: Request,
) -> list[dict[str, Any]]:
    await _guard(principal)
    kb = await kb_service.get_owned_kb(db, kb_id, principal.user_id)
    rows = await db.scalars(
        select(Document)
        .where(Document.kb_id == kb.id)
        .order_by(Document.created_at.desc())
        .limit(200)
    )
    return [
        {
            "id": str(document.id),
            "filename": document.filename,
            "status": document.status.value
            if isinstance(document.status, DocumentStatus)
            else str(document.status),
            "chunk_count": document.chunk_count,
            "page_count": document.page_count,
            "created_at": document.created_at.isoformat() if document.created_at else None,
        }
        for document in rows
    ]


@router.post("/kbs/{kb_id}/search", response_model=PublicSearchResponse)
async def search(
    kb_id: UUID,
    payload: PublicSearchRequest,
    db: DbSession,
    principal: ReadPrincipal,
    request: Request,
) -> PublicSearchResponse:
    await _guard(principal)
    kb = await kb_service.get_owned_kb(db, kb_id, principal.user_id)

    config = RetrievalConfig.from_kb(kb)
    overrides: dict[str, Any] = {}
    if payload.top_k is not None:
        overrides["final_top_k"] = payload.top_k
        overrides["fusion_top_k"] = max(payload.top_k * 3, 20)
    if payload.use_rerank is not None:
        overrides["use_rerank"] = payload.use_rerank
    if payload.use_multi_query is not None:
        overrides["use_multi_query"] = payload.use_multi_query
    if overrides:
        config = replace(config, **overrides)

    started = time.perf_counter()
    outcome = await retrieve(db, kb, payload.query, config=config)
    latency_ms = round((time.perf_counter() - started) * 1000, 1)

    return PublicSearchResponse(
        query=payload.query,
        rewritten_query=outcome.rewritten_query,
        chunks=[_candidate_to_chunk(item) for item in outcome.candidates],
        trace=_trace_summary(outcome.trace),
        latency_ms=latency_ms,
    )


@router.post("/kbs/{kb_id}/ask", response_model=PublicAskResponse)
async def ask(
    kb_id: UUID,
    payload: PublicAskRequest,
    db: DbSession,
    principal: ChatPrincipal,
    request: Request,
) -> PublicAskResponse:
    """Stateless grounded answer.

    Retrieval runs first; if nothing survives the thresholds the endpoint refuses
    without calling the model at all. That keeps the "no evidence, no answer"
    contract intact and keeps a refusal free.
    """
    await _guard(principal)
    settings = get_settings()
    kb = await kb_service.get_owned_kb(db, kb_id, principal.user_id)

    started = time.perf_counter()
    outcome = await retrieve(db, kb, payload.question)

    if not outcome.candidates:
        return PublicAskResponse(
            answer=REFUSAL_MESSAGE,
            citations=[],
            refusal=True,
            usage=None,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            trace=_trace_summary(outcome.trace),
        )

    answer, usage = await complete_answer(
        payload.question,
        outcome.candidates,
        [],
        max_context_chars=payload.max_context_chars or settings.max_context_chars,
        prompt_variant=payload.prompt_variant,
    )
    metrics.observe_llm_usage(usage)

    cited = extract_citation_ranks(answer, len(outcome.candidates))
    valid_ranks = sorted(rank for rank in cited if 0 < rank <= len(outcome.candidates))
    selected = (
        [outcome.candidates[rank - 1] for rank in valid_ranks] if cited else outcome.candidates
    )

    return PublicAskResponse(
        answer=answer or REFUSAL_MESSAGE,
        citations=[_candidate_to_chunk(item) for item in selected],
        refusal=not bool(answer),
        usage=usage,
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
        trace=_trace_summary(outcome.trace),
    )
