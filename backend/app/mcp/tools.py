"""Tools exposed over MCP.

The tool surface is intentionally small and *read-only*: an assistant connected to
this server can explore and quote a knowledge base, but cannot upload, delete or
reconfigure anything. Write operations stay behind the human UI, where a mistake
is visible and reversible.

Every handler takes an explicit ``Principal`` instead of relying on ambient
state, so the same function can be called from stdio (one long-lived key) and from
the HTTP transport (one key per request) without behavioural drift.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.config import get_settings
from app.core.deps import Principal
from app.core.errors import AppError
from app.mcp.protocol import ToolSpec, error_content, text_content
from app.models.chunk import Chunk
from app.rag.answer import complete_answer
from app.rag.pipeline import RetrievalConfig, retrieve
from app.rag.prompt import REFUSAL_MESSAGE
from app.services import kb_service


@dataclass
class ToolContext:
    db: AsyncSession
    principal: Principal


def _short(text: str, limit: int = 500) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _chunk_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": str(candidate.get("chunk_id")),
        "document_id": str(candidate.get("document_id")),
        "filename": candidate.get("filename"),
        "chunk_index": candidate.get("chunk_index"),
        "heading_path": candidate.get("heading_path"),
        "page_number": candidate.get("page_number"),
        "content": candidate.get("content"),
        "score": candidate.get("rerank_score") or candidate.get("fused_score"),
    }


TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="list_knowledge_bases",
        description="列出当前 API Key 可访问的知识库（id、名称、文档数、索引版本）。",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="search_knowledge_base",
        description=(
            "在一个知识库中做混合检索（向量 + BM25 + RRF + 可选重排），返回最相关的片段。"
            "只读，不生成答案，适合需要自己组织证据的场景。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "kb_id": {
                    "type": "string",
                    "description": "知识库 UUID，可由 list_knowledge_bases 获得",
                },
                "query": {"type": "string", "minLength": 1, "description": "检索问题，尽量具体"},
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                    "description": "返回片段数",
                },
                "use_rerank": {
                    "type": "boolean",
                    "description": "是否使用 cross-encoder 重排，默认跟随知识库配置",
                },
            },
            "required": ["kb_id", "query"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="ask_knowledge_base",
        description=(
            "在一个知识库中检索并生成带 [n] 编号引用的答案。没有证据时直接拒答，"
            "不会凭模型记忆作答。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "kb_id": {"type": "string"},
                "question": {"type": "string", "minLength": 1},
                "prompt_variant": {
                    "type": "string",
                    "enum": ["grounded", "strict_citation", "structured"],
                    "description": "答案风格，默认 grounded",
                },
            },
            "required": ["kb_id", "question"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="get_document_outline",
        description="返回一篇文档的标题结构与页码分布，用于判断该读哪一段。",
        input_schema={
            "type": "object",
            "properties": {"document_id": {"type": "string"}},
            "required": ["document_id"],
            "additionalProperties": False,
        },
    ),
]


async def _list_knowledge_bases(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    rows, total = await kb_service.list_kbs(ctx.db, ctx.principal.user_id, 0, 100)
    payload = [
        {
            "id": str(kb.id),
            "name": kb.name,
            "description": kb.description,
            "index_version": kb.index_version,
        }
        for kb in rows
    ]
    if not payload:
        return error_content("当前账号下没有知识库，请先在 Web 端创建并上传文档。")
    lines = [f"共 {total} 个知识库："]
    lines += [f"- {item['name']}（id={item['id']}）{item['description'] or ''}" for item in payload]
    return text_content("\n".join(lines), structured={"knowledge_bases": payload})


async def _search(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    kb = await kb_service.get_owned_kb(ctx.db, UUID(args["kb_id"]), ctx.principal.user_id)
    config = RetrievalConfig.from_kb(kb)
    top_k = int(args.get("top_k") or 5)
    overrides: dict[str, Any] = {"final_top_k": top_k, "fusion_top_k": max(top_k * 3, 20)}
    if args.get("use_rerank") is not None:
        overrides["use_rerank"] = bool(args["use_rerank"])
    config = replace(config, **overrides)

    started = time.perf_counter()
    outcome = await retrieve(ctx.db, kb, args["query"], config=config)
    latency_ms = round((time.perf_counter() - started) * 1000, 1)

    if not outcome.candidates:
        return text_content(
            f"知识库《{kb.name}》中没有找到与「{args['query']}」相关的内容。",
            structured={"chunks": [], "latency_ms": latency_ms},
        )

    chunks = [_chunk_payload(item) for item in outcome.candidates]
    blocks = []
    for index, chunk in enumerate(chunks, start=1):
        location = chunk.get("heading_path") or ""
        if chunk.get("page_number"):
            location = f"{location}（第 {chunk['page_number']} 页）"
        blocks.append(
            f"[{index}] {chunk['filename']} · chunk {chunk['chunk_index']} {location}\n"
            f"{chunk['content']}"
        )
    return text_content(
        f"命中 {len(chunks)} 个片段（{latency_ms} ms）：\n\n" + "\n\n".join(blocks),
        structured={"chunks": chunks, "latency_ms": latency_ms, "kb": kb.name},
    )


async def _ask(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if "chat" not in ctx.principal.scopes:
        return error_content("当前 API Key 缺少 chat 权限，无法调用 ask_knowledge_base。")

    kb = await kb_service.get_owned_kb(ctx.db, UUID(args["kb_id"]), ctx.principal.user_id)
    started = time.perf_counter()
    outcome = await retrieve(ctx.db, kb, args["question"])
    if not outcome.candidates:
        return text_content(REFUSAL_MESSAGE, structured={"refusal": True, "citations": []})

    answer, usage = await complete_answer(
        args["question"],
        outcome.candidates,
        [],
        max_context_chars=get_settings().max_context_chars,
        prompt_variant=args.get("prompt_variant"),
    )
    metrics.observe_llm_usage(usage)
    citations = [_chunk_payload(item) for item in outcome.candidates]
    return text_content(
        answer or REFUSAL_MESSAGE,
        structured={
            "answer": answer,
            "refusal": not bool(answer),
            "citations": citations,
            "usage": usage,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    )


async def _outline(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from app.models.document import Document

    document = await ctx.db.get(Document, UUID(args["document_id"]))
    if document is None:
        return error_content("文档不存在。")
    await kb_service.get_owned_kb(ctx.db, document.kb_id, ctx.principal.user_id)

    rows = (
        await ctx.db.scalars(
            select(Chunk)
            .where(Chunk.document_id == document.id)
            .order_by(Chunk.chunk_index)
        )
    ).all()

    sections: list[dict[str, Any]] = []
    for chunk in rows:
        heading = chunk.heading_path or "（正文）"
        if not sections or sections[-1]["heading"] != heading:
            sections.append(
                {
                    "heading": heading,
                    "first_chunk": chunk.chunk_index,
                    "page_number": chunk.page_number,
                    "preview": _short(chunk.content, 120),
                }
            )
        else:
            sections[-1]["last_chunk"] = chunk.chunk_index

    lines = [f"《{document.filename}》共 {len(rows)} 个 chunk："]
    lines += [
        f"- {section['heading']}（chunk {section['first_chunk']}"
        f"~{section.get('last_chunk', section['first_chunk'])}"
        + (f"，第 {section['page_number']} 页" if section.get("page_number") else "")
        + f"）：{section['preview']}"
        for section in sections
    ]
    return text_content(
        "\n".join(lines),
        structured={"document": document.filename, "sections": sections},
    )


HANDLERS = {
    "list_knowledge_bases": _list_knowledge_bases,
    "search_knowledge_base": _search,
    "ask_knowledge_base": _ask,
    "get_document_outline": _outline,
}


async def call_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = HANDLERS.get(name)
    if handler is None:
        return error_content(f"未知工具：{name}")
    try:
        return await handler(ctx, args)
    except AppError as exc:
        return error_content(f"{exc.message}", details={"code": exc.code})
    except (ValueError, KeyError) as exc:
        return error_content(f"参数错误：{exc}")
    except Exception as exc:  # pragma: no cover - defensive
        import logging

        logging.getLogger(__name__).exception("mcp tool failed name=%s", name)
        return error_content(f"工具执行失败：{exc.__class__.__name__}")

