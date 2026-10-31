"""Multi-agent graph logic with the model and retriever stubbed out.

These tests care about the *orchestration* decisions — decomposition fallback,
evidence dedupe, loop termination — not about the quality of an LLM response.
That boundary is what makes the graph testable at all.
"""

import pytest

from app.core.config import get_settings
from app.rag.agent.graph import (
    DeepResearcher,
    _dedupe_queries,
    _stop_reason,
    parse_json_object,
)
from app.rag.pipeline import RetrievalConfig, RetrievalOutcome


class FakeKb:
    id = "kb-1"


def make_researcher(replies):
    """Researcher whose model replies come from a queue of JSON payloads."""
    researcher = DeepResearcher(None, FakeKb(), RetrievalConfig(), get_settings())
    queue = list(replies)

    async def fake_ask(system, user):
        if not queue:
            return None, "exhausted"
        return queue.pop(0), None

    researcher._ask_json = fake_ask  # type: ignore[method-assign]
    return researcher


def test_parse_json_object_handles_fences_and_chatter():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('好的\n```json\n{"sub_questions": ["x"]}\n```') == {
        "sub_questions": ["x"]
    }
    assert parse_json_object("no json here") is None
    assert parse_json_object("[1, 2]") is None


def test_dedupe_queries_is_case_insensitive_and_keeps_order():
    assert _dedupe_queries(["A", "b", "a", "  ", "C"], ["c"]) == ["A", "b"]


@pytest.mark.parametrize(
    ("sufficient", "followups", "exhausted", "payload", "expected"),
    [
        (True, [], False, {"sufficient": True}, "sufficient"),
        (False, ["q"], False, {"sufficient": False}, "needs_more_evidence"),
        (False, [], False, {"sufficient": False}, "critic_stopped"),
        (False, ["q"], True, {"sufficient": False}, "max_iterations"),
        (False, [], False, None, "llm_error"),
    ],
)
def test_stop_reason_matrix(sufficient, followups, exhausted, payload, expected):
    assert _stop_reason(sufficient, followups, exhausted, payload) == expected


async def test_plan_falls_back_to_the_original_question():
    researcher = make_researcher([None])
    state = await researcher.plan({"question": "X1 的电池容量是多少？", "steps": []})

    assert state["plan"] == ["X1 的电池容量是多少？"]
    assert state["steps"][0]["status"] == "degraded"
    assert state["stopped_reason"] == "degraded_plan"


async def test_plan_dedupes_and_caps_sub_questions():
    researcher = make_researcher(
        [{"sub_questions": ["a", "A", "b", "c", "d", "e"]}]
    )
    state = await researcher.plan({"question": "q", "steps": []})
    assert state["plan"] == ["a", "b", "c", "d"]


async def test_research_dedupes_evidence_and_records_steps(monkeypatch):
    import app.rag.agent.graph as graph

    calls = []

    async def fake_retrieve(db, kb, query, *, history=None, config=None):
        calls.append(query)
        return RetrievalOutcome(
            query=query,
            rewritten_query=query,
            candidates=[
                {"chunk_id": "c1", "content": "one", "fused_score": 0.5},
                {"chunk_id": "c2", "content": "two", "fused_score": 0.9},
            ],
            trace={"cache_hit": False, "stage_timings": {"total_ms": 12.0}},
        )

    monkeypatch.setattr(graph, "retrieve", fake_retrieve)

    researcher = make_researcher([])
    state = await researcher.research(
        {
            "question": "q",
            "plan": ["a", "b", "a"],
            "searched": ["a"],
            "evidence": [{"chunk_id": "c1", "content": "one", "fused_score": 0.4}],
            "steps": [],
        }
    )

    assert calls == ["b"]  # already-searched queries are skipped
    assert {item["chunk_id"] for item in state["evidence"]} == {"c1", "c2"}
    # The better-scoring copy wins when a chunk is seen twice.
    assert state["evidence"][0]["chunk_id"] == "c2"
    assert [step["tool_name"] for step in state["steps"]] == ["search_knowledge_base"]


async def test_review_without_evidence_stops_immediately():
    researcher = make_researcher([])
    state = await researcher.review({"question": "q", "evidence": [], "iteration": 1})
    assert state["stopped_reason"] == "no_evidence"
    assert state["review"]["sufficient"] is False


async def test_review_appends_followups_and_bumps_iteration():
    researcher = make_researcher(
        [
            {
                "sub_questions": ["a"],
            }
        ]
    )
    state = await researcher.plan({"question": "q", "steps": []})

    researcher = make_researcher(
        [
            {
                "sufficient": False,
                "missing": "缺少保修期限",
                "followup_queries": ["保修期多长", "a"],
            }
        ]
    )
    state = await researcher.review(
        {
            "question": "q",
            "evidence": [{"chunk_id": "c1", "content": "x", "filename": "f.md"}],
            "searched": ["a"],
            "steps": state["steps"],
            "iteration": 1,
        }
    )

    assert state["review"]["sufficient"] is False
    assert "保修期多长" in state["plan"]
    assert state["iteration"] == 2
    assert researcher._route_after_review(state) == "research"


async def test_route_stops_when_iterations_are_exhausted():
    researcher = make_researcher([])
    state = {
        "review": {"sufficient": False, "followup_queries": ["x"], "exhausted": True},
    }
    assert researcher._route_after_review(state) == "done"
