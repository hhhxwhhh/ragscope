from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import get_settings

pytestmark = pytest.mark.integration

LOGIN = "/api/v1/auth/login"
settings = get_settings()


async def test_login_is_throttled_by_email_and_ip(
    client: AsyncClient, register: Any
) -> None:
    email = "locked@example.com"
    await register(email=email)

    for _ in range(settings.login_max_attempts):
        response = await client.post(
            LOGIN, json={"email": email, "password": "definitely-not-it"}
        )
        assert response.status_code == 401

    blocked = await client.post(LOGIN, json={"email": email, "password": "definitely-not-it"})
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"

    # The correct password is also rejected while the window is open.
    still_blocked = await client.post(
        LOGIN, json={"email": email, "password": "Passw0rd!123"}
    )
    assert still_blocked.status_code == 429


async def test_successful_login_clears_the_email_counter(
    client: AsyncClient, register: Any
) -> None:
    email = "careful@example.com"
    await register(email=email)

    for _ in range(settings.login_max_attempts - 1):
        response = await client.post(LOGIN, json={"email": email, "password": "wrong-pass"})
        assert response.status_code == 401

    success = await client.post(LOGIN, json={"email": email, "password": "Passw0rd!123"})
    assert success.status_code == 200

    # Counter cleared: a fresh bad attempt is a 401 again, not a 429.
    again = await client.post(LOGIN, json={"email": email, "password": "wrong-pass"})
    assert again.status_code == 401
