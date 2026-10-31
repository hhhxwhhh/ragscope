"""Append-only audit trail.

Audit writes are best-effort by design: an audit failure must never roll back the
business action that has already succeeded, but it must be *loud* — hence the
warning log with the full payload so the record can be replayed from logs.

The table is intentionally narrow and generic (``action`` + ``resource`` +
free-form ``detail``) so new features can record events without a migration.
"""

import logging
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import AuditLog
from app.models.user import User

logger = logging.getLogger(__name__)


def request_meta(request: Request | None) -> dict[str, str | None]:
    if request is None:
        return {"request_id": None, "ip_address": None}
    client = request.client
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return {
        "request_id": request.headers.get("X-Request-ID"),
        "ip_address": forwarded or (client.host if client else None),
    }


async def record(
    db: AsyncSession,
    *,
    action: str,
    actor: User | None = None,
    actor_id: UUID | None = None,
    actor_type: str = "user",
    resource_type: str | None = None,
    resource_id: str | UUID | None = None,
    detail: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    """Attach an audit row to the caller's transaction.

    The row is added to the session rather than committed immediately so it
    shares the business action's transaction: either both are visible or neither
    is. Callers that intentionally commit early (SSE endpoints) still get the row
    on their own commit.
    """
    try:
        meta = request_meta(request)
        db.add(
            AuditLog(
                actor_id=actor.id if actor is not None else actor_id,
                actor_type=actor_type,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id) if resource_id is not None else None,
                request_id=meta["request_id"],
                ip_address=meta["ip_address"],
                detail=detail or {},
            )
        )
    except Exception:  # pragma: no cover - defensive: auditing is never fatal
        logger.exception(
            "failed to write audit log action=%s resource=%s/%s detail=%s",
            action,
            resource_type,
            resource_id,
            detail,
        )
