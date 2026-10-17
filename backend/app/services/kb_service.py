from uuid import UUID

import anyio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.rag import vector_store
from app.schemas.kb import KbCreate, KbUpdate
from app.services.storage import remove_stored_file


async def get_owned_kb(
    db: AsyncSession,
    kb_id: UUID,
    owner_id: UUID,
) -> KnowledgeBase:
    kb = await db.get(KnowledgeBase, kb_id)
    # Returning 404 (not 403) avoids leaking whether someone else's KB exists.
    if kb is None or kb.owner_id != owner_id:
        raise NotFoundError("知识库不存在")
    return kb


async def list_kbs(
    db: AsyncSession,
    owner_id: UUID,
    offset: int,
    limit: int,
) -> tuple[list[KnowledgeBase], int]:
    condition = KnowledgeBase.owner_id == owner_id
    items = (
        await db.scalars(
            select(KnowledgeBase)
            .where(condition)
            .order_by(KnowledgeBase.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    total = await db.scalar(select(func.count()).select_from(KnowledgeBase).where(condition))
    return list(items), int(total or 0)


async def create_kb(
    db: AsyncSession,
    owner_id: UUID,
    payload: KbCreate,
) -> KnowledgeBase:
    kb = KnowledgeBase(owner_id=owner_id, **payload.model_dump())
    db.add(kb)
    await db.flush()
    await db.refresh(kb)
    return kb


async def update_kb(
    db: AsyncSession,
    kb: KnowledgeBase,
    payload: KbUpdate,
) -> KnowledgeBase:
    data = payload.model_dump(exclude_unset=True)

    chunk_size = data.get("chunk_size", kb.chunk_size)
    chunk_overlap = data.get("chunk_overlap", kb.chunk_overlap)
    if chunk_overlap >= chunk_size:
        raise BadRequestError("chunk_overlap 必须小于 chunk_size")

    chunk_params_changed = ("chunk_size" in data and data["chunk_size"] != kb.chunk_size) or (
        "chunk_overlap" in data and data["chunk_overlap"] != kb.chunk_overlap
    )
    if chunk_params_changed:
        doc_count = await db.scalar(
            select(func.count()).select_from(Document).where(Document.kb_id == kb.id)
        )
        if doc_count:
            raise ConflictError("知识库已有文档，修改切块参数请新建知识库")

    for field, value in data.items():
        setattr(kb, field, value)
    await db.flush()
    await db.refresh(kb)
    return kb


async def delete_kb(db: AsyncSession, kb: KnowledgeBase) -> None:
    paths = (await db.scalars(select(Document.storage_path).where(Document.kb_id == kb.id))).all()
    for path in paths:
        remove_stored_file(path)
    await anyio.to_thread.run_sync(vector_store.drop_kb_collection, kb.id)
    # documents/chunks are removed by ON DELETE CASCADE in PostgreSQL.
    await db.delete(kb)
