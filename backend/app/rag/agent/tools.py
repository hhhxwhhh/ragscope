"""Whitelisted, Pydantic-validated tools available to the retrieval agent.

Every tool is scoped to one knowledge base. Tool arguments come from an LLM, so
they are treated as untrusted input: the agent advertises only these tools,
LangChain validates arguments against the Pydantic schema, and every observation
is plain JSON that can be persisted in ``agent_steps``.

Tools are built per request as closures over a ``ToolContext``. That keeps the
KB/DB/evidence state request-scoped instead of passing it through globals.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.rag.pipeline import RetrievalConfig, retrieve

logger = logging.getLogger(__name__)

PREVIEW_CHARS = 220
MAX_OUTLINE_ITEMS = 40
BUDGET_ERROR = "已达到最大工具步数"


class ToolError(Exception):
    """Expected tool failure that should be shown to the model, not raised."""


class SearchArgs(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=500,
        description="独立完整的检索问题，不要使用“它”“这个”之类指代",
    )
    top_k: int = Field(default=5, ge=1, le=10, description="返回条数，1-10，默认 5")


class NeighborArgs(BaseModel):
    chunk_id: str = Field(min_length=1, max_length=64, description="chunk 的 UUID")
    window: int = Field(default=1, ge=1, le=3, description="前后各读取几个 chunk，1-3，默认 1")


class OutlineArgs(BaseModel):
    document_id: str = Field(min_length=1, max_length=64, description="文档的 UUID")


@dataclass
class ToolContext:
    db: AsyncSession
    kb: KnowledgeBase
    config: RetrievalConfig
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    searches: list[dict[str, Any]] = field(default_factory=list)
    _seen: int = 0
    # LangGraph may run several tool calls from one model turn concurrently,
    # but they share one AsyncSession. Serialise DB access (and model inference)
    # so the agent keeps the old sequential-execution safety guarantees.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def merge_evidence(
        self,
        candidate: dict[str, Any],
        *,
        rank: float,
        source: str,
    ) -> None:
        """Deduplicate evidence across searches by chunk id.

        Ranks from different queries are not directly comparable, so the best
        observed rank is only a heuristic ordering hint; first-seen breaks ties.
        """
        chunk_id = str(candidate.get("chunk_id"))
        existing = self.evidence.get(chunk_id)
        if existing is None:
            item = dict(candidate)
            item["_agent_best_rank"] = float(rank)
            item["_agent_first_seen"] = self._seen
            item["_agent_sources"] = [source]
            self._seen += 1
            self.evidence[chunk_id] = item
            return
        existing["_agent_best_rank"] = min(float(existing["_agent_best_rank"]), float(rank))
        if source not in existing["_agent_sources"]:
            existing["_agent_sources"].append(source)
        # Keep the richer search candidate if a neighbour was seen first.
        for key in ("vector_score", "bm25_score", "fused_score", "rerank_score"):
            if existing.get(key) is None and candidate.get(key) is not None:
                existing[key] = candidate[key]


def _score(candidate: dict[str, Any]) -> float | None:
    score = candidate.get("rerank_score")
    if score is None:
        score = candidate.get("fused_score")
    return float(score) if score is not None else None


def _preview(content: str) -> str:
    return content[:PREVIEW_CHARS]


async def _search_knowledge_base(ctx: ToolContext, args: SearchArgs) -> dict[str, Any]:
    # The agent already plans multiple queries itself; enabling multi-query here
    # would multiply LLM calls per tool use. Cache stays on.
    config = replace(ctx.config, use_multi_query=False)
    outcome = await retrieve(ctx.db, ctx.kb, args.query, history=[], config=config)

    results: list[dict[str, Any]] = []
    for rank, candidate in enumerate(outcome.candidates[: args.top_k], start=1):
        ctx.merge_evidence(candidate, rank=rank, source="search")
        results.append(
            {
                "rank": rank,
                "chunk_id": str(candidate.get("chunk_id")),
                "document_id": str(candidate.get("document_id")),
                "filename": candidate.get("filename"),
                "chunk_index": candidate.get("chunk_index"),
                "heading_path": candidate.get("heading_path"),
                "page_number": candidate.get("page_number"),
                "score": _score(candidate),
                "preview": _preview(candidate.get("content") or ""),
            }
        )

    ctx.searches.append(
        {
            "query": args.query,
            "rewritten_query": outcome.rewritten_query,
            "cache_hit": bool(outcome.trace.get("cache_hit")),
            "result_count": len(results),
            "total_ms": outcome.trace.get("stage_timings", {}).get("total_ms"),
        }
    )
    return {
        "ok": True,
        "query": args.query,
        "rewritten_query": outcome.rewritten_query,
        "cache_hit": bool(outcome.trace.get("cache_hit")),
        "count": len(results),
        "results": results,
    }


async def _read_chunk_neighbors(ctx: ToolContext, args: NeighborArgs) -> dict[str, Any]:
    try:
        chunk_id = UUID(args.chunk_id)
    except ValueError as exc:
        raise ToolError("chunk_id 不是合法的 UUID") from exc

    chunk = await ctx.db.scalar(
        select(Chunk).where(Chunk.id == chunk_id, Chunk.kb_id == ctx.kb.id)
    )
    if chunk is None:
        raise ToolError("chunk 不存在或不属于当前知识库")

    document = await ctx.db.get(Document, chunk.document_id)
    if document is None:
        raise ToolError("chunk 所属文档不存在")

    lower = max(0, chunk.chunk_index - args.window)
    upper = chunk.chunk_index + args.window
    rows = (
        await ctx.db.scalars(
            select(Chunk)
            .where(
                Chunk.kb_id == ctx.kb.id,
                Chunk.document_id == chunk.document_id,
                Chunk.chunk_index.between(lower, upper),
                Chunk.id != chunk.id,
            )
            .order_by(Chunk.chunk_index)
        )
    ).all()

    parent = ctx.evidence.get(str(chunk.id))
    base_rank = float(parent["_agent_best_rank"]) if parent else 100.0
    results: list[dict[str, Any]] = []
    for offset, row in enumerate(rows, start=1):
        candidate = {
            "chunk_id": str(row.id),
            "document_id": str(row.document_id),
            "filename": document.filename,
            "chunk_index": row.chunk_index,
            "heading_path": row.heading_path,
            "page_number": row.page_number,
            "content": row.content,
        }
        ctx.merge_evidence(candidate, rank=base_rank + offset / 100, source="neighbors")
        results.append(
            {
                "chunk_id": str(row.id),
                "chunk_index": row.chunk_index,
                "heading_path": row.heading_path,
                "page_number": row.page_number,
                "preview": _preview(row.content),
            }
        )

    return {
        "ok": True,
        "chunk_id": str(chunk.id),
        "filename": document.filename,
        "chunk_index": chunk.chunk_index,
        "count": len(results),
        "neighbors": results,
    }


async def _get_document_outline(ctx: ToolContext, args: OutlineArgs) -> dict[str, Any]:
    try:
        document_id = UUID(args.document_id)
    except ValueError as exc:
        raise ToolError("document_id 不是合法的 UUID") from exc

    document = await ctx.db.get(Document, document_id)
    if document is None or document.kb_id != ctx.kb.id:
        raise ToolError("文档不存在或不属于当前知识库")

    rows = (
        await ctx.db.scalars(
            select(Chunk)
            .where(Chunk.kb_id == ctx.kb.id, Chunk.document_id == document.id)
            .order_by(Chunk.chunk_index)
        )
    ).all()

    outline: list[dict[str, Any]] = []
    for row in rows:
        heading = row.heading_path or "无标题"
        if not outline or outline[-1]["heading"] != heading:
            outline.append(
                {
                    "heading": heading,
                    "first_chunk_index": row.chunk_index,
                    "page_number": row.page_number,
                    "chunks": 1,
                }
            )
        else:
            outline[-1]["chunks"] += 1

    return {
        "ok": True,
        "document_id": str(document.id),
        "filename": document.filename,
        "chunk_count": len(rows),
        "page_count": document.page_count,
        "outline": outline[:MAX_OUTLINE_ITEMS],
    }


_ARG_MODELS: dict[str, type[BaseModel]] = {
    "search_knowledge_base": SearchArgs,
    "read_chunk_neighbors": NeighborArgs,
    "get_document_outline": OutlineArgs,
}

_TOOL_FUNCS = {
    "search_knowledge_base": _search_knowledge_base,
    "read_chunk_neighbors": _read_chunk_neighbors,
    "get_document_outline": _get_document_outline,
}


async def execute_tool(
    name: str,
    raw_arguments: dict[str, Any],
    ctx: ToolContext,
) -> dict[str, Any]:
    """Validate and execute one tool call. Never raises for expected failures."""
    arg_model = _ARG_MODELS.get(name)
    func = _TOOL_FUNCS.get(name)
    if arg_model is None or func is None:
        return {"ok": False, "error": f"未知工具：{name}"}
    try:
        args = arg_model.model_validate(raw_arguments)
    except ValidationError as exc:
        return {
            "ok": False,
            "error": "参数校验失败",
            "details": exc.errors(include_url=False)[:3],
        }
    try:
        return await func(ctx, args)  # type: ignore[arg-type]
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive boundary for LLM tools
        logger.exception("tool %s failed", name)
        return {"ok": False, "error": f"工具执行失败：{type(exc).__name__}"}


@dataclass
class StepBudget:
    """Hard cap on executed tool calls for one agent turn."""

    limit: int
    used: int = 0
    exhausted: bool = False

    def reserve(self) -> bool:
        if self.used >= self.limit:
            self.exhausted = True
            return False
        self.used += 1
        return True


async def _run_tool(
    ctx: ToolContext,
    settings: Settings,
    budget: StepBudget,
    args_model: type[BaseModel],
    func: Callable[[ToolContext, Any], Awaitable[dict[str, Any]]],
    raw_arguments: dict[str, Any],
) -> dict[str, Any]:
    """Budget, validate, serialise and time-box one tool execution."""
    if not budget.reserve():
        return {"ok": False, "error": BUDGET_ERROR}
    try:
        args = args_model.model_validate(raw_arguments)
    except ValidationError as exc:
        return {
            "ok": False,
            "error": "参数校验失败",
            "details": exc.errors(include_url=False)[:3],
        }
    try:
        async with ctx.lock:
            return await asyncio.wait_for(
                func(ctx, args),
                timeout=settings.agent_tool_timeout_seconds,
            )
    except TimeoutError:
        return {"ok": False, "error": "工具执行超时"}
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive boundary for LLM tools
        logger.exception("tool %s failed", args_model.__name__)
        return {"ok": False, "error": f"工具执行失败：{type(exc).__name__}"}


def _dump(observation: dict[str, Any]) -> str:
    return json.dumps(observation, ensure_ascii=False)


def make_agent_tools(
    ctx: ToolContext,
    settings: Settings,
    budget: StepBudget,
) -> list[BaseTool]:
    """Build the whitelisted LangChain tools for one request.

    The closures capture the request-scoped context, so evidence and the DB
    session never leak between conversations or concurrent users.
    """

    @tool("search_knowledge_base", args_schema=SearchArgs)
    async def search_tool(query: str, top_k: int = 5) -> str:
        """在知识库中做混合检索（向量 + BM25 + RRF + 可选重排）。

        问题是多跳、比较或多个实体时，请分别调用多次，每次使用独立、完整的问题。
        """
        observation = await _run_tool(
            ctx,
            settings,
            budget,
            SearchArgs,
            _search_knowledge_base,
            {"query": query, "top_k": top_k},
        )
        return _dump(observation)

    @tool("read_chunk_neighbors", args_schema=NeighborArgs)
    async def neighbors_tool(chunk_id: str, window: int = 1) -> str:
        """读取某个 chunk 前后相邻的 chunk，用于补全被切块截断的上下文。

        chunk_id 必须来自 search_knowledge_base 的返回。
        """
        observation = await _run_tool(
            ctx,
            settings,
            budget,
            NeighborArgs,
            _read_chunk_neighbors,
            {"chunk_id": chunk_id, "window": window},
        )
        return _dump(observation)

    @tool("get_document_outline", args_schema=OutlineArgs)
    async def outline_tool(document_id: str) -> str:
        """查看一篇文档的标题结构（章节、chunk 序号与页码）。

        document_id 必须来自 search_knowledge_base 的返回。
        """
        observation = await _run_tool(
            ctx,
            settings,
            budget,
            OutlineArgs,
            _get_document_outline,
            {"document_id": document_id},
        )
        return _dump(observation)

    return [search_tool, neighbors_tool, outline_tool]


def ordered_evidence(
    ctx: ToolContext,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Evidence ordered by best observed rank, capped for the final prompt."""
    items = sorted(
        ctx.evidence.values(),
        key=lambda item: (item.get("_agent_best_rank", 999.0), item.get("_agent_first_seen", 0)),
    )
    return items[:limit]
