"""Semantic answer cache.

Exact-match caches miss almost everything in a question-answering product: users
ask "X1 电池多大" and "X1 的电池容量是多少" within a minute of each other. A
semantic cache keys on the *meaning* of the question, so the second request can
skip generation entirely.

Design decisions worth defending:

* **Postgres, not a second vector store.** The candidate set is tiny (bounded per
  knowledge base) and we already require Postgres. Loading ≤200 embeddings and
  doing cosine in NumPy costs microseconds; a separate index would add an
  operational dependency for no measurable gain at this size.
* **Cache after retrieval, not before.** The entry stores the evidence set it was
  generated from, so a hit can restore *exactly* the citations the answer refers
  to. Caching before retrieval would let an old answer attach to new evidence and
  silently break `[n]` markers.
* **Never cache refusals.** "No evidence" is cheap to compute and depends on
  thresholds/config, so caching it would freeze a temporary state.
* **Bounded + version-scoped.** Entries carry `index_version` and
  `prompt_variant`; changing either makes old entries unreachable immediately, and
  they are pruned on the next write.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.config import get_settings
from app.models.knowledge_base import KnowledgeBase
from app.models.semantic_cache import SemanticCacheEntry
from app.rag.embedder import embed_texts_async
from app.rag.pipeline import RetrievalOutcome

logger = logging.getLogger(__name__)

_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[，。！？；：、,.!?;:]+$")


def normalize_question(question: str) -> str:
    text = _SPACE.sub(" ", (question or "").strip()).casefold()
    return _PUNCT.sub("", text)[:500]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    left = np.asarray(a, dtype=np.float32)
    right = np.asarray(b, dtype=np.float32)
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(left, right) / denominator)


@dataclass
class CacheHit:
    answer: str
    candidates: list[dict[str, Any]]
    trace: dict[str, Any]
    usage: dict[str, Any] | None
    score: float
    question: str


async def lookup(
    db: AsyncSession,
    kb: KnowledgeBase,
    question: str,
    *,
    prompt_variant: str,
    threshold: float | None = None,
) -> CacheHit | None:
    """Find a semantically equivalent question for this KB/version/variant."""
    settings = get_settings()
    if not settings.semantic_cache_enabled:
        return None

    limit = max(1, settings.semantic_cache_max_entries)
    entries = (
        await db.scalars(
            select(SemanticCacheEntry)
            .where(
                SemanticCacheEntry.kb_id == kb.id,
                SemanticCacheEntry.index_version == kb.index_version,
                SemanticCacheEntry.prompt_variant == prompt_variant,
            )
            .order_by(SemanticCacheEntry.last_hit_at.desc())
            .limit(limit)
        )
    ).all()
    if not entries:
        metrics.observe_semantic_cache("miss")
        return None

    normalized = normalize_question(question)
    for entry in entries:
        if entry.normalized_question == normalized:
            _touch(entry)
            metrics.observe_semantic_cache("hit")
            return _to_hit(entry, 1.0)

    try:
        vector = (await embed_texts_async([question], is_query=True))[0]
    except Exception:
        logger.warning("semantic cache embedding failed; treating as miss", exc_info=True)
        metrics.observe_semantic_cache("error")
        return None

    cutoff = settings.semantic_cache_threshold if threshold is None else threshold
    best: tuple[float, SemanticCacheEntry | None] = (0.0, None)
    for entry in entries:
        score = cosine(vector, entry.embedding or [])
        if score > best[0]:
            best = (score, entry)

    if best[1] is None or best[0] < cutoff:
        metrics.observe_semantic_cache("miss")
        return None

    _touch(best[1])
    metrics.observe_semantic_cache("hit")
    logger.info(
        "semantic cache hit kb=%s score=%.4f cached_question=%r",
        kb.id,
        best[0],
        best[1].question,
    )
    return _to_hit(best[1], best[0])


def _touch(entry: SemanticCacheEntry) -> None:
    entry.hits = (entry.hits or 0) + 1
    from datetime import UTC, datetime

    entry.last_hit_at = datetime.now(UTC)


def _to_hit(entry: SemanticCacheEntry, score: float) -> CacheHit:
    trace = dict(entry.trace or {})
    trace["semantic_cache_hit"] = True
    trace["semantic_cache_score"] = round(score, 4)
    trace["cache_hit"] = False  # distinguishes exact retrieval cache from this one
    return CacheHit(
        answer=entry.answer,
        candidates=list(entry.candidates or []),
        trace=trace,
        usage=entry.usage,
        score=score,
        question=entry.question,
    )


async def store(
    db: AsyncSession,
    kb: KnowledgeBase,
    question: str,
    answer: str,
    outcome: RetrievalOutcome,
    *,
    prompt_variant: str,
    usage: dict[str, Any] | None,
) -> bool:
    """Remember one (question, evidence, answer) triple. Best-effort."""
    settings = get_settings()
    if not settings.semantic_cache_enabled:
        return False
    if not answer or not outcome.candidates:
        return False

    try:
        vector = (await embed_texts_async([question], is_query=True))[0]
    except Exception:
        logger.warning("semantic cache embed failed; skipping store", exc_info=True)
        metrics.observe_semantic_cache("error")
        return False

    normalized = normalize_question(question)
    existing = await db.scalar(
        select(SemanticCacheEntry).where(
            SemanticCacheEntry.kb_id == kb.id,
            SemanticCacheEntry.index_version == kb.index_version,
            SemanticCacheEntry.prompt_variant == prompt_variant,
            SemanticCacheEntry.normalized_question == normalized,
        )
    )
    payload = {
        "question": question,
        "answer": answer,
        "candidates": outcome.candidates,
        "trace": {key: value for key, value in outcome.trace.items() if key != "candidates"},
        "usage": usage,
        "embedding": [float(value) for value in vector],
    }
    if existing is not None:
        for key, value in payload.items():
            setattr(existing, key, value)
    else:
        db.add(
            SemanticCacheEntry(
                kb_id=kb.id,
                index_version=kb.index_version,
                prompt_variant=prompt_variant,
                normalized_question=normalized,
                **payload,
            )
        )

    await db.flush()
    await _prune(db, kb, prompt_variant)
    metrics.observe_semantic_cache("store")
    return True


async def _prune(db: AsyncSession, kb: KnowledgeBase, prompt_variant: str) -> None:
    """Keep the newest N entries per (kb, version, variant), drop older versions."""
    settings = get_settings()
    await db.execute(
        delete(SemanticCacheEntry).where(
            SemanticCacheEntry.kb_id == kb.id,
            SemanticCacheEntry.index_version != kb.index_version,
        )
    )
    total = int(
        await db.scalar(
            select(func.count())
            .select_from(SemanticCacheEntry)
            .where(
                SemanticCacheEntry.kb_id == kb.id,
                SemanticCacheEntry.index_version == kb.index_version,
                SemanticCacheEntry.prompt_variant == prompt_variant,
            )
        )
        or 0
    )
    excess = total - settings.semantic_cache_max_entries
    if excess <= 0:
        return
    stale_ids = (
        await db.scalars(
            select(SemanticCacheEntry.id)
            .where(
                SemanticCacheEntry.kb_id == kb.id,
                SemanticCacheEntry.index_version == kb.index_version,
                SemanticCacheEntry.prompt_variant == prompt_variant,
            )
            .order_by(SemanticCacheEntry.last_hit_at.asc())
            .limit(excess)
        )
    ).all()
    if stale_ids:
        await db.execute(delete(SemanticCacheEntry).where(SemanticCacheEntry.id.in_(stale_ids)))


async def stats(db: AsyncSession, kb_id: UUID) -> dict[str, Any]:
    total = int(
        await db.scalar(
            select(func.count()).select_from(SemanticCacheEntry).where(
                SemanticCacheEntry.kb_id == kb_id
            )
        )
        or 0
    )
    hits = int(
        await db.scalar(
            select(func.coalesce(func.sum(SemanticCacheEntry.hits), 0)).where(
                SemanticCacheEntry.kb_id == kb_id
            )
        )
        or 0
    )
    return {"entries": total, "hits": hits}
