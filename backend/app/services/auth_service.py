from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ConflictError, RateLimitedError, UnauthorizedError
from app.core.security import hash_password, verify_password
from app.models.user import User

settings = get_settings()


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


# GET + DEL as one script. The reason this is not ``GETDEL``: that command only
# exists on Redis >= 6.2, and running against an older server (a local Redis 5,
# a managed instance pinned to an old version) turned every refresh into a
# 500 instead of a rotation. A script keeps the read-then-delete atomic — which
# is the entire point here, since a concurrent refresh must not be able to read
# the same jti twice — and works on any server with EVAL (2.6+).
_CONSUME_REFRESH_SCRIPT = """
local value = redis.call('GET', KEYS[1])
if value then redis.call('DEL', KEYS[1]) end
return value
"""


async def consume_refresh_token(redis: Redis, jti: str) -> str | None:
    """Consume a refresh jti atomically; a token can only be used once."""
    result = await redis.eval(_CONSUME_REFRESH_SCRIPT, 1, f"refresh:{jti}")
    if result is None:
        return None
    if isinstance(result, bytes):
        return result.decode("utf-8")
    return str(result)


async def revoke_refresh_token(redis: Redis, jti: str) -> None:
    await redis.delete(f"refresh:{jti}")


# ---------------------------------------------------------------------------
# Login throttling
# ---------------------------------------------------------------------------

LOGIN_FAIL_PREFIX = "login:fail"


def _login_fail_keys(email: str, client_ip: str) -> tuple[str, str]:
    normalized_email = email.strip().lower()
    return (
        f"{LOGIN_FAIL_PREFIX}:email:{normalized_email}",
        f"{LOGIN_FAIL_PREFIX}:ip:{client_ip or 'unknown'}",
    )


async def ensure_login_allowed(redis: Redis, email: str, client_ip: str) -> None:
    """Reject a login attempt once either the account or the IP is over budget.

    A fixed window is enough here: failed attempts are cheap to count, and the
    goal is to slow credential stuffing rather than enforce exact quotas.
    """
    for key in _login_fail_keys(email, client_ip):
        raw = await redis.get(key)
        if raw is not None and int(raw) >= settings.login_max_attempts:
            minutes = max(settings.login_attempt_window_seconds // 60, 1)
            raise RateLimitedError(f"登录失败次数过多，请 {minutes} 分钟后再试")


async def record_login_failure(redis: Redis, email: str, client_ip: str) -> None:
    window = settings.login_attempt_window_seconds
    async with redis.pipeline(transaction=True) as pipe:
        for key in _login_fail_keys(email, client_ip):
            pipe.incr(key)
            pipe.expire(key, window)
        await pipe.execute()


async def clear_login_failures(redis: Redis, email: str, client_ip: str) -> None:
    email_key, _ = _login_fail_keys(email, client_ip)
    # Do not clear the IP counter on success: a valid account must not reset
    # the budget an attacker shares from the same address.
    await redis.delete(email_key)
