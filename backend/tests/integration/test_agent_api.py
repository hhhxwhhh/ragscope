from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient

from app.api.v1 import chat as chat_api
from app.core.config import get_settings
from app.rag import llm as llm_module

pytestmark = pytest.mark.integration


@pytest.fixture
def no_key(monkeypatch: pytest.MonkeyPatch):
    """Make the real client factory fail with its production 503 path."""
    real = get_settings()
    fake = SimpleNamespace(
        agent_enabled=True,
        deepseek_api_key="",
        deepseek_base_url=real.deepseek_base_url,
        deepseek_model=real.deepseek_model,
        llm_timeout_seconds=real.llm_timeout_seconds,
    )
    monkeypatch.setattr(chat_api, "get_settings", lambda: fake)
    monkeypatch.setattr(llm_module, "get_settings", lambda: fake)
    llm_module.get_llm_client.cache_clear()
    yield
    llm_module.get_llm_client.cache_clear()


async def _create_kb(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(
        "/api/v1/kbs",
        json={"name": "Agent 测试库", "chunk_size": 400, "chunk_overlap": 60},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_agent_chat_returns_503_without_llm_key(
    client: AsyncClient, register: Any, no_key: None
) -> None:
    _, headers = await register()
    kb_id = await _create_kb(client, headers)

    response = await client.post(
        f"/api/v1/kbs/{kb_id}/chat",
        json={"question": "X1 的续航是多少？", "mode": "agent"},
        headers=headers,
    )

    assert response.status_code == 503
    assert "DEEPSEEK_API_KEY" in response.json()["detail"]


async def test_agent_stream_returns_503_without_llm_key(
    client: AsyncClient, register: Any, no_key: None
) -> None:
    _, headers = await register()
    kb_id = await _create_kb(client, headers)

    response = await client.post(
        f"/api/v1/kbs/{kb_id}/chat/stream",
        json={"question": "X1 的续航是多少？", "mode": "agent"},
        headers=headers,
    )

    assert response.status_code == 503


async def test_unknown_chat_mode_is_rejected(client: AsyncClient, register: Any) -> None:
    _, headers = await register()
    kb_id = await _create_kb(client, headers)

    response = await client.post(
        f"/api/v1/kbs/{kb_id}/chat",
        json={"question": "问题", "mode": "graph"},
        headers=headers,
    )

    assert response.status_code == 422
