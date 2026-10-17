import json
from types import SimpleNamespace
from uuid import uuid4

from app.core.config import Settings
from app.rag.agent import loop as loop_module
from app.rag.agent.loop import parse_tool_arguments, stream_agent
from app.rag.agent.tools import ToolContext, execute_tool, ordered_evidence
from app.rag.pipeline import RetrievalConfig


def _settings(**overrides) -> Settings:
    return Settings(deepseek_api_key="test-key", **overrides)


def _tool_call(call_id: str, name: str, arguments: dict | str) -> SimpleNamespace:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=raw),
    )


def _response(*calls: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=list(calls) or None,
                )
            )
        ],
        usage=None,
    )


class FakeCompletions:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)


def _ctx() -> ToolContext:
    return ToolContext(
        db=None,  # type: ignore[arg-type]
        kb=SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
        config=RetrievalConfig(),
    )


def test_parse_tool_arguments_accepts_object_and_empty_input() -> None:
    assert parse_tool_arguments('{"query": "X1 续航"}') == ({"query": "X1 续航"}, None)
    assert parse_tool_arguments(None) == ({}, None)
    assert parse_tool_arguments("   ") == ({}, None)


def test_parse_tool_arguments_rejects_malformed_or_non_object_json() -> None:
    value, error = parse_tool_arguments("{not json")
    assert value is None
    assert error is not None

    value, error = parse_tool_arguments('["query"]')
    assert value is None
    assert error == "arguments 必须是 JSON 对象"


async def test_execute_tool_rejects_unknown_tool_and_invalid_arguments() -> None:
    unknown = await execute_tool("drop_database", {}, _ctx())
    assert unknown == {"ok": False, "error": "未知工具：drop_database"}

    invalid = await execute_tool("read_chunk_neighbors", {"chunk_id": "x", "window": 9}, _ctx())
    assert invalid["ok"] is False
    assert invalid["error"] == "参数校验失败"

    bad_uuid = await execute_tool("read_chunk_neighbors", {"chunk_id": "not-a-uuid"}, _ctx())
    assert bad_uuid == {"ok": False, "error": "chunk_id 不是合法的 UUID"}


async def test_loop_runs_tool_then_finishes(monkeypatch) -> None:
    ctx_seen: list[ToolContext] = []

    async def fake_execute(name: str, arguments: dict, ctx: ToolContext) -> dict:
        ctx_seen.append(ctx)
        assert name == "search_knowledge_base"
        ctx.merge_evidence(
            {
                "chunk_id": str(uuid4()),
                "document_id": str(uuid4()),
                "filename": "manual.md",
                "chunk_index": 0,
                "content": "X1 的续航为 12 小时。",
                "fused_score": 0.9,
            },
            rank=1,
            source="search",
        )
        return {"ok": True, "count": 1, "results": []}

    monkeypatch.setattr(loop_module, "execute_tool", fake_execute)
    client = FakeClient(
        [
            _response(_tool_call("call-1", "search_knowledge_base", {"query": "X1 续航"})),
            _response(),
        ]
    )

    events = [
        event
        async for event in stream_agent(
            None,  # type: ignore[arg-type]
            SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
            "X1 的续航是多少？",
            [],
            client,  # type: ignore[arg-type]
            RetrievalConfig(),
            _settings(agent_max_steps=3),
        )
    ]

    assert [event.type for event in events] == ["step", "result"]
    assert events[0].step is not None
    assert events[0].step.status == "ok"
    result = events[1].result
    assert result is not None
    assert result.stopped_reason == "model_finished"
    assert result.tool_calls == 1
    assert len(result.evidence) == 1
    assert ctx_seen
    # A tool-result message with the matching id must be appended after each call.
    assert client.completions.calls[1]["messages"][-1]["role"] == "tool"
    assert client.completions.calls[1]["messages"][-1]["tool_call_id"] == "call-1"


async def test_loop_stops_at_step_budget(monkeypatch) -> None:
    async def fake_execute(name: str, arguments: dict, ctx: ToolContext) -> dict:
        return {"ok": True}

    monkeypatch.setattr(loop_module, "execute_tool", fake_execute)
    client = FakeClient(
        [
            _response(_tool_call("call-1", "search_knowledge_base", {"query": "一"})),
            _response(_tool_call("call-2", "search_knowledge_base", {"query": "二"})),
            _response(_tool_call("call-3", "search_knowledge_base", {"query": "三"})),
        ]
    )

    events = [
        event
        async for event in stream_agent(
            None,  # type: ignore[arg-type]
            SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
            "多跳问题",
            [],
            client,  # type: ignore[arg-type]
            RetrievalConfig(),
            _settings(agent_max_steps=2),
        )
    ]

    result = events[-1].result
    assert result is not None
    assert result.stopped_reason == "max_steps"
    assert result.tool_calls == 2
    assert result.turns == 2
    assert len(client.completions.calls) == 2


async def test_loop_records_invalid_json_as_failed_step(monkeypatch) -> None:
    async def fake_execute(name: str, arguments: dict, ctx: ToolContext) -> dict:
        return {"ok": True}

    monkeypatch.setattr(loop_module, "execute_tool", fake_execute)
    client = FakeClient(
        [
            _response(_tool_call("call-1", "search_knowledge_base", "{broken")),
            _response(),
        ]
    )

    events = [
        event
        async for event in stream_agent(
            None,  # type: ignore[arg-type]
            SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
            "问题",
            [],
            client,  # type: ignore[arg-type]
            RetrievalConfig(),
            _settings(),
        )
    ]

    step = events[0].step
    assert step is not None
    assert step.status == "error"
    assert step.arguments == {}
    assert step.observation["ok"] is False
    assert events[-1].result is not None
    assert events[-1].result.stopped_reason == "model_finished"


async def test_loop_fails_open_when_llm_errors() -> None:
    client = FakeClient([RuntimeError("upstream down")])

    events = [
        event
        async for event in stream_agent(
            None,  # type: ignore[arg-type]
            SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
            "问题",
            [],
            client,  # type: ignore[arg-type]
            RetrievalConfig(),
            _settings(),
        )
    ]

    result = events[-1].result
    assert result is not None
    assert result.stopped_reason == "llm_error"
    assert result.steps == []
    assert result.error is not None


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
