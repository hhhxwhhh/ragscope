from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_current_user
from app.core.errors import UnauthorizedError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.db.redis import get_redis_client
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisClient = Annotated[Redis, Depends(get_redis_client)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def _set_refresh_cookie(response: Response, token: str, expires_at: datetime) -> None:
    max_age = int((expires_at - datetime.now(UTC)).total_seconds())
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=settings.app_env != "dev",
        samesite="lax",
        path=f"{settings.api_prefix}/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=f"{settings.api_prefix}/auth",
    )


def _client_ip(request: Request) -> str:
    """Use the proxy-provided address when behind Nginx, else the socket peer."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


async def _issue_tokens(
    redis: Redis,
    user: User,
    response: Response,
) -> TokenResponse:
    access_token, _, _ = create_access_token(user.id)
    refresh_token, refresh_jti, refresh_expires_at = create_refresh_token(user.id)
    ttl = int((refresh_expires_at - datetime.now(UTC)).total_seconds())

    await auth_service.store_refresh_token(redis, str(user.id), refresh_jti, ttl)
    _set_refresh_cookie(response, refresh_token, refresh_expires_at)

    return TokenResponse(
        access_token=access_token,
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    response: Response,
    db: DbSession,
    redis: RedisClient,
) -> TokenResponse:
    user = await auth_service.register_user(
        db,
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
    )
    await db.commit()
    return await _issue_tokens(redis, user, response)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    redis: RedisClient,
) -> TokenResponse:
    client_ip = _client_ip(request)
    await auth_service.ensure_login_allowed(redis, payload.email, client_ip)
    try:
        user = await auth_service.authenticate_user(db, payload.email, payload.password)
    except UnauthorizedError:
        await auth_service.record_login_failure(redis, payload.email, client_ip)
        raise
    await auth_service.clear_login_failures(redis, payload.email, client_ip)
    return await _issue_tokens(redis, user, response)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    response: Response,
    db: DbSession,
    redis: RedisClient,
    refresh_token: Annotated[str | None, Cookie(alias=settings.refresh_cookie_name)] = None,
) -> TokenResponse:
    if not refresh_token:
        raise UnauthorizedError("缺少刷新令牌")

    payload = decode_token(refresh_token, expected_type="refresh")
    jti = payload.get("jti")
    subject = payload.get("sub")
    if not jti or not subject:
        raise UnauthorizedError("刷新令牌内容无效")

    # Rotation: each refresh token is valid exactly once.
    stored_user_id = await auth_service.consume_refresh_token(redis, jti)
    if stored_user_id is None or stored_user_id != subject:
        raise UnauthorizedError("刷新令牌已失效，请重新登录")

    try:
        user_id = UUID(subject)
    except ValueError as exc:
        raise UnauthorizedError("刷新令牌内容无效") from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("用户不存在或已禁用")

    return await _issue_tokens(redis, user, response)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    redis: RedisClient,
    refresh_token: Annotated[str | None, Cookie(alias=settings.refresh_cookie_name)] = None,
) -> Response:
    if refresh_token:
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
        except UnauthorizedError:
            payload = {}
        jti = payload.get("jti")
        if jti:
            await auth_service.revoke_refresh_token(redis, jti)

    _clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut)
async def me(current_user: CurrentUser) -> User:
    return current_user
