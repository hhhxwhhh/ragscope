from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnauthorizedError
from app.core.security import decode_token
from app.db.session import SessionLocal, get_db
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


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
