from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, UnauthorizedError
from app.core.security import hash_password, verify_password
from app.models.user import User


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email.strip().lower()))
    return result.scalar_one_or_none()


async def register_user(
    db: AsyncSession,
    email: str,
    password: str,
    display_name: str | None,
) -> User:
    normalized_email = email.strip().lower()
    if await get_user_by_email(db, normalized_email) is not None:
        raise ConflictError("该邮箱已注册")

    user = User(
        email=normalized_email,
        password_hash=hash_password(password),
        display_name=display_name,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    user = await get_user_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        raise UnauthorizedError("邮箱或密码错误")
    if not user.is_active:
        raise UnauthorizedError("账号已被禁用")
    return user


async def store_refresh_token(
    redis: Redis,
    user_id: str,
    jti: str,
    ttl_seconds: int,
) -> None:
    await redis.set(f"refresh:{jti}", user_id, ex=ttl_seconds)


async def consume_refresh_token(redis: Redis, jti: str) -> str | None:
    """Consume a refresh jti atomically; a token can only be used once."""
    return await redis.getdel(f"refresh:{jti}")


async def revoke_refresh_token(redis: Redis, jti: str) -> None:
    await redis.delete(f"refresh:{jti}")
