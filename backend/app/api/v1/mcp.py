"""MCP over HTTP (JSON-RPC 2.0 over POST).

The stdio transport is what a desktop client uses; this endpoint is what a
server-side integration uses. Both share ``app.mcp.server.handle_message``, so a
tool behaves identically in both places and there is only one dispatcher to test.

Streamable HTTP allows SSE responses. We answer with plain JSON: every tool here
is request/response, and holding an SSE stream open for a 200 ms search would only
add buffering problems behind nginx.
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Principal, require_scope
from app.db.session import get_db
from app.mcp.server import handle_message
from app.mcp.tools import TOOLS

router = APIRouter(prefix="/mcp", tags=["mcp"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
McpPrincipal = Annotated[Principal, Depends(require_scope("kb:read"))]


@router.get("/tools")
async def list_tools(principal: McpPrincipal) -> dict[str, Any]:
    """Human-readable discovery, also handy for a health check from a client."""
    return {
        "server": {"name": "ragscope-mcp", "version": "0.1.0"},
        "protocol": "2025-06-18",
        "tools": [
            {"name": tool.name, "description": tool.description, "input": tool.input_schema}
            for tool in TOOLS
        ],
        "caller": {"kind": principal.kind, "scopes": sorted(principal.scopes)},
    }


@router.post("")
async def mcp_endpoint(
    payload: dict[str, Any] | list[dict[str, Any]],
    db: DbSession,
    principal: McpPrincipal,
) -> Response:
    batch = payload if isinstance(payload, list) else [payload]
    responses = []
    for message in batch:
        response = await handle_message(message, principal=principal, db=db)
        if response is not None:
            responses.append(response)

    if not responses:
        return Response(status_code=status.HTTP_202_ACCEPTED)

    body = responses if isinstance(payload, list) else responses[0]
    return Response(
        content=json.dumps(body, ensure_ascii=False),
        media_type="application/json",
    )
