"""Chroma vector store adapter built on ``langchain_chroma.Chroma``.

One collection per knowledge base, chunk ids are PostgreSQL chunk UUIDs, and
the distance metric is cosine. Reads go through LangChain's public
``similarity_search_by_vector_with_relevance_scores``; ingestion supplies
pre-computed BGE vectors, so writes use the underlying collection's ``upsert``
instead of ``add_texts`` (which would re-embed everything).

Chroma's client is synchronous, so async callers wrap these functions in
``anyio.to_thread``. Readiness deliberately uses a raw client so it never loads
the embedding model.
"""

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

import chromadb
from langchain_chroma import Chroma

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ChunkVector:
    chunk_id: str
    document_id: str
    chunk_index: int
    filename: str
    heading_path: str
    content: str
    embedding: list[float]
    page_number: int | None = None


@dataclass
class VectorHit:
    chunk_id: str
    document_id: str
    chunk_index: int
    filename: str
    heading_path: str | None
    content: str
    score: float
    page_number: int | None = None


def collection_name(kb_id: UUID) -> str:
    # Chroma allows 3-63 chars of [a-zA-Z0-9_-]; a UUID hex fits exactly.
    return f"kb_{kb_id.hex}"


@lru_cache(maxsize=1)
def _client() -> chromadb.PersistentClient:
    path = Path(get_settings().chroma_dir)
    path.mkdir(parents=True, exist_ok=True)
    logger.info("opening Chroma at %s", path)
    return chromadb.PersistentClient(path=str(path))


def _open_store(kb_id: UUID, *, create: bool) -> Chroma:
    return Chroma(
        collection_name=collection_name(kb_id),
        # Vectors are always supplied by our embedding wrapper. Leaving this
        # empty also keeps readiness/admin calls from loading BGE.
        embedding_function=None,
        persist_directory=str(Path(get_settings().chroma_dir)),
        collection_metadata={"hnsw:space": "cosine"},
        create_collection_if_not_exists=create,
    )


@lru_cache(maxsize=8)
def _read_store(kb_id: UUID) -> Chroma:
    return _open_store(kb_id, create=False)


@lru_cache(maxsize=8)
def _write_store(kb_id: UUID) -> Chroma:
    return _open_store(kb_id, create=True)


def _get_read_store(kb_id: UUID) -> Chroma | None:
    try:
        return _read_store(kb_id)
    except Exception:  # collection does not exist / invalid name
        return None


def ping() -> bool:
    """Cheap liveness check used by /readyz (does not load any models)."""
    return _client().heartbeat() > 0


def upsert_chunks(kb_id: UUID, index_version: int, chunks: list[ChunkVector]) -> int:
    if not chunks:
        return 0
    store = _write_store(kb_id)
    metadatas: list[dict[str, Any]] = []
    for chunk in chunks:
        metadata: dict[str, Any] = {
            "document_id": chunk.document_id,
            "chunk_index": chunk.chunk_index,
            "filename": chunk.filename,
            "heading_path": chunk.heading_path,
            "index_version": index_version,
        }
        # Chroma metadata values cannot be None; omit the key for text formats.
        if chunk.page_number is not None:
            metadata["page_number"] = chunk.page_number
        metadatas.append(metadata)

    # Public LangChain add_texts() would call the embedding function again.
    # Ingestion already embedded outside the write transaction, so write the
    # pre-computed vectors directly. upsert keeps retries idempotent.
    store._collection.upsert(
        ids=[chunk.chunk_id for chunk in chunks],
        embeddings=[chunk.embedding for chunk in chunks],
        documents=[chunk.content for chunk in chunks],
        metadatas=metadatas,
    )
    return len(chunks)


def query(
    kb_id: UUID,
    embedding: list[float],
    top_k: int,
    *,
    index_version: int | None = None,
) -> list[VectorHit]:
    store = _get_read_store(kb_id)
    if store is None:
        return []

    where: dict[str, Any] | None = None
    if index_version is not None:
        where = {"index_version": index_version}

    # LangChain returns relevance = 1 - cosine distance, the same score the
    # hand-written client produced before the migration.
    matches = store.similarity_search_by_vector_with_relevance_scores(
        embedding,
        k=top_k,
        filter=where,
    )

    hits: list[VectorHit] = []
    for document, score in matches:
        metadata = document.metadata or {}
        hits.append(
            VectorHit(
                chunk_id=str(document.id or ""),
                document_id=str(metadata.get("document_id", "")),
                chunk_index=int(metadata.get("chunk_index", 0)),
                filename=str(metadata.get("filename", "")),
                heading_path=str(metadata.get("heading_path") or "") or None,
                content=document.page_content or "",
                score=float(score),
                page_number=(
                    int(metadata["page_number"])
                    if metadata.get("page_number") is not None
                    else None
                ),
            )
        )
    return hits


def delete_document_chunks(kb_id: UUID, document_id: UUID) -> None:
    store = _get_read_store(kb_id)
    if store is not None:
        store._collection.delete(where={"document_id": str(document_id)})


def drop_kb_collection(kb_id: UUID) -> None:
    try:
        _client().delete_collection(name=collection_name(kb_id))
    except Exception:
        logger.debug("no Chroma collection to drop for kb %s", kb_id)
    finally:
        # Cached handles point at a collection that no longer exists.
        _read_store.cache_clear()
        _write_store.cache_clear()


def count_chunks(kb_id: UUID) -> int:
    store = _get_read_store(kb_id)
    return int(store._collection.count()) if store is not None else 0
