import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import anyio
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document, DocumentStatus
from app.models.knowledge_base import KnowledgeBase
from app.rag.chunker import build_chunks
from app.rag.parsers import parse_document

logger = logging.getLogger(__name__)

STALE_MINUTES = 15
IN_PROGRESS = (DocumentStatus.PENDING, DocumentStatus.PARSING, DocumentStatus.INDEXING)


async def ingest_document(db: AsyncSession, document_id: UUID) -> None:
    document = await db.get(Document, document_id)
    if document is None:
        return

    stage = "parsing"
    try:
        document.status = DocumentStatus.PARSING
        document.error_stage = None
        document.error_message = None
        await db.commit()

        path = Path(document.storage_path)
        if not path.is_file():
            raise FileNotFoundError("原始文件已丢失，请重新上传")

        text = await anyio.to_thread.run_sync(parse_document, path)
        kb = await db.get(KnowledgeBase, document.kb_id)
        if kb is None:
            raise RuntimeError("知识库已被删除")

        stage = "chunking"
        drafts = build_chunks(
            text,
            chunk_size=kb.chunk_size,
            chunk_overlap=kb.chunk_overlap,
        )
        if not drafts:
            raise ValueError("文档没有可索引的内容（每个切块至少 30 字）")

        stage = "indexing"
        document.status = DocumentStatus.INDEXING
        await db.commit()

        # Retrying must be idempotent: remove chunks from any previous attempt.
        await db.execute(delete(Chunk).where(Chunk.document_id == document.id))
        db.add_all(
            [
                Chunk(
                    document_id=document.id,
                    kb_id=document.kb_id,
                    chunk_index=index,
                    content=draft.content,
                    embedding_text=draft.embedding_text,
                    heading_path=draft.heading_path,
                    char_start=draft.char_start,
                    char_end=draft.char_end,
                )
                for index, draft in enumerate(drafts)
            ]
        )

        document.chunk_count = len(drafts)
        document.indexed_at = datetime.now(UTC)
        document.status = DocumentStatus.INDEXED
        kb.index_version += 1
        await db.commit()

    except Exception as exc:
        await db.rollback()
        await _mark_failed(db, document_id, stage, exc)
        logger.warning("ingestion failed for %s at %s: %s", document_id, stage, exc)


async def _mark_failed(
    db: AsyncSession,
    document_id: UUID,
    stage: str,
    exc: Exception,
) -> None:
    document = await db.get(Document, document_id)
    if document is None:
        return
    document.status = DocumentStatus.FAILED
    document.error_stage = stage
    document.error_message = (str(exc) or exc.__class__.__name__)[:1000]
    await db.commit()


async def recover_stale_documents(db: AsyncSession) -> int:
    """Mark documents stuck in an in-progress state (e.g. after a crash) as failed."""
    cutoff = datetime.now(UTC) - timedelta(minutes=STALE_MINUTES)
    result = await db.execute(
        update(Document)
        .where(Document.status.in_(IN_PROGRESS), Document.updated_at < cutoff)
        .values(
            status=DocumentStatus.FAILED,
            error_stage="stale",
            error_message="入库超时，请重试",
        )
    )
    await db.commit()
    return int(result.rowcount or 0)
