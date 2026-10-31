from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_token, looks_like_api_key
from app.db.session import SessionLocal, get_db
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)

ALL_SCOPES = ("kb:read", "kb:write", "chat", "admin")


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if credentials is None:
        raise UnauthorizedError("缺少认证信息")

    payload = decode_token(credentials.credentials, expected_type="access")

    try:
        user_id = UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("令牌内容无效") from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("用户不存在或已禁用")

    return user


async def get_current_user_stateless(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> User:
    """Authenticate with a short-lived session.

    SSE endpoints must not hold a request-scoped DB session for the whole
    stream, so they use this dependency instead of ``get_current_user``.
    """
    if credentials is None:
        raise UnauthorizedError("缺少认证信息")

    payload = decode_token(credentials.credentials, expected_type="access")
    try:
        user_id = UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("令牌内容无效") from exc

    async with SessionLocal() as db:
        user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("用户不存在或已禁用")
    return user


@dataclass(frozen=True)
class Principal:
    """The caller behind a request.

    Both humans (JWT) and machines (API key) resolve to the same shape, so
    downstream code depends on "who is calling and what may they do" rather than
    on the credential type. That keeps authorization out of the handlers.
    """

    user: User
    kind: str  # "user" | "api_key"
    scopes: frozenset[str]
    api_key_id: UUID | None = None

    @property
    def user_id(self) -> UUID:
        return self.user.id


async def get_principal(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Principal:
    """Authenticate a JWT or an API key.

    API keys are recognised by their `rk_` prefix, which means a machine caller
    never needs a second header or a second endpoint family.
    """
    if credentials is None:
        raise UnauthorizedError("缺少认证信息")

    token = credentials.credentials
    if looks_like_api_key(token):
        from app.services import api_key_service

        user, key = await api_key_service.resolve(db, token)
        return Principal(
            user=user,
            kind="api_key",
            scopes=frozenset(key.scopes or []),
            api_key_id=key.id,
        )

    payload = decode_token(token, expected_type="access")
    try:
        user_id = UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("令牌内容无效") from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("用户不存在或已禁用")
    # A logged-in human owns the knowledge bases they see through the UI, so the
    # JWT principal carries every scope; per-KB ownership is still enforced by
    # the existing service layer.
    return Principal(user=user, kind="user", scopes=frozenset(ALL_SCOPES))


def require_scope(scope: str):
    """Dependency factory: enforce one scope for API-key callers."""

    async def _dependency(
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Principal:
        if principal.kind == "api_key" and scope not in principal.scopes:
            raise ForbiddenError(f"API Key 缺少 {scope} 权限")
        return principal

    return _dependency
