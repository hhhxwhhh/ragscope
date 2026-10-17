from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from app.core.config import get_settings
from app.core.errors import UnauthorizedError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

settings = get_settings()


def test_password_hash_roundtrip() -> None:
    password_hash = hash_password("Passw0rd!123")

    assert password_hash != "Passw0rd!123"
    assert verify_password("Passw0rd!123", password_hash) is True
    assert verify_password("wrong-password", password_hash) is False


def test_access_token_carries_subject_and_type_but_no_secrets() -> None:
    user_id = uuid4()
    token, jti, expires_at = create_access_token(user_id)

    payload = decode_token(token, expected_type="access")

    assert payload["sub"] == str(user_id)
    assert payload["type"] == "access"
    assert payload["jti"] == jti
    assert expires_at > datetime.now(UTC)
    assert "password" not in payload
    assert set(payload) == {"sub", "type", "jti", "iat", "exp"}


def test_token_type_is_enforced() -> None:
    refresh_token, _, _ = create_refresh_token(uuid4())

    with pytest.raises(UnauthorizedError):
        decode_token(refresh_token, expected_type="access")


def test_tampered_or_expired_token_is_rejected() -> None:
    token, _, _ = create_access_token(uuid4())

    with pytest.raises(UnauthorizedError):
        decode_token(token + "x", expected_type="access")

    expired = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "jti": str(uuid4()),
            "exp": datetime.now(UTC) - timedelta(seconds=1),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    with pytest.raises(UnauthorizedError):
        decode_token(expired, expected_type="access")
