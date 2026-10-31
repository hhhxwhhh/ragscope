"""Data source API: manage connectors, trigger syncs, inspect run history."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.source import (
    SourceCreate,
    SourceOut,
    SourcePreviewOut,
    SourcePreviewRequest,
    SourceUpdate,
    SyncRunOut,
)
from app.services import audit_service, kb_service, source_service

router = APIRouter(tags=["sources"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("/kbs/{kb_id}/sources", response_model=list[SourceOut])
async def list_sources(
    kb_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> list[SourceOut]:
    kb = await kb_service.get_owned_kb(db, kb_id, current_user.id)
    return [SourceOut.model_validate(item) for item in await source_service.list_sources(db, kb.id)]


@router.post(
    "/kbs/{kb_id}/sources",
    response_model=SourceOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_source(
    kb_id: UUID,
    payload: SourceCreate,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> SourceOut:
    kb = await kb_service.get_owned_kb(db, kb_id, current_user.id)
    source = await source_service.create_source(db, kb, payload)
    await audit_service.record(
        db,
        action="source.create",
        actor=current_user,
        resource_type="data_source",
        resource_id=source.id,
        detail={
            "name": source.name,
            "type": source.source_type,
            "schedule_minutes": source.schedule_minutes,
        },
        request=request,
    )
    await db.commit()
    return SourceOut.model_validate(source)


@router.post("/kbs/{kb_id}/sources/preview", response_model=SourcePreviewOut)
async def preview_source(
    kb_id: UUID,
    payload: SourcePreviewRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> SourcePreviewOut:
    """Fetch a handful of items without writing anything.

    Connector config is easy to get subtly wrong (wrong sitemap, path returns 404).
    Letting the user see real titles before committing avoids a sync that quietly
    adds nothing.
    """
    await kb_service.get_owned_kb(db, kb_id, current_user.id)
    result = await source_service.preview(payload.source_type, payload.config, limit=5)
    return SourcePreviewOut(**result)


@router.patch("/sources/{source_id}", response_model=SourceOut)
async def update_source(
    source_id: UUID,
    payload: SourceUpdate,
    db: DbSession,
    current_user: CurrentUser,
) -> SourceOut:
    source = await source_service.get_owned_source(db, source_id, current_user.id)
    source = await source_service.update_source(db, source, payload)
    await db.commit()
    return SourceOut.model_validate(source)


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: UUID,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    source = await source_service.get_owned_source(db, source_id, current_user.id)
    source_id_copy = source.id
    name = source.name
    await source_service.delete_source(db, source)
    await audit_service.record(
        db,
        action="source.delete",
        actor=current_user,
        resource_type="data_source",
        resource_id=source_id_copy,
        detail={"name": name},
        request=request,
    )
    await db.commit()


@router.post(
    "/sources/{source_id}/sync",
    response_model=SyncRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def sync_now(
    source_id: UUID,
    background: BackgroundTasks,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> SyncRunOut:
    source = await source_service.get_owned_source(db, source_id, current_user.id)
    run = await source_service.create_run(db, source, trigger="manual")
    await audit_service.record(
        db,
        action="source.sync",
        actor=current_user,
        resource_type="data_source",
        resource_id=source.id,
        detail={"name": source.name, "run_id": str(run.id)},
        request=request,
    )
    await db.commit()
    source_service.dispatch_run(run.id, background)
    return SyncRunOut.model_validate(run)


@router.get("/sources/{source_id}/runs", response_model=list[SyncRunOut])
async def list_runs(
    source_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[SyncRunOut]:
    source = await source_service.get_owned_source(db, source_id, current_user.id)
    runs = await source_service.list_runs(db, source.id, limit=limit)
    return [SyncRunOut.model_validate(run) for run in runs]
