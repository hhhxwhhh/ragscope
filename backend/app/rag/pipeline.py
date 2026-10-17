"""Hybrid retrieval pipeline with per-stage observability.

query rewrite -> embed -> vector top-k + BM25 top-k -> RRF -> rerank -> final top-k

Every stage records candidates, scores and wall-clock timings. Chat persists the
trace; the debug endpoint returns it directly. Retrieval never raises for a
degraded reranker: it falls back to the fused order and marks the trace.
"""

import logging
import time
from dataclasses import asdict, dataclass, field
from functools import partial
from typing import Any

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.knowledge_base import KnowledgeBase
from app.rag import bm25_index, vector_store
from app.rag.embedder import embed_query_async
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.query_rewriter import rewrite_query
from app.rag.reranker import rerank

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalConfig:
    vector_top_k: int = 20
    bm25_top_k: int = 20
    fusion_top_k: int = 20
    final_top_k: int = 5
    rrf_k: int = 60
    vector_weight: float = 1.0
    bm25_weight: float = 1.0
    min_vector_score: float = 0.35
    use_rerank: bool = True
    rerank_timeout_seconds: float = 3.0

    @classmethod
    def from_kb(cls, kb: KnowledgeBase) -> "RetrievalConfig":
        settings = get_settings()
        raw = kb.retrieval_config or {}
        return cls(
            vector_top_k=_clamp_int(raw, "vector_top_k", settings.vector_top_k, 1, 50),
            bm25_top_k=_clamp_int(raw, "bm25_top_k", settings.bm25_top_k, 1, 50),
            fusion_top_k=_clamp_int(raw, "fusion_top_k", settings.fusion_top_k, 1, 50),
            final_top_k=_clamp_int(raw, "final_top_k", settings.final_top_k, 1, 20),
            rrf_k=_clamp_int(raw, "rrf_k", settings.rrf_k, 1, 200),
            vector_weight=_clamp_float(raw, "vector_weight", 1.0, 0.0, 5.0),
            bm25_weight=_clamp_float(raw, "bm25_weight", 1.0, 0.0, 5.0),
            min_vector_score=_clamp_float(
                raw, "min_vector_score", settings.min_vector_score, -1.0, 1.0
            ),
            use_rerank=_clamp_bool(raw, "use_rerank", settings.rerank_enabled),
            rerank_timeout_seconds=_clamp_float(
                raw,
                "rerank_timeout_seconds",
                settings.rerank_timeout_seconds,
                0.5,
                30.0,
            ),
        )


@dataclass
class RetrievalOutcome:
    query: str
    rewritten_query: str
    candidates: list[dict[str, Any]]
    trace: dict[str, Any] = field(default_factory=dict)


