"""In-memory BM25 index built from PostgreSQL chunks, exposed as a retriever.

The corpus is loaded once per ``(kb_id, index_version)`` and cached in an LRU.
Ingestion increments ``index_version``, so a rebuild is triggered naturally on
the next query and stale versions are evicted without explicit invalidation.

The retriever implements LangChain's ``BaseRetriever`` interface. The pipeline
calls ``scored_search`` because the trace needs absolute BM25 scores, something
the generic ``invoke`` API (documents only) does not expose.
"""

import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import anyio
import jieba
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document as DocumentModel

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
_cache: OrderedDict[
    tuple[str, int], tuple["JiebaBM25Retriever", list["Bm25Document"]]
] = OrderedDict()
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


class JiebaBM25Retriever(BaseRetriever):
    """LangChain retriever over an in-memory BM25 index.

    ``preprocess_func`` is jieba-based because the corpus is Chinese and BM25
    needs word boundaries. ``scored_search`` is the observability-friendly
    variant used by the retrieval pipeline.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    documents: list[Document]
    vectorizer: Any
    preprocess_func: Callable[[str], list[str]]
    top_k: int = 20

    def _rank(self, query: str) -> list[Document]:
        tokens = self.preprocess_func(query)
        if not tokens:
            return []
        return self.vectorizer.get_top_n(tokens, self.documents, n=self.top_k)

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> list[Document]:
        return self._rank(query)

    async def _aget_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> list[Document]:
        return await anyio.to_thread.run_sync(self._rank, query)

    def scored_search(self, query: str, top_k: int) -> list[tuple[Document, float]]:
        tokens = self.preprocess_func(query)
        if not tokens:
            return []
        scores = self.vectorizer.get_scores(tokens)
        ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)[:top_k]
        return [(self.documents[index], float(score)) for index, score in ranked if score > 0]


def _build_index(documents: list[Document]) -> JiebaBM25Retriever:
    vectorizer = BM25Okapi([tokenize(document.page_content) for document in documents])
    return JiebaBM25Retriever(
        documents=documents,
        vectorizer=vectorizer,
        preprocess_func=tokenize,
    )


def _to_langchain_document(document: Bm25Document) -> Document:
    return Document(
        id=document.chunk_id,
        page_content=document.content,
        metadata={
            "document_id": document.document_id,
            "chunk_index": document.chunk_index,
            "filename": document.filename,
            "heading_path": document.heading_path,
        },
    )


async def _load_index(
    db: AsyncSession,
    kb_id: UUID,
    index_version: int,
) -> tuple[JiebaBM25Retriever | None, list[Bm25Document]]:
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
                DocumentModel.filename,
            )
            .join(DocumentModel, Chunk.document_id == DocumentModel.id)
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

    retriever = (
        await anyio.to_thread.run_sync(
            _build_index, [_to_langchain_document(document) for document in documents]
        )
        if documents
        else None
    )

    entry: tuple[JiebaBM25Retriever | None, list[Bm25Document]] = (retriever, documents)
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
    retriever, documents = await _load_index(db, kb_id, index_version)
    if retriever is None or not query.strip():
        return []

    scored = await anyio.to_thread.run_sync(retriever.scored_search, query, top_k)
    by_chunk_id = {document.chunk_id: document for document in documents}
    return [
        Bm25Hit(chunk=by_chunk_id[str(document.id)], score=score)
        for document, score in scored
    ]


def clear_cache() -> None:
    """Used by tests and maintenance scripts."""
    with _lock:
        _cache.clear()
