from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.errors import ConflictError
from app.db.session import get_db
from app.models.chunk import Chunk
from app.models.document import Document, DocumentStatus
from app.models.user import User
from app.schemas.common import Page
from app.schemas.document import ChunkOut, ChunkSearchItem, DocumentOut
from app.services import document_service, kb_service
from app.workers.dispatch import schedule

router = APIRouter(tags=["documents"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/kbs/{kb_id}/documents", response_model=Page[DocumentOut])
async def list_documents(
    kb_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
    offset: Offset = 0,
    limit: Limit = 20,
) -> Page[DocumentOut]:
    await kb_service.get_owned_kb(db, kb_id, current_user.id)
    items, total = await document_service.list_documents(db, kb_id, offset, limit)
    return Page[DocumentOut](
        items=[DocumentOut.model_validate(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/kbs/{kb_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    kb_id: UUID,
    file: Annotated[UploadFile, File()],
    background: BackgroundTasks,
    db: DbSession,
    current_user: CurrentUser,
) -> DocumentOut:
    kb = await kb_service.get_owned_kb(db, kb_id, current_user.id)
    document = await document_service.create_document(db, kb, file)

    # Commit before scheduling: the background task uses its own session.
    await db.commit()
    schedule(background, "ingest", document.id)
    return DocumentOut.model_validate(document)


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> DocumentOut:
    document = await document_service.get_owned_document(db, document_id, current_user.id)
    return DocumentOut.model_validate(document)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    document = await document_service.get_owned_document(db, document_id, current_user.id)
    await document_service.delete_document(db, document)
    await db.commit()


@router.post(
    "/documents/{document_id}/retry",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_document(
    document_id: UUID,
    background: BackgroundTasks,
    db: DbSession,
    current_user: CurrentUser,
) -> DocumentOut:
    document = await document_service.get_owned_document(db, document_id, current_user.id)
    if document.status != DocumentStatus.FAILED:
        raise ConflictError("只有失败的文档可以重试")

    await document_service.reset_for_retry(db, document)
    await db.commit()
    schedule(background, "ingest", document.id)
    return DocumentOut.model_validate(document)


@router.get("/kbs/{kb_id}/chunks", response_model=list[ChunkSearchItem])
async def search_chunks(
    kb_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ChunkSearchItem]:
    """Full-text-ish chunk lookup used by the eval annotation UI."""
    await kb_service.get_owned_kb(db, kb_id, current_user.id)
    stmt = (
        select(Chunk, Document.filename)
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.kb_id == kb_id)
    )
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Chunk.content.ilike(pattern), Chunk.heading_path.ilike(pattern)))
    rows = (
        await db.execute(stmt.order_by(Document.filename, Chunk.chunk_index).limit(limit))
    ).all()
    return [
        ChunkSearchItem(
            id=chunk.id,
            document_id=chunk.document_id,
            filename=filename,
            chunk_index=chunk.chunk_index,
            heading_path=chunk.heading_path,
            content=chunk.content,
        )
        for chunk, filename in rows
    ]


@router.get("/documents/{document_id}/chunks", response_model=Page[ChunkOut])
async def list_chunks(
    document_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
    offset: Offset = 0,
    limit: Limit = 50,
) -> Page[ChunkOut]:
    await document_service.get_owned_document(db, document_id, current_user.id)
    condition = Chunk.document_id == document_id
    items = (
        await db.scalars(
            select(Chunk).where(condition).order_by(Chunk.chunk_index).offset(offset).limit(limit)
        )
    ).all()
    total = await db.scalar(select(func.count()).select_from(Chunk).where(condition))
    return Page[ChunkOut](
        items=[ChunkOut.model_validate(item) for item in items],
        total=int(total or 0),
        offset=offset,
        limit=limit,
    )
