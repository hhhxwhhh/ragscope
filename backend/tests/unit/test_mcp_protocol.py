"""MCP dispatcher tests that need no database: handshake, discovery, errors."""

import pytest

from app.mcp.protocol import PROTOCOL_VERSION
from app.mcp.server import handle_message


async def dispatch(message):
    return await handle_message(message, principal=None, db=None)


async def test_initialize_negotiates_client_protocol_version():
    response = await dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}},
        }
    )

    assert response["id"] == 1
    result = response["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"]["tools"]["listChanged"] is False
    assert result["serverInfo"]["name"] == "ragscope-mcp"


async def test_initialize_falls_back_to_latest_supported_version():
    response = await dispatch(
        {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999"}}
    )
    assert response["result"]["protocolVersion"] == PROTOCOL_VERSION


async def test_notifications_never_get_a_response():
    assert (
        await dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    )


async def test_tools_list_declares_json_schema():
    response = await dispatch({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = {tool["name"]: tool for tool in response["result"]["tools"]}

    assert {"list_knowledge_bases", "search_knowledge_base", "ask_knowledge_base"} <= set(tools)
    schema = tools["search_knowledge_base"]["inputSchema"]
    assert schema["required"] == ["kb_id", "query"]
    assert schema["properties"]["top_k"]["maximum"] == 10


async def test_unknown_method_returns_method_not_found():
    response = await dispatch({"jsonrpc": "2.0", "id": 4, "method": "does/not/exist"})
    assert response["error"]["code"] == -32601


async def test_bad_envelope_is_invalid_request():
    response = await dispatch({"id": 5, "method": "ping"})
    assert response["error"]["code"] == -32600


async def test_tool_call_without_principal_is_rejected():
    response = await dispatch(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "list_knowledge_bases", "arguments": {}},
        }
    )
    assert response["error"]["code"] == -32001


async def test_ping_is_supported():
    response = await dispatch({"jsonrpc": "2.0", "id": 7, "method": "ping"})
    assert response["result"] == {}


def test_unknown_tool_reports_error_content():
    import asyncio

    from app.mcp.tools import call_tool

    class FakeCtx:
        db = None
        principal = None

    result = asyncio.run(call_tool(FakeCtx(), "nope", {}))
    assert result["isError"] is True


@pytest.mark.parametrize("method", ["resources/list", "prompts/list"])
async def test_empty_capability_lists(method):
    response = await dispatch({"jsonrpc": "2.0", "id": 8, "method": method})
    assert "result" in response
