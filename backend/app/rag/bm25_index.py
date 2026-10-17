"""In-memory BM25 index built from PostgreSQL chunks.

The index is keyed by ``(kb_id, index_version)`` and cached in an LRU. Ingestion
increments ``index_version``, so a rebuild is triggered naturally on the next
query and stale versions are evicted without explicit invalidation.
"""

import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from uuid import UUID

import anyio
import jieba
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[0-9a-zA-Z一-鿿]")
_STOPWORDS = {
    "的",
    "了",
    "和",
    "是",
    "在",
    "有",
    "我",
    "你",
    "他",
    "她",
    "它",
    "们",
    "这",
    "那",
    "吗",
    "呢",
    "吧",
    "啊",
    "呀",
    "与",
    "及",
    "或",
}

_CACHE_SIZE = 8
_cache: OrderedDict[tuple[str, int], tuple[BM25Okapi | None, list["Bm25Document"]]] = OrderedDict()
_lock = threading.Lock()


@dataclass(frozen=True)
class Bm25Document:
    chunk_id: str
    document_id: str
    chunk_index: int
    filename: str
    heading_path: str | None
    content: str


@dataclass(frozen=True)
class Bm25Hit:
    chunk: Bm25Document
    score: float


def tokenize(text: str) -> list[str]:
    return [
        token
        for token in jieba.lcut(text.lower())
        if token.strip() and token not in _STOPWORDS and _TOKEN_RE.search(token)
    ]


def _build(corpus: list[str]) -> BM25Okapi:
    return BM25Okapi([tokenize(text) for text in corpus])


async def _load_index(
    db: AsyncSession,
    kb_id: UUID,
    index_version: int,
) -> tuple[BM25Okapi | None, list[Bm25Document]]:
    key = (str(kb_id), index_version)
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached

    rows = (
        await db.execute(
            select(
                Chunk.id,
                Chunk.document_id,
                Chunk.chunk_index,
                Chunk.heading_path,
                Chunk.content,
                Document.filename,
            )
            .join(Document, Chunk.document_id == Document.id)
            .where(Chunk.kb_id == kb_id)
            .order_by(Chunk.document_id, Chunk.chunk_index)
        )
    ).all()

    documents = [
        Bm25Document(
            chunk_id=str(row.id),
            document_id=str(row.document_id),
            chunk_index=row.chunk_index,
            filename=row.filename,
            heading_path=row.heading_path,
            content=row.content,
        )
        for row in rows
    ]

    if documents:
        bm25 = await anyio.to_thread.run_sync(_build, [document.content for document in documents])
    else:
        bm25 = None

    entry: tuple[BM25Okapi | None, list[Bm25Document]] = (bm25, documents)
    with _lock:
        _cache[key] = entry
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return entry


async def search(
    db: AsyncSession,
    kb_id: UUID,
    index_version: int,
    query: str,
    top_k: int,
) -> list[Bm25Hit]:
    bm25, documents = await _load_index(db, kb_id, index_version)
    if bm25 is None or not query.strip():
        return []

    tokens = await anyio.to_thread.run_sync(tokenize, query)
    if not tokens:
        return []

    scores = bm25.get_scores(tokens)
    ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)[:top_k]
    return [
        Bm25Hit(chunk=documents[index], score=float(score)) for index, score in ranked if score > 0
    ]


def clear_cache() -> None:
    """Used by tests and maintenance scripts."""
    with _lock:
        _cache.clear()
