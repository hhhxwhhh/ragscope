from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreatedOut, ApiKeyOut, AuditLogOut
from app.schemas.common import Page
from app.services import api_key_service, audit_service

router = APIRouter(prefix="/api-keys", tags=["api-keys"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("/scopes")
async def list_scopes(current_user: CurrentUser) -> dict[str, str]:
    return api_key_service.SCOPES


@router.get("", response_model=Page[ApiKeyOut])
async def list_api_keys(
    db: DbSession,
    current_user: CurrentUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[ApiKeyOut]:
    keys = await api_key_service.list_keys(db, current_user.id)
    items = []
    for key in keys[offset : offset + limit]:
        usable, _ = api_key_service.is_usable(key)
        items.append(
            ApiKeyOut.model_validate(key).model_copy(
                update={"usable": usable, "scopes": list(key.scopes or [])}
            )
        )
    return Page[ApiKeyOut](items=items, total=len(keys), offset=offset, limit=limit)


@router.post("", response_model=ApiKeyCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: ApiKeyCreate,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> ApiKeyCreatedOut:
    key, plaintext = await api_key_service.create_key(
        db,
        current_user,
        name=payload.name,
        scopes=payload.scopes,
        description=payload.description,
        expires_in_days=payload.expires_in_days,
    )
    await audit_service.record(
        db,
        action="api_key.create",
        actor=current_user,
        resource_type="api_key",
        resource_id=key.id,
        detail={"name": key.name, "scopes": key.scopes, "expires_in_days": payload.expires_in_days},
        request=request,
    )
    await db.commit()
    return ApiKeyCreatedOut(
        **ApiKeyOut.model_validate(key).model_dump(exclude={"usable"}),
        key=plaintext,
        usable=True,
    )


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    from uuid import UUID

    try:
        parsed = UUID(key_id)
    except ValueError:
        from app.core.errors import NotFoundError

        raise NotFoundError("API Key 不存在") from None

    key = await api_key_service.get_owned_key(db, parsed, current_user.id)
    await api_key_service.revoke_key(db, key)
    await audit_service.record(
        db,
        action="api_key.revoke",
        actor=current_user,
        resource_type="api_key",
        resource_id=key.id,
        detail={"name": key.name},
        request=request,
    )
    await db.commit()


@router.get("/{key_id}/audit", response_model=Page[AuditLogOut])
async def key_audit(
    key_id: str,
    db: DbSession,
    current_user: CurrentUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[AuditLogOut]:
    """Audit entries recorded for one key's lifecycle.

    Kept here (rather than a global audit console) so the story stays simple:
    every secret is traceable from the page where it is managed.
    """
    logs, total = await api_key_service.list_audit_logs(
        db, current_user.id, resource_id=key_id, offset=offset, limit=limit
    )
    return Page[AuditLogOut](
        items=[AuditLogOut.model_validate(row) for row in logs],
        total=total,
        offset=offset,
        limit=limit,
    )

