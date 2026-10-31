from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from secrets import token_urlsafe
from uuid import UUID, uuid4

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings
from app.core.errors import UnauthorizedError

settings = get_settings()
_password_hash = PasswordHash.recommended()

API_KEY_PREFIX = "rk_"
API_KEY_PREFIX_LENGTH = 20


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hash.verify(password, password_hash)


def _create_token(
    subject: UUID,
    token_type: str,
    expires_delta: timedelta,
) -> tuple[str, str, datetime]:
    now = datetime.now(UTC)
    expires_at = now + expires_delta
    jti = str(uuid4())
    payload = {
        "sub": str(subject),
        "type": token_type,
        "jti": jti,
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    return token, jti, expires_at


def create_access_token(user_id: UUID) -> tuple[str, str, datetime]:
    return _create_token(
        user_id,
        "access",
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: UUID) -> tuple[str, str, datetime]:
    return _create_token(
        user_id,
        "refresh",
        timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("登录状态无效或已过期") from exc

    if payload.get("type") != expected_type:
        raise UnauthorizedError("令牌类型不正确")

    return payload


def generate_api_key() -> tuple[str, str, str]:
    """Create an API key and return ``(plaintext, prefix, sha256_hash)``.

    The plaintext is shown to the user exactly once. Only the hash is stored,
    exactly like a password: a database leak must not hand over working
    credentials. A random 32-byte body gives ~256 bits of entropy, which is why a
    plain SHA-256 is sufficient here — there is nothing to brute-force, so the
    deliberately slow Argon2 used for user passwords would only add latency to
    every API call.
    """
    plaintext = f"{API_KEY_PREFIX}{token_urlsafe(32)}"
    return plaintext, plaintext[:API_KEY_PREFIX_LENGTH], hash_api_key(plaintext)


def hash_api_key(plaintext: str) -> str:
    return sha256(plaintext.encode("utf-8")).hexdigest()


def api_key_matches(plaintext: str, stored_hash: str) -> bool:
    """Constant-time comparison to avoid leaking the hash through timing."""
    return compare_digest(hash_api_key(plaintext), stored_hash)


def looks_like_api_key(token: str) -> bool:
    return token.startswith(API_KEY_PREFIX) and len(token) > API_KEY_PREFIX_LENGTH
