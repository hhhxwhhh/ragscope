"""Agent loop tests run a real LangGraph graph with a scripted chat model.

``FakeMessagesListChatModel`` replays a fixed list of messages, so each test
defines the exact tool calls the "model" makes. That exercises ``create_agent``,
LangGraph's tool node and our ``astream_events`` translation without any network
call, while keeping the assertions deterministic.
"""

import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.core.config import Settings
from app.rag.agent import loop as loop_module
from app.rag.agent import tools as tools_module
from app.rag.agent.loop import _parse_observation, stream_agent
from app.rag.agent.tools import (
    BUDGET_ERROR,
    StepBudget,
    ToolContext,
    execute_tool,
    make_agent_tools,
    ordered_evidence,
)
from app.rag.pipeline import RetrievalConfig


class ToolCallingFakeModel(FakeMessagesListChatModel):
    """Replays messages; ``create_agent`` always calls ``bind_tools`` first."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ToolCallingFakeModel":
        return self


class ExplodingModel(ToolCallingFakeModel):
    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("upstream down")


def _settings(**overrides: Any) -> Settings:
    return Settings(deepseek_api_key="test-key", **overrides)


def _tool_call(name: str, arguments: dict[str, Any], call_id: str = "call-1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": arguments, "id": call_id, "type": "tool_call"},
        ],
    )


def _ctx() -> ToolContext:
    return ToolContext(
        db=None,  # type: ignore[arg-type]
        kb=SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
        config=RetrievalConfig(),
    )


def _candidate(**overrides: Any) -> dict[str, Any]:
    candidate = {
        "chunk_id": str(uuid4()),
        "document_id": str(uuid4()),
        "filename": "manual.md",
        "chunk_index": 0,
        "heading_path": "续航",
        "page_number": None,
        "content": "X1 的续航为 12 小时。",
        "fused_score": 0.9,
    }
    candidate.update(overrides)
    return candidate


def _fake_retrieve(candidates: list[dict[str, Any]], seen: list[dict[str, Any]] | None = None):
    async def retrieve(db, kb, query, history, config):
        if seen is not None:
            seen.append({"query": query, "config": config})
        return SimpleNamespace(
            candidates=candidates,
            rewritten_query=query,
            trace={"cache_hit": False, "stage_timings": {"total_ms": 1.0}},
        )

    return retrieve


async def _run(monkeypatch, responses, settings: Settings, candidates=None):
    monkeypatch.setattr(
        loop_module,
        "get_agent_model",
        lambda: ToolCallingFakeModel(responses=responses),
    )
    if candidates is not None:
        monkeypatch.setattr(tools_module, "retrieve", _fake_retrieve(candidates))
    return [
        event
        async for event in stream_agent(
            None,  # type: ignore[arg-type]
            SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
            "X1 的续航是多少？",
            [],
            RetrievalConfig(),
            settings,
        )
    ]


def test_step_budget_reserves_until_limit() -> None:
    budget = StepBudget(limit=2)
    assert budget.reserve() is True
    assert budget.reserve() is True
    assert budget.reserve() is False
    assert budget.reserve() is False
    assert budget.used == 2
    assert budget.exhausted is True
    assert budget.limit == 2


def test_parse_observation_handles_json_strings_and_tool_errors() -> None:
    assert _parse_observation('{"ok": true, "count": 1}') == {"ok": True, "count": 1}
    assert _parse_observation("plain text") == {"raw": "plain text"}
    assert _parse_observation(SimpleNamespace(content="{}", status="error")) == {
        "ok": False,
        "error": "{}",
    }


async def test_execute_tool_rejects_unknown_tool_and_invalid_arguments() -> None:
    unknown = await execute_tool("drop_database", {}, _ctx())
    assert unknown == {"ok": False, "error": "未知工具：drop_database"}

    invalid = await execute_tool("read_chunk_neighbors", {"chunk_id": "x", "window": 9}, _ctx())
    assert invalid["ok"] is False
    assert invalid["error"] == "参数校验失败"

    bad_uuid = await execute_tool("read_chunk_neighbors", {"chunk_id": "not-a-uuid"}, _ctx())
    assert bad_uuid == {"ok": False, "error": "chunk_id 不是合法的 UUID"}


async def test_loop_runs_tool_then_finishes(monkeypatch) -> None:
    seen: list[dict[str, Any]] = []
    candidates = [_candidate()]
    monkeypatch.setattr(tools_module, "retrieve", _fake_retrieve(candidates, seen))

    events = await _run(
        monkeypatch,
        [_tool_call("search_knowledge_base", {"query": "X1 续航"}), AIMessage(content="done")],
        _settings(agent_max_steps=3),
    )

    assert [event.type for event in events] == ["step", "result"]
    step = events[0].step
    assert step is not None
    assert step.tool_name == "search_knowledge_base"
    assert step.arguments == {"query": "X1 续航"}
    assert step.status == "ok"
    assert step.observation["ok"] is True
    assert step.observation["count"] == 1

    # The agent plans its own queries, so the tool must not fan out into
    # multi-query expansions (that would multiply LLM calls per tool use).
    assert seen and seen[0]["config"].use_multi_query is False

    result = events[-1].result
    assert result is not None
    assert result.stopped_reason == "model_finished"
    assert result.tool_calls == 1
    assert result.turns == 2  # tool-call turn + final turn
    assert [item["chunk_id"] for item in result.evidence] == [candidates[0]["chunk_id"]]
    assert result.searches[0]["result_count"] == 1


async def test_loop_stops_at_step_budget(monkeypatch) -> None:
    events = await _run(
        monkeypatch,
        [
            _tool_call("search_knowledge_base", {"query": "一"}, "call-1"),
            _tool_call("search_knowledge_base", {"query": "二"}, "call-2"),
            _tool_call("search_knowledge_base", {"query": "三"}, "call-3"),
        ],
        _settings(agent_max_steps=2),
        candidates=[_candidate()],
    )

    result = events[-1].result
    assert result is not None
    assert result.stopped_reason == "max_steps"
    assert result.tool_calls == 2
    # Rejected over-budget calls are shown to the model but not persisted as
    # user-visible steps.
    assert all(step.observation.get("error") != BUDGET_ERROR for step in result.steps)
    assert len(result.steps) == 2


async def test_loop_records_tool_error_as_failed_step(monkeypatch) -> None:
    events = await _run(
        monkeypatch,
        [
            _tool_call("read_chunk_neighbors", {"chunk_id": "not-a-uuid"}),
            AIMessage(content="done"),
        ],
        _settings(),
    )

    step = events[0].step
    assert step is not None
    assert step.status == "error"
    assert step.observation == {"ok": False, "error": "chunk_id 不是合法的 UUID"}
    result = events[-1].result
    assert result is not None
    assert result.stopped_reason == "model_finished"


async def test_loop_records_schema_validation_failure(monkeypatch) -> None:
    # LangChain's tool node validates args before our wrapper runs and emits
    # on_tool_error; the loop still records a failed, persisted step.
    events = await _run(
        monkeypatch,
        [
            _tool_call("read_chunk_neighbors", {"chunk_id": "x", "window": 9}),
            AIMessage(content="done"),
        ],
        _settings(),
    )

    step = events[0].step
    assert step is not None
    assert step.status == "error"
    assert step.arguments == {"chunk_id": "x", "window": 9}
    assert step.observation["ok"] is False
    assert "validation error" in step.observation["error"]


async def test_loop_fails_open_when_llm_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        loop_module,
        "get_agent_model",
        lambda: ExplodingModel(responses=[]),
    )

    events = [
        event
        async for event in stream_agent(
            None,  # type: ignore[arg-type]
            SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
            "问题",
            [],
            RetrievalConfig(),
            _settings(),
        )
    ]

    result = events[-1].result
    assert result is not None
    assert result.stopped_reason == "llm_error"
    assert result.steps == []
    assert result.error is not None


async def test_loop_sums_usage_across_turns(monkeypatch) -> None:
    tool_turn = _tool_call("search_knowledge_base", {"query": "X1"})
    tool_turn.usage_metadata = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
    final_turn = AIMessage(content="done")
    final_turn.usage_metadata = {"input_tokens": 20, "output_tokens": 3, "total_tokens": 23}

    events = await _run(
        monkeypatch,
        [tool_turn, final_turn],
        _settings(),
        candidates=[_candidate()],
    )

    result = events[-1].result
    assert result is not None
    assert result.token_usage == {
        "input_tokens": 30,
        "output_tokens": 5,
        "total_tokens": 35,
    }


async def test_tool_factory_exposes_whitelisted_schemas_and_enforces_budget(monkeypatch) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(tools_module, "retrieve", _fake_retrieve([_candidate()], seen))
    ctx = _ctx()
    budget = StepBudget(limit=1)
    tools = make_agent_tools(ctx, _settings(), budget)
    assert [tool.name for tool in tools] == [
        "search_knowledge_base",
        "read_chunk_neighbors",
        "get_document_outline",
    ]

    search = tools[0]
    first = json.loads(await search.ainvoke({"query": "X1 续航"}))
    assert first["ok"] is True
    assert first["count"] == 1
    assert budget.used == 1

    second = json.loads(await search.ainvoke({"query": "再来一次"}))
    assert second == {"ok": False, "error": BUDGET_ERROR}
    assert len(seen) == 1  # the rejected call never reaches retrieval


def test_merge_evidence_deduplicates_and_orders_by_best_rank() -> None:
    ctx = _ctx()
    first = {
        "chunk_id": "chunk-a",
        "content": "A",
        "document_id": "doc-1",
        "chunk_index": 0,
        "fused_score": 0.8,
    }
    second = {
        "chunk_id": "chunk-b",
        "content": "B",
        "document_id": "doc-1",
        "chunk_index": 1,
    }
    ctx.merge_evidence(first, rank=2, source="search")
    ctx.merge_evidence(second, rank=1, source="search")
    ctx.merge_evidence(
        {"...": "neighbour", "chunk_id": "chunk-a", "content": "A", "rerank_score": 0.7},
        rank=0.5,
        source="neighbors",
    )

    assert len(ctx.evidence) == 2
    evidence = ordered_evidence(ctx, limit=10)
    assert [item["chunk_id"] for item in evidence] == ["chunk-a", "chunk-b"]
    assert evidence[0]["_agent_best_rank"] == 0.5
    assert evidence[0]["_agent_sources"] == ["search", "neighbors"]
    assert evidence[0]["rerank_score"] == 0.7
    assert evidence[0]["fused_score"] == 0.8
    assert len(ordered_evidence(ctx, limit=1)) == 1
