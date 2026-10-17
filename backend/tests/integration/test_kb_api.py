from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

KBS = "/api/v1/kbs"


async def _create_kb(
    client: AsyncClient, headers: dict[str, str], name: str = "手册"
) -> dict[str, Any]:
    response = await client.post(
        KBS,
        json={"name": name, "description": "产品文档", "chunk_size": 400, "chunk_overlap": 60},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_kb_crud_roundtrip(client: AsyncClient, register: Any) -> None:
    _, headers = await register()

    created = await _create_kb(client, headers)
    kb_id = created["id"]
    assert created["index_version"] == 1
    assert created["chunk_size"] == 400

    listing = await client.get(KBS, headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    updated = await client.patch(
        f"{KBS}/{kb_id}", json={"name": "新手册", "chunk_size": 500}, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "新手册"

    deleted = await client.delete(f"{KBS}/{kb_id}", headers=headers)
    assert deleted.status_code == 204

    missing = await client.get(f"{KBS}/{kb_id}", headers=headers)
    assert missing.status_code == 404


async def test_other_users_resources_are_not_found(
    client: AsyncClient, register: Any
) -> None:
    _, alice_headers = await register(email="alice@example.com")
    kb = await _create_kb(client, alice_headers)
    _, bob_headers = await register(email="bob@example.com")

    read = await client.get(f"{KBS}/{kb['id']}", headers=bob_headers)
    delete = await client.delete(f"{KBS}/{kb['id']}", headers=bob_headers)
    listing = await client.get(KBS, headers=bob_headers)

    # 404 rather than 403: do not reveal that somebody else owns this id.
    assert read.status_code == 404
    assert read.json()["code"] == "not_found"
    assert delete.status_code == 404
    assert listing.json()["total"] == 0


async def test_requires_authentication(client: AsyncClient) -> None:
    response = await client.get(KBS)
    assert response.status_code == 401


async def test_rejects_invalid_chunk_parameters(client: AsyncClient, register: Any) -> None:
    _, headers = await register()

    response = await client.post(
        KBS,
        json={"name": "坏参数", "chunk_size": 200, "chunk_overlap": 200},
        headers=headers,
    )

    assert response.status_code == 422
