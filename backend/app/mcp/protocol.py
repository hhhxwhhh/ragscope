"""Model Context Protocol primitives.

MCP is JSON-RPC 2.0 with a small, versioned handshake. This module implements the
parts a tool server needs — request dispatch, error objects and the initialise
handshake — without pulling in the reference SDK, so the server can run in an
offline environment and the transport stays readable in one file.

Spec references: JSON-RPC 2.0 error codes, MCP revision ``2025-06-18``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "ragscope-mcp"
SERVER_VERSION = "0.1.0"

# JSON-RPC 2.0 reserved codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# MCP-specific codes use the implementation-defined range.
UNAUTHORIZED = -32001


@dataclass
class RpcError(Exception):
    code: int
    message: str
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            payload["data"] = self.data
        return payload


def success(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def failure(request_id: Any, error: RpcError) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": error.to_dict()}


def initialise_result(client_protocol: str | None) -> dict[str, Any]:
    """Reply to ``initialize``.

    Version negotiation is one line in the spec but easy to get wrong: the server
    answers with the client's version when it supports it, otherwise with its own
    latest, and the client decides whether to continue.
    """
    negotiated = (
        client_protocol
        if client_protocol in SUPPORTED_VERSIONS
        else PROTOCOL_VERSION
    )
    return {
        "protocolVersion": negotiated,
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"subscribe": False, "listChanged": False},
        },
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": (
            "RAGScope 知识库工具。先用 list_knowledge_bases 找到 kb_id，"
            "再用 search_knowledge_base 检索证据，需要引用式回答时调用 "
            "ask_knowledge_base。回答必须来自工具返回的内容。"
        ),
    }


def text_content(text: str, *, structured: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a ``tools/call`` result.

    MCP clients render ``content``; ``structuredContent`` lets a programmatic
    caller skip re-parsing the human-readable text.
    """
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "isError": False,
    }
    if structured is not None:
        result["structuredContent"] = structured
    return result


def error_content(message: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }
    if details:
        result["structuredContent"] = details
    return result


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