def _clamp_int(raw: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(raw.get(key, default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, low), high)


def _clamp_float(raw: dict[str, Any], key: str, default: float, low: float, high: float) -> float:
    try:
        value = float(raw.get(key, default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, low), high)


def _clamp_bool(raw: dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _trace_candidate(candidate: dict[str, Any], content_chars: int = 400) -> dict[str, Any]:
    keys = (
        "chunk_id",
        "document_id",
        "filename",
        "chunk_index",
        "heading_path",
        "vector_score",
        "bm25_score",
        "vector_rank",
        "bm25_rank",
        "fused_score",
        "rerank_score",
    )
    payload = {key: candidate.get(key) for key in keys}
    payload["content"] = (candidate.get("content") or "")[:content_chars]
    return payload


async def retrieve(
    db: AsyncSession,
    kb: KnowledgeBase,
    query: str,
    *,
    history: list[tuple[str, str]] | None = None,
    config: RetrievalConfig | None = None,
) -> RetrievalOutcome:
    total_started = time.perf_counter()
    history = history or []
    config = config or RetrievalConfig.from_kb(kb)
    timings: dict[str, float] = {}

    # 1. Rewrite the query using conversation history (multi-turn only).
    started = time.perf_counter()
    rewrite = await rewrite_query(query, history)
    rewritten = rewrite.query
    timings["rewrite_ms"] = _ms(started)

    # 2. Embed the rewritten query.
    started = time.perf_counter()
    embedding = await embed_query_async(rewritten)
    timings["embed_ms"] = _ms(started)

    # 3. Candidate generation: dense and lexical run in sequence over one DB session.
    started = time.perf_counter()
    vector_hits = await anyio.to_thread.run_sync(
        partial(
            vector_store.query,
            kb.id,
            embedding,
            config.vector_top_k,
            index_version=kb.index_version,
        )
    )
    # Dense retrieval always returns k neighbours, even for unrelated queries.
    # Drop weak matches so an off-topic question can reach the refusal path.
    vector_hits = [hit for hit in vector_hits if hit.score >= config.min_vector_score]
    timings["vector_ms"] = _ms(started)

    started = time.perf_counter()
    bm25_hits = await bm25_index.search(
        db,
        kb.id,
        kb.index_version,
        rewritten,
        config.bm25_top_k,
    )
    timings["bm25_ms"] = _ms(started)

    # 4. Merge candidates by chunk id, keeping both source scores for the trace.
    candidates: dict[str, dict[str, Any]] = {}
    for rank, hit in enumerate(vector_hits, start=1):
        candidates[hit.chunk_id] = {
            "chunk_id": hit.chunk_id,
            "document_id": hit.document_id,
            "filename": hit.filename,
            "chunk_index": hit.chunk_index,
            "heading_path": hit.heading_path,
            "content": hit.content,
            "vector_score": round(hit.score, 6),
            "vector_rank": rank,
        }
    for rank, hit in enumerate(bm25_hits, start=1):
        candidate = candidates.setdefault(
            hit.chunk.chunk_id,
            {
                "chunk_id": hit.chunk.chunk_id,
                "document_id": hit.chunk.document_id,
                "filename": hit.chunk.filename,
                "chunk_index": hit.chunk.chunk_index,
                "heading_path": hit.chunk.heading_path,
                "content": hit.chunk.content,
            },
        )
        candidate["bm25_score"] = round(hit.score, 6)
        candidate["bm25_rank"] = rank

    # 5. RRF fusion.
    started = time.perf_counter()
    fused = reciprocal_rank_fusion(
        [
            [hit.chunk_id for hit in vector_hits],
            [hit.chunk.chunk_id for hit in bm25_hits],
        ],
        k=config.rrf_k,
        weights=[config.vector_weight, config.bm25_weight],
    )
    for chunk_id, score in fused:
        candidates[chunk_id]["fused_score"] = round(score, 6)
    fused_candidates = [candidates[chunk_id] for chunk_id, _ in fused[: config.fusion_top_k]]
    timings["fusion_ms"] = _ms(started)

    # 6. Cross-encoder rerank over the fused list.
    started = time.perf_counter()
    reranked = False
    rerank_error: str | None = None
    final_candidates = fused_candidates[: config.final_top_k]
    if config.use_rerank and fused_candidates:
        result = await rerank(
            rewritten,
            [candidate["content"] for candidate in fused_candidates],
            timeout_seconds=config.rerank_timeout_seconds,
        )
        rerank_error = result.error
        if result.applied:
            reranked = True
            for candidate, score in zip(fused_candidates, result.scores, strict=False):
                candidate["rerank_score"] = round(score, 6)
            final_candidates = sorted(
                fused_candidates,
                key=lambda candidate: candidate["rerank_score"],
                reverse=True,
            )[: config.final_top_k]
    timings["rerank_ms"] = _ms(started)
    timings["total_ms"] = round((time.perf_counter() - total_started) * 1000, 1)

    trace = {
        "query": query,
        "rewritten_query": rewritten,
        "rewrite_applied": rewrite.applied,
        "rewrite_error": rewrite.error,
        "reranked": reranked,
        "rerank_error": rerank_error,
        "config": asdict(config),
        "stage_timings": timings,
        "counts": {
            "vector": len(vector_hits),
            "bm25": len(bm25_hits),
            "fused": len(fused),
            "final": len(final_candidates),
        },
        "candidates": {
            "vector": [_trace_candidate(candidates[hit.chunk_id]) for hit in vector_hits],
            "bm25": [_trace_candidate(candidates[hit.chunk.chunk_id]) for hit in bm25_hits],
            "fused": [_trace_candidate(candidate) for candidate in fused_candidates],
            "final": [_trace_candidate(candidate) for candidate in final_candidates],
        },
    }
    logger.info(
        "retrieval kb=%s vector=%d bm25=%d final=%d rerank=%s total=%.1fms",
        kb.id,
        len(vector_hits),
        len(bm25_hits),
        len(final_candidates),
        reranked,
        timings["total_ms"],
    )
    return RetrievalOutcome(
        query=query,
        rewritten_query=rewritten,
        candidates=final_candidates,
        trace=trace,
    )
