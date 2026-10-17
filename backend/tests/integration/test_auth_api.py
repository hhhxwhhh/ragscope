import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

TEST_PASSWORD = "Passw0rd!123"

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"


async def test_register_returns_tokens_and_sets_http_only_refresh_cookie(
    client: AsyncClient,
) -> None:
    response = await client.post(
        REGISTER,
        json={"email": "Alice@Example.com", "password": TEST_PASSWORD, "display_name": "Alice"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["display_name"] == "Alice"
    assert "password" not in body["user"]

    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "refresh_token=" in set_cookie


async def test_duplicate_email_is_a_conflict(client: AsyncClient) -> None:
    payload = {"email": "dup@example.com", "password": TEST_PASSWORD}
    assert (await client.post(REGISTER, json=payload)).status_code == 201

    response = await client.post(REGISTER, json=payload)

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


async def test_login_and_me(client: AsyncClient) -> None:
    await client.post(REGISTER, json={"email": "bob@example.com", "password": TEST_PASSWORD})

    login = await client.post(
        LOGIN, json={"email": "BOB@example.com", "password": TEST_PASSWORD}
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = await client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "bob@example.com"

    unauthenticated = await client.get(ME)
    assert unauthenticated.status_code == 401


async def test_wrong_password_is_rejected_and_does_not_leak_existence(
    client: AsyncClient,
) -> None:
    await client.post(REGISTER, json={"email": "carol@example.com", "password": TEST_PASSWORD})

    response = await client.post(
        LOGIN, json={"email": "carol@example.com", "password": "not-the-password"}
    )
    unknown = await client.post(
        LOGIN, json={"email": "nobody@example.com", "password": "not-the-password"}
    )

    assert response.status_code == 401
    assert unknown.status_code == 401
    assert response.json() == unknown.json()


async def test_refresh_token_rotation_allows_each_token_only_once(
    client: AsyncClient,
) -> None:
    await client.post(REGISTER, json={"email": "dave@example.com", "password": TEST_PASSWORD})
    old_refresh = client.cookies.get("refresh_token")
    assert old_refresh

    first = await client.post(REFRESH)
    assert first.status_code == 200
    new_refresh = client.cookies.get("refresh_token")
    assert new_refresh and new_refresh != old_refresh

    # Replaying the consumed token must fail even though it is still valid JWT-wise.
    client.cookies.clear()
    client.cookies.set(
        "refresh_token", old_refresh, domain="testserver", path="/api/v1/auth"
    )
    replay = await client.post(REFRESH)
    assert replay.status_code == 401
    assert replay.json()["code"] == "unauthorized"


async def test_refresh_without_cookie_and_after_logout_is_rejected(
    client: AsyncClient,
) -> None:
    assert (await client.post(REFRESH)).status_code == 401

    await client.post(REGISTER, json={"email": "erin@example.com", "password": TEST_PASSWORD})
    logout = await client.post(LOGOUT)
    assert logout.status_code == 204

    assert (await client.post(REFRESH)).status_code == 401
