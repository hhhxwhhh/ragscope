from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.chunk import Chunk
from app.models.document import Document, DocumentStatus
from app.models.knowledge_base import KnowledgeBase
from app.services.storage import remove_stored_file, sanitize_filename, save_upload


async def get_owned_document(
    db: AsyncSession,
    document_id: UUID,
    owner_id: UUID,
) -> Document:
    document = await db.scalar(
        select(Document)
        .join(KnowledgeBase, Document.kb_id == KnowledgeBase.id)
        .where(Document.id == document_id, KnowledgeBase.owner_id == owner_id)
    )
    if document is None:
        raise NotFoundError("文档不存在")
    return document


async def list_documents(
    db: AsyncSession,
    kb_id: UUID,
    offset: int,
    limit: int,
) -> tuple[list[Document], int]:
    condition = Document.kb_id == kb_id
    items = (
        await db.scalars(
            select(Document)
            .where(condition)
            .order_by(Document.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    total = await db.scalar(select(func.count()).select_from(Document).where(condition))
    return list(items), int(total or 0)


async def create_document(
    db: AsyncSession,
    kb: KnowledgeBase,
    upload: UploadFile,
) -> Document:
    filename = sanitize_filename(upload.filename)
    document_id = uuid4()
    path, size, sha256 = await save_upload(kb.id, document_id, upload)

    existing = await db.scalar(
        select(Document).where(Document.kb_id == kb.id, Document.sha256 == sha256)
    )
    if existing is not None:
        remove_stored_file(str(path))
        raise ConflictError("该文件已存在于当前知识库")

    document = Document(
        id=document_id,
        kb_id=kb.id,
        filename=filename,
        ext=Path(filename).suffix.lower(),
        size_bytes=size,
        sha256=sha256,
        storage_path=str(path),
        status=DocumentStatus.PENDING,
    )
    db.add(document)
    await db.flush()
    return document


async def reset_for_retry(db: AsyncSession, document: Document) -> None:
    await db.execute(delete(Chunk).where(Chunk.document_id == document.id))
    document.status = DocumentStatus.PENDING
    document.error_stage = None
    document.error_message = None
    document.chunk_count = 0
    document.indexed_at = None
    await db.flush()


async def delete_document(db: AsyncSession, document: Document) -> None:
    remove_stored_file(document.storage_path)
    await db.delete(document)
