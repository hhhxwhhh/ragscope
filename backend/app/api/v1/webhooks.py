"""Webhook subscription management."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.errors import NotFoundError
from app.db.session import get_db
from app.models.user import User
from app.models.webhook import WebhookEndpoint
from app.schemas.webhook import WebhookCreate, WebhookOut, WebhookTestOut
from app.services import webhook_service

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("/events")
async def list_events(current_user: CurrentUser) -> dict[str, str]:
    return {
        "sync.completed": "数据源同步完成（含 added/updated/unchanged 统计）",
        "sync.failed": "数据源同步失败",
        "eval.completed": "评测运行完成（含指标）",
        "ingest.failed": "文档入库失败（解析/切块/embedding）",
    }


@router.get("", response_model=list[WebhookOut])
async def list_webhooks(db: DbSession, current_user: CurrentUser) -> list[WebhookOut]:
    rows = await webhook_service.list_endpoints(db, current_user.id)
    return [WebhookOut.model_validate(row) for row in rows]


@router.post("", response_model=WebhookOut, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    payload: WebhookCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> WebhookOut:
    endpoint = await webhook_service.create_endpoint(
        db, current_user.id, url=payload.url, events=list(payload.events)
    )
    await db.commit()
    return WebhookOut.model_validate(endpoint)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    endpoint = await db.get(WebhookEndpoint, webhook_id)
    if endpoint is None or endpoint.user_id != current_user.id:
        raise NotFoundError("Webhook 不存在")
    await webhook_service.delete_endpoint(db, endpoint)
    await db.commit()


@router.post("/{webhook_id}/test", response_model=WebhookTestOut)
async def test_webhook(
    webhook_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> WebhookTestOut:
    endpoint = await db.get(WebhookEndpoint, webhook_id)
    if endpoint is None or endpoint.user_id != current_user.id:
        raise NotFoundError("Webhook 不存在")
    result = await webhook_service.send_test(endpoint)
    return WebhookTestOut(**result)

