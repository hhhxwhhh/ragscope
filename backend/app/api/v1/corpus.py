"""Corpus packs: browse crawled Markdown and import it into a knowledge base."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.services import corpus_service, kb_service

router = APIRouter(prefix="/corpus", tags=["corpus"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


class CorpusImportRequest(BaseModel):
    kb_id: UUID


class CorpusImportStatus(BaseModel):
    total: int = 0
    done: int = 0
    added: int = 0
    skipped: int = 0
    failed: int = 0
    status: str = "idle"
    error: str | None = None


@router.get("")
async def list_packs(current_user: CurrentUser) -> list[dict[str, Any]]:
    return corpus_service.list_packs()


@router.get("/{slug}")
async def get_pack(slug: str, current_user: CurrentUser) -> dict[str, Any]:
    return corpus_service.get_pack(slug)


@router.get("/{slug}/docs/{filename}")
async def read_document(slug: str, filename: str, current_user: CurrentUser) -> dict[str, str]:
    return {"filename": filename, "content": corpus_service.read_document(slug, filename)}


@router.post("/{slug}/import", status_code=status.HTTP_202_ACCEPTED)
async def import_pack(
    slug: str,
    payload: CorpusImportRequest,
    background: BackgroundTasks,
    db: DbSession,
    current_user: CurrentUser,
) -> CorpusImportStatus:
    """Import the whole pack into a knowledge base.

    Snapshot semantics: documents whose URL already exists in the KB are skipped.
    Progress is written to Redis so the UI can poll without a job table.
    """
    pack = corpus_service.get_pack(slug)
    await kb_service.get_owned_kb(db, payload.kb_id, current_user.id)
    background.add_task(corpus_service.import_pack, payload.kb_id, slug)
    return CorpusImportStatus(total=pack["count"], status="running")


@router.get("/{slug}/import/{kb_id}", response_model=CorpusImportStatus)
async def import_status(
    slug: str,
    kb_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> CorpusImportStatus:
    await kb_service.get_owned_kb(db, kb_id, current_user.id)
    progress = await corpus_service.get_progress(kb_id, slug)
    return CorpusImportStatus(**(progress or {}))
