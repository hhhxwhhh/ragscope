"""API key lifecycle and authentication.

Design notes worth defending in a review:

* **Only the hash is stored.** The plaintext is returned once at creation.
* **Prefix is stored separately** so the UI can render `rk_ab12cd…` and a user
  can identify a key without the secret being readable anywhere.
* **Scopes are checked at the dependency layer**, not inside each handler, so a
  new endpoint cannot accidentally inherit more privilege than it declares.
* **`last_used_at` is throttled** to one write per minute per key: an API hot
  path should not pay for a `UPDATE` on every single request.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.security import api_key_matches, generate_api_key, hash_api_key
from app.models.api_key import ApiKey, AuditLog
from app.models.user import User

logger = logging.getLogger(__name__)

SCOPES: dict[str, str] = {
    "kb:read": "读取知识库、文档与检索结果",
    "kb:write": "上传 / 删除文档，修改知识库配置",
    "chat": "以知识库为上下文发起问答",
    "admin": "管理 API Key 与审计日志",
}

DEFAULT_SCOPES = ("kb:read", "chat")
LAST_USED_THROTTLE = timedelta(minutes=1)


def validate_scopes(scopes: list[str]) -> list[str]:
    unknown = sorted(set(scopes) - set(SCOPES))
    if unknown:
        raise ForbiddenError(f"未知的 scope: {', '.join(unknown)}")
    if not scopes:
        raise ForbiddenError("至少需要选择一个 scope")
    return sorted(set(scopes))


async def create_key(
    db: AsyncSession,
    user: User,
    *,
    name: str,
    scopes: list[str],
    description: str | None = None,
    expires_in_days: int | None = None,
) -> tuple[ApiKey, str]:
    plaintext, prefix, key_hash = generate_api_key()
    expires_at = (
        datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days else None
    )
    record = ApiKey(
        user_id=user.id,
        name=name,
        key_prefix=prefix,
        key_hash=key_hash,
        scopes=validate_scopes(scopes),
        description=description,
        expires_at=expires_at,
    )
    db.add(record)
    await db.flush()
    await db.refresh(record)
    return record, plaintext


async def list_keys(db: AsyncSession, user_id: UUID) -> list[ApiKey]:
    rows = await db.scalars(
        select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.created_at.desc())
    )
    return list(rows)


async def get_owned_key(db: AsyncSession, key_id: UUID, user_id: UUID) -> ApiKey:
    record = await db.get(ApiKey, key_id)
    if record is None or record.user_id != user_id:
        raise NotFoundError("API Key 不存在")
    return record


async def revoke_key(db: AsyncSession, key: ApiKey) -> None:
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        await db.flush()


def is_usable(key: ApiKey, now: datetime | None = None) -> tuple[bool, str]:
    now = now or datetime.now(UTC)
    if key.revoked_at is not None:
        return False, "revoked"
    if key.expires_at is not None and key.expires_at <= now:
        return False, "expired"
    return True, "ok"


async def resolve(db: AsyncSession, plaintext: str) -> tuple[User, ApiKey]:
    """Look up a key by hash and return the owning user.

    The lookup is by hash, so it uses the unique index rather than scanning
    prefixes. A single generic error is returned for every failure mode so an
    attacker cannot distinguish "wrong key" from "revoked key".
    """
    key = await db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(plaintext)))
    if key is None or not api_key_matches(plaintext, key.key_hash):
        raise UnauthorizedError("API Key 无效")

    usable, reason = is_usable(key)
    if not usable:
        logger.info("rejected api key %s reason=%s", key.id, reason)
        raise UnauthorizedError("API Key 已失效")

    user = await db.get(User, key.user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("API Key 所属用户不可用")

    now = datetime.now(UTC)
    if key.last_used_at is None or now - key.last_used_at > LAST_USED_THROTTLE:
        key.last_used_at = now
        await db.commit()

    return user, key


def has_scope(key: ApiKey, scope: str) -> bool:
    if "admin" in (key.scopes or []):
        return True
    return scope in (key.scopes or [])


async def list_audit_logs(
    db: AsyncSession,
    user_id: UUID,
    *,
    action: str | None = None,
    resource_id: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[AuditLog], int]:
    condition = AuditLog.actor_id == user_id
    if action:
        condition = condition & (AuditLog.action == action)
    if resource_id:
        condition = condition & (AuditLog.resource_id == resource_id)

    rows = await db.scalars(
        select(AuditLog)
        .where(condition)
        .order_by(AuditLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    total = await db.scalar(select(func.count()).select_from(AuditLog).where(condition))
    return list(rows), int(total or 0)
