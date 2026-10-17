"""Chroma vector store: one collection per knowledge base.

Chroma's client is synchronous, so every public function here is called through
``anyio.to_thread`` by the async layer. Chunk ids are the PostgreSQL chunk UUIDs,
which makes delete/upsert idempotent and lets retrieval join back to PG.
"""

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

import chromadb

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


@dataclass
class VectorHit:
    chunk_id: str
    document_id: str
    chunk_index: int
    filename: str
    heading_path: str | None
    content: str
    score: float


def collection_name(kb_id: UUID) -> str:
    # Chroma allows 3-63 chars of [a-zA-Z0-9_-]; a UUID hex fits exactly.
    return f"kb_{kb_id.hex}"


@lru_cache(maxsize=1)
def _client() -> chromadb.PersistentClient:
    path = Path(get_settings().chroma_dir)
    path.mkdir(parents=True, exist_ok=True)
    logger.info("opening Chroma at %s", path)
    return chromadb.PersistentClient(path=str(path))


def ping() -> bool:
    """Cheap liveness check used by /readyz (does not load any models)."""
    return _client().heartbeat() > 0


def _get_or_create_collection(kb_id: UUID):
    return _client().get_or_create_collection(
        name=collection_name(kb_id),
        metadata={"hnsw:space": "cosine"},
    )


def _get_collection(kb_id: UUID):
    try:
        return _client().get_collection(name=collection_name(kb_id))
    except Exception:  # NotFoundError, invalid name, ...
        return None


def upsert_chunks(kb_id: UUID, index_version: int, chunks: list[ChunkVector]) -> int:
    if not chunks:
        return 0
    collection = _get_or_create_collection(kb_id)
    collection.upsert(
        ids=[chunk.chunk_id for chunk in chunks],
        embeddings=[chunk.embedding for chunk in chunks],
        documents=[chunk.content for chunk in chunks],
        metadatas=[
            {
                "document_id": chunk.document_id,
                "chunk_index": chunk.chunk_index,
                "filename": chunk.filename,
                "heading_path": chunk.heading_path,
                "index_version": index_version,
            }
            for chunk in chunks
        ],
    )
    return len(chunks)


def query(
    kb_id: UUID,
    embedding: list[float],
    top_k: int,
    *,
    index_version: int | None = None,
) -> list[VectorHit]:
    collection = _get_collection(kb_id)
    if collection is None:
        return []

    where: dict[str, Any] | None = None
    if index_version is not None:
        where = {"index_version": index_version}

    result = collection.query(
        query_embeddings=[embedding],
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    ids = (result.get("ids") or [[]])[0]
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    hits: list[VectorHit] = []
    for chunk_id, document, metadata, distance in zip(
        ids, documents, metadatas, distances, strict=False
    ):
        metadata = metadata or {}
        # Collection uses cosine distance in [0, 2]; similarity is 1 - distance.
        hits.append(
            VectorHit(
                chunk_id=str(chunk_id),
                document_id=str(metadata.get("document_id", "")),
                chunk_index=int(metadata.get("chunk_index", 0)),
                filename=str(metadata.get("filename", "")),
                heading_path=str(metadata.get("heading_path") or "") or None,
                content=document or "",
                score=1.0 - float(distance),
            )
        )
    return hits


def delete_document_chunks(kb_id: UUID, document_id: UUID) -> None:
    collection = _get_collection(kb_id)
    if collection is not None:
        collection.delete(where={"document_id": str(document_id)})


def drop_kb_collection(kb_id: UUID) -> None:
    try:
        _client().delete_collection(name=collection_name(kb_id))
    except Exception:
        logger.debug("no Chroma collection to drop for kb %s", kb_id)


def count_chunks(kb_id: UUID) -> int:
    collection = _get_collection(kb_id)
    return int(collection.count()) if collection is not None else 0
