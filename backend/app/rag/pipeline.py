"""Hybrid retrieval pipeline with per-stage observability.

query rewrite -> cache lookup -> optional multi-query expansion
-> embed -> vector top-k + BM25 top-k (per query) -> RRF -> rerank -> final top-k

Every stage records candidates, scores and wall-clock timings. Chat persists the
trace; the debug endpoint returns it directly. Retrieval never raises for a
degraded reranker or a cache/LLM outage: it falls back to the fused order and
marks the trace.
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
from app.rag import bm25_index, retrieval_cache, vector_store
from app.rag.embedder import embed_texts_async
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.multi_query import expand_queries
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
    min_vector_score: float = 0.45
    use_rerank: bool = True
    rerank_timeout_seconds: float = 3.0
    use_multi_query: bool = False
    multi_query_count: int = 3
    multi_query_timeout_seconds: float = 8.0
    use_cache: bool = True

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
            use_multi_query=_clamp_bool(
                raw, "use_multi_query", settings.multi_query_enabled
            ),
            multi_query_count=_clamp_int(
                raw, "multi_query_count", settings.multi_query_count, 1, 5
            ),
            multi_query_timeout_seconds=_clamp_float(
                raw,
                "multi_query_timeout_seconds",
                settings.multi_query_timeout_seconds,
                1.0,
                30.0,
            ),
            use_cache=_clamp_bool(raw, "use_cache", settings.retrieval_cache_enabled),
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
        "page_number",
        "vector_score",
        "bm25_score",
        "vector_rank",
        "bm25_rank",
        "fused_score",
        "rerank_score",
        "matched_queries",
    )
    payload = {key: candidate.get(key) for key in keys}
    payload["content"] = (candidate.get("content") or "")[:content_chars]
    return payload


def _merge_vector_hit(
    candidates: dict[str, dict[str, Any]],
    hit: vector_store.VectorHit,
    rank: int,
    query_index: int,
) -> None:
    candidate = candidates.get(hit.chunk_id)
    if candidate is None:
        candidates[hit.chunk_id] = {
            "chunk_id": hit.chunk_id,
            "document_id": hit.document_id,
            "filename": hit.filename,
            "chunk_index": hit.chunk_index,
            "heading_path": hit.heading_path,
            "page_number": hit.page_number,
            "content": hit.content,
            "vector_score": round(hit.score, 6),
            "vector_rank": rank,
            "matched_queries": [query_index],
        }
        return
    candidate["vector_score"] = max(candidate.get("vector_score") or -2.0, round(hit.score, 6))
    candidate["vector_rank"] = min(candidate.get("vector_rank") or rank, rank)
    if query_index not in candidate["matched_queries"]:
        candidate["matched_queries"].append(query_index)


def _merge_bm25_hit(
    candidates: dict[str, dict[str, Any]],
    hit: bm25_index.Bm25Hit,
    rank: int,
    query_index: int,
) -> None:
    chunk = hit.chunk
    candidate = candidates.get(chunk.chunk_id)
    if candidate is None:
        candidates[chunk.chunk_id] = {
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "filename": chunk.filename,
            "chunk_index": chunk.chunk_index,
            "heading_path": chunk.heading_path,
            "page_number": chunk.page_number,
            "content": chunk.content,
            "bm25_score": round(hit.score, 6),
            "bm25_rank": rank,
            "matched_queries": [query_index],
        }
        return
    candidate["bm25_score"] = max(candidate.get("bm25_score") or 0.0, round(hit.score, 6))
    candidate["bm25_rank"] = min(candidate.get("bm25_rank") or rank, rank)
    if query_index not in candidate["matched_queries"]:
        candidate["matched_queries"].append(query_index)


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
    timings: dict[str, float] = {
        "cache_ms": 0.0,
        "multi_query_ms": 0.0,
        "embed_ms": 0.0,
        "vector_ms": 0.0,
        "bm25_ms": 0.0,
        "fusion_ms": 0.0,
        "rerank_ms": 0.0,
    }

    # 1. Rewrite the query using conversation history (multi-turn only).
    started = time.perf_counter()
    rewrite = await rewrite_query(query, history)
    rewritten = rewrite.query
    timings["rewrite_ms"] = _ms(started)

    # 2. Cache lookup after rewrite: the key uses the standalone query that is
    # actually embedded, plus the history signature to avoid cross-context hits.
    cache_key: str | None = None
    if config.use_cache:
        started = time.perf_counter()
        cache_key = retrieval_cache.make_key(
            kb.id, kb.index_version, config, rewritten, history
        )
        cached = await retrieval_cache.get(cache_key)
        timings["cache_ms"] = _ms(started)
        if cached is not None:
            trace = dict(cached.get("trace") or {})
            trace["query"] = query
            trace["rewritten_query"] = rewritten
            trace["rewrite_applied"] = rewrite.applied
            trace["cache_hit"] = True
            trace["stage_timings"] = {
                "cache_ms": timings["cache_ms"],
                "total_ms": _ms(total_started),
            }
            candidates = list(cached.get("candidates") or [])
            logger.info(
                "retrieval cache hit kb=%s queries=%s final=%d total=%.1fms",
                kb.id,
                trace.get("counts", {}).get("queries", 1),
                len(candidates),
                _ms(total_started),
            )
            return RetrievalOutcome(
                query=query,
                rewritten_query=rewritten,
                candidates=candidates,
                trace=trace,
            )

    # 3. Optional multi-query expansion. One LLM call, fail-open.
    queries = [rewritten]
    multi_query_applied = False
    multi_query_error: str | None = None
    if config.use_multi_query:
        started = time.perf_counter()
        expansion = await expand_queries(
            rewritten,
            config.multi_query_count,
            config.multi_query_timeout_seconds,
        )
        queries.extend(expansion.queries)
        multi_query_applied = expansion.applied
        multi_query_error = expansion.error
        timings["multi_query_ms"] = _ms(started)

    # 4. Embed all queries in one batch; query instructions are added per text.
    started = time.perf_counter()
    embeddings = await embed_texts_async(queries, is_query=True)
    timings["embed_ms"] = _ms(started)

    # 5. Candidate generation per query. Dense and lexical run sequentially
    # because they share one DB session; BM25 itself is synchronous/in-memory
    # after the first (index_version-keyed) build.
    candidates: dict[str, dict[str, Any]] = {}
    rankings: list[list[str]] = []
    weights: list[float] = []
    vector_hits_total = 0
    bm25_hits_total = 0

    for query_index, (sub_query, embedding) in enumerate(
        zip(queries, embeddings, strict=True), start=1
    ):
        vector_started = time.perf_counter()
        vector_hits = await anyio.to_thread.run_sync(
            partial(
                vector_store.query,
                kb.id,
                embedding,
                config.vector_top_k,
                index_version=kb.index_version,
            )
        )
        # Dense retrieval always returns k neighbours, even for unrelated
        # queries. Drop weak matches so off-topic questions reach refusal.
        vector_hits = [hit for hit in vector_hits if hit.score >= config.min_vector_score]
        vector_hits_total += len(vector_hits)
        for rank, hit in enumerate(vector_hits, start=1):
            _merge_vector_hit(candidates, hit, rank, query_index)
        rankings.append([hit.chunk_id for hit in vector_hits])
        weights.append(config.vector_weight)
        timings["vector_ms"] = round(timings["vector_ms"] + _ms(vector_started), 1)

        bm25_started = time.perf_counter()
        # BM25 has no absolute threshold; gate it on this query's dense hits so
        # a stray lexical match cannot defeat refusal.
        bm25_hits = (
            await bm25_index.search(
                db,
                kb.id,
                kb.index_version,
                sub_query,
                config.bm25_top_k,
            )
            if vector_hits
            else []
        )
        bm25_hits_total += len(bm25_hits)
        for rank, hit in enumerate(bm25_hits, start=1):
            _merge_bm25_hit(candidates, hit, rank, query_index)
        rankings.append([hit.chunk.chunk_id for hit in bm25_hits])
        weights.append(config.bm25_weight)
        timings["bm25_ms"] = round(timings["bm25_ms"] + _ms(bm25_started), 1)

    # Trace views are the union of all queries, ordered by the best score seen.
    vector_trace = [
        _trace_candidate(candidate)
        for candidate in sorted(
            (item for item in candidates.values() if "vector_score" in item),
            key=lambda item: item["vector_score"],
            reverse=True,
        )
    ]
    bm25_trace = [
        _trace_candidate(candidate)
        for candidate in sorted(
            (item for item in candidates.values() if "bm25_score" in item),
            key=lambda item: item["bm25_score"],
            reverse=True,
        )
    ]

    # 6. RRF fusion across every query's dense and lexical ranking.
    started = time.perf_counter()
    fused = reciprocal_rank_fusion(rankings, k=config.rrf_k, weights=weights)
    for chunk_id, score in fused:
        candidates[chunk_id]["fused_score"] = round(score, 6)
    fused_candidates = [candidates[chunk_id] for chunk_id, _ in fused[: config.fusion_top_k]]
    timings["fusion_ms"] = _ms(started)

    # 7. Cross-encoder rerank over the fused list.
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
    timings["total_ms"] = _ms(total_started)

    for candidate in candidates.values():
        candidate["matched_queries"] = sorted(set(candidate["matched_queries"]))

    trace = {
        "query": query,
        "rewritten_query": rewritten,
        "rewrite_applied": rewrite.applied,
        "rewrite_error": rewrite.error,
        "sub_queries": queries[1:],
        "multi_query_applied": multi_query_applied,
        "multi_query_error": multi_query_error,
        "cache_hit": False,
        "reranked": reranked,
        "rerank_error": rerank_error,
        "config": asdict(config),
        "stage_timings": timings,
        "counts": {
            "vector": len(vector_trace),
            "bm25": len(bm25_trace),
            "fused": len(fused),
            "final": len(final_candidates),
            "queries": len(queries),
            "vector_hits": vector_hits_total,
            "bm25_hits": bm25_hits_total,
        },
        "candidates": {
            "vector": vector_trace,
            "bm25": bm25_trace,
            "fused": [_trace_candidate(candidate) for candidate in fused_candidates],
            "final": [_trace_candidate(candidate) for candidate in final_candidates],
        },
    }

    if cache_key is not None:
        await retrieval_cache.set(
            cache_key,
            {"candidates": final_candidates, "trace": trace},
            get_settings().retrieval_cache_ttl_seconds,
        )

    logger.info(
        "retrieval kb=%s queries=%d vector=%d bm25=%d final=%d rerank=%s cache=miss total=%.1fms",
        kb.id,
        len(queries),
        vector_hits_total,
        bm25_hits_total,
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
