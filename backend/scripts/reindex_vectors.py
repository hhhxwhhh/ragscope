"""Populate Chroma from chunks that were ingested before Week 2.

Usage:
    PYTHONPATH=. .venv/Scripts/python.exe scripts/reindex_vectors.py [kb_id]

Re-parsing is unnecessary: PostgreSQL already holds the chunk text and offsets.
"""

from app.core.env import configure_env

configure_env()

import asyncio  # noqa: E402
import sys  # noqa: E402
from uuid import UUID  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.chunk import Chunk  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.knowledge_base import KnowledgeBase  # noqa: E402
from app.rag import vector_store  # noqa: E402
from app.rag.embedder import embed_texts_async  # noqa: E402


async def reindex(kb_id: UUID | None) -> None:
    async with SessionLocal() as db:
        query = select(KnowledgeBase)
        if kb_id is not None:
            query = query.where(KnowledgeBase.id == kb_id)
        kbs = (await db.scalars(query)).all()

        for kb in kbs:
            rows = (
                await db.execute(
                    select(Chunk, Document.filename)
                    .join(Document, Chunk.document_id == Document.id)
                    .where(Chunk.kb_id == kb.id)
                    .order_by(Chunk.document_id, Chunk.chunk_index)
                )
            ).all()
            if not rows:
                print(f"kb {kb.id}: no chunks, skipped")
                continue

            embeddings = await embed_texts_async([chunk.embedding_text for chunk, _ in rows])
            new_version = kb.index_version + 1
            records = [
                vector_store.ChunkVector(
                    chunk_id=str(chunk.id),
                    document_id=str(chunk.document_id),
                    chunk_index=chunk.chunk_index,
                    filename=filename,
                    heading_path=chunk.heading_path or "",
                    content=chunk.content,
                    embedding=embedding,
                )
                for (chunk, filename), embedding in zip(rows, embeddings, strict=False)
            ]
            vector_store.upsert_chunks(kb.id, new_version, records)
            kb.index_version = new_version
            print(f"kb {kb.id} ({kb.name}): indexed {len(records)} chunks, v{new_version}")

        await db.commit()


if __name__ == "__main__":
    target = UUID(sys.argv[1]) if len(sys.argv) > 1 else None
    asyncio.run(reindex(target))
