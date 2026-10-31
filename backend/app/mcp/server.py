"""MCP server: stdio transport plus a reusable message dispatcher.

Run it with an API key::

    set RAGSCOPE_API_KEY=rk_...
    python -m app.mcp.server

Then register it in any MCP client, e.g. Claude Desktop / Cursor:

.. code-block:: json

    {
      "mcpServers": {
        "ragscope": {
          "command": "python",
          "args": ["-m", "app.mcp.server"],
          "env": {"RAGSCOPE_API_KEY": "rk_..."}
        }
      }
    }

The same dispatcher powers ``POST /api/v1/mcp`` so a remote client can talk to a
deployed instance without a local Python environment.

Transport notes:

* stdio is newline-delimited JSON-RPC; nothing else may be written to stdout, so
  logging is redirected to stderr before the loop starts.
* Notifications (messages without an ``id``) never get a response.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.deps import Principal
from app.db.session import SessionLocal
from app.mcp.protocol import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PROTOCOL_VERSION,
    RpcError,
    failure,
    initialise_result,
    success,
)
from app.mcp.tools import TOOLS, ToolContext, call_tool
from app.services import api_key_service

logger = logging.getLogger(__name__)

async def handle_message(
    message: dict[str, Any],
    *,
    principal: Principal | None,
    db: AsyncSession | None,
) -> dict[str, Any] | None:
    """Dispatch one JSON-RPC message. Returns ``None`` for notifications."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return failure(message.get("id") if isinstance(message, dict) else None,
                       RpcError(INVALID_REQUEST, "非法 JSON-RPC 请求"))

    method = message.get("method")
    request_id = message.get("id")
    is_notification = "id" not in message
    params = message.get("params") or {}

    try:
        if method == "initialize":
            client_version = params.get("protocolVersion")
            return success(request_id, initialise_result(client_version))

        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None

        if method == "ping":
            return success(request_id, {})

        if method == "tools/list":
            return success(request_id, {"tools": [tool.to_dict() for tool in TOOLS]})

        if method == "resources/list":
            return success(request_id, {"resources": []})

        if method == "prompts/list":
            return success(request_id, {"prompts": []})

        if method == "tools/call":
            if principal is None or db is None:
                return failure(request_id, RpcError(-32001, "未认证：需要 RAGSCOPE_API_KEY"))
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return failure(request_id, RpcError(-32602, "arguments 必须是对象"))
            started = time.perf_counter()
            result = await call_tool(ToolContext(db=db, principal=principal), name, arguments)
            metrics.observe_agent_tool(
                f"mcp:{name}", (time.perf_counter() - started) * 1000
            )
            return success(request_id, result)

        if is_notification:
            return None
        return failure(request_id, RpcError(METHOD_NOT_FOUND, f"未知方法：{method}"))
    except RpcError as exc:
        return failure(request_id, exc)
    except Exception as exc:  # pragma: no cover - transport level safety net
        logger.exception("mcp dispatch failed method=%s", method)
        return failure(request_id, RpcError(INTERNAL_ERROR, f"内部错误：{exc.__class__.__name__}"))


async def _resolve_principal(key: str) -> Principal:
    async with SessionLocal() as db:
        user, record = await api_key_service.resolve(db, key)
        return Principal(
            user=user,
            kind="api_key",
            scopes=frozenset(record.scopes or []),
            api_key_id=record.id,
        )


def _configure_stdio_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


async def _read_line() -> str:
    return await asyncio.to_thread(sys.stdin.readline)


async def serve_stdio() -> int:
    _configure_stdio_logging()
    key = os.environ.get("RAGSCOPE_API_KEY", "").strip()
    if not key:
        print(
            "缺少 RAGSCOPE_API_KEY。请在 Web 端「API Key」页面创建一个带 kb:read/chat 权限的密钥。",
            file=sys.stderr,
        )
        return 2

    try:
        principal = await _resolve_principal(key)
    except Exception as exc:
        print(f"API Key 校验失败：{exc}", file=sys.stderr)
        return 3

    print(
        f"{PROTOCOL_VERSION} ready · server=ragscope-mcp · user={principal.user.email} "
        f"· scopes={sorted(principal.scopes)}",
        file=sys.stderr,
    )

    while True:
        line = (await _read_line()).strip()
        if not line:
            break
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps(failure(None, RpcError(-32700, "JSON 解析失败"))), flush=True)
            continue

        # A batch is allowed by JSON-RPC 2.0; MCP clients rarely use it.
        batch = message if isinstance(message, list) else None
        requests = message if batch is not None else [message]
        responses: list[Any] = []
        for request in requests:
            async with SessionLocal() as session:
                response = await handle_message(request, principal=principal, db=session)
            if response is not None:
                responses.append(response)

        if not responses:
            continue
        payload = responses if batch is not None else responses[0]
        print(json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> None:
    try:
        raise SystemExit(asyncio.run(serve_stdio()))
    except KeyboardInterrupt:  # pragma: no cover
        raise SystemExit(0) from None


if __name__ == "__main__":  # pragma: no cover
    main()
