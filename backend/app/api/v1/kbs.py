from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.common import Page
from app.schemas.kb import KbCreate, KbOut, KbUpdate
from app.services import kb_service

router = APIRouter(prefix="/kbs", tags=["knowledge-bases"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("", response_model=Page[KbOut])
async def list_knowledge_bases(
    db: DbSession,
    current_user: CurrentUser,
    offset: Offset = 0,
    limit: Limit = 20,
) -> Page[KbOut]:
    items, total = await kb_service.list_kbs(db, current_user.id, offset, limit)
    return Page[KbOut](
        items=[KbOut.model_validate(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("", response_model=KbOut, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    payload: KbCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> KbOut:
    kb = await kb_service.create_kb(db, current_user.id, payload)
    # Commit explicitly: dependency teardown runs after the response is sent.
    await db.commit()
    return KbOut.model_validate(kb)


@router.get("/{kb_id}", response_model=KbOut)
async def get_knowledge_base(
    kb_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> KbOut:
    kb = await kb_service.get_owned_kb(db, kb_id, current_user.id)
    return KbOut.model_validate(kb)


@router.patch("/{kb_id}", response_model=KbOut)
async def update_knowledge_base(
    kb_id: UUID,
    payload: KbUpdate,
    db: DbSession,
    current_user: CurrentUser,
) -> KbOut:
    kb = await kb_service.get_owned_kb(db, kb_id, current_user.id)
    kb = await kb_service.update_kb(db, kb, payload)
    await db.commit()
    return KbOut.model_validate(kb)


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    kb_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    kb = await kb_service.get_owned_kb(db, kb_id, current_user.id)
    await kb_service.delete_kb(db, kb)
    await db.commit()
