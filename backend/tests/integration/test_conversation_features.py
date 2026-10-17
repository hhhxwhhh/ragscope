from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _create_kb_and_conversation(
    client: AsyncClient,
    headers: dict[str, str],
    title: str = "X1 续航",
) -> tuple[str, str]:
    kb = await client.post(
        "/api/v1/kbs",
        json={"name": "会话测试库", "chunk_size": 400, "chunk_overlap": 60},
        headers=headers,
    )
    kb_id = kb.json()["id"]
    conversation = await client.post(
        f"/api/v1/kbs/{kb_id}/conversations", json={"title": title}, headers=headers
    )
    assert conversation.status_code == 201, conversation.text
    return kb_id, conversation.json()["id"]


async def test_conversation_rename_and_search(client: AsyncClient, register: Any) -> None:
    _, headers = await register()
    kb_id, conversation_id = await _create_kb_and_conversation(client, headers)
    await _create_kb_and_conversation(client, headers, title="售后政策")

    renamed = await client.patch(
        f"/api/v1/conversations/{conversation_id}",
        json={"title": "X2 与 X1 对比"},
        headers=headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "X2 与 X1 对比"

    matched = await client.get(
        f"/api/v1/kbs/{kb_id}/conversations", params={"q": "X2"}, headers=headers
    )
    assert matched.status_code == 200
    assert matched.json()["total"] == 1
    assert matched.json()["items"][0]["id"] == conversation_id

    missed = await client.get(
        f"/api/v1/kbs/{kb_id}/conversations", params={"q": "不存在"}, headers=headers
    )
    assert missed.json()["total"] == 0


async def test_conversation_export_markdown(client: AsyncClient, register: Any) -> None:
    _, headers = await register()
    _, conversation_id = await _create_kb_and_conversation(client, headers)

    response = await client.get(
        f"/api/v1/conversations/{conversation_id}/export", headers=headers
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "attachment" in response.headers["content-disposition"]
    assert "# X1 续航" in response.text


async def test_conversation_endpoints_hide_other_users_resources(
    client: AsyncClient, register: Any
) -> None:
    _, headers = await register()
    _, conversation_id = await _create_kb_and_conversation(client, headers)
    _, other_headers = await register()

    for method, url in (
        ("patch", f"/api/v1/conversations/{conversation_id}"),
        ("get", f"/api/v1/conversations/{conversation_id}/export"),
        ("delete", f"/api/v1/conversations/{conversation_id}"),
    ):
        kwargs = {"headers": other_headers}
        if method == "patch":
            kwargs["json"] = {"title": "劫持"}
        response = await getattr(client, method)(url, **kwargs)
        assert response.status_code == 404, (method, url, response.text)
