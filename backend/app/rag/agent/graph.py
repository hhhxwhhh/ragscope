"""Deep research: a small multi-agent graph on top of the existing retriever.

The single-agent loop in ``loop.py`` lets one model decide when to search again.
That works for "compare X1 and X2", but it has two known weaknesses:

* the model has to hold the whole decomposition in its head, and
* nothing checks whether the collected evidence actually covers the question.

This module separates those concerns into three roles, compiled as a LangGraph
state machine::

    START -> planner -> researcher -> critic --(insufficient)--> researcher
                                       └------(sufficient)------> END

* **planner** decomposes the question into self-contained sub-queries;
* **researcher** runs the *existing* hybrid retrieval for each unsearched query
  and merges evidence by ``chunk_id`` (no new retrieval code path, no divergence
  from the metrics and traces of the single-shot pipeline);
* **critic** inspects the evidence *coverage* and either stops or asks for at most
  two follow-up queries. The loop is bounded by ``deep_max_iterations``.

The graph only decides *what evidence to collect*. The final answer is still
produced by the grounded prompt from ``app/rag/answer.py``, so citations,
``citation_stats`` and the trace format are identical across all three modes —
that consistency is what makes the modes comparable in the evaluation workbench.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.knowledge_base import KnowledgeBase
from app.rag.agent.loop import AgentLoopResult, AgentStepRecord
from app.rag.llm import get_agent_model, usage_to_dict
from app.rag.pipeline import RetrievalConfig, retrieve

logger = logging.getLogger(__name__)

PLANNER_PROMPT = """你是检索规划器。把用户问题拆成 1-4 个可以独立检索的中文子问题。

要求：
1. 每个子问题必须自包含，不出现「这个」「它」这类指代。
2. 覆盖原问题的所有要素：多实体比较时每个实体单独一条，多跳问题按依赖顺序排列。
3. 简单事实问题只输出一条，不要为了凑数量硬拆。
4. 不要回答问题，只输出子问题。

严格输出 JSON，不要解释：{"sub_questions": ["...", "..."]}"""

CRITIC_PROMPT = """你是证据审核员。判断已有证据是否足以完整回答用户问题。

判断标准：
1. 问题里的每个实体、每个子问题都要有证据覆盖。
2. 证据必须直接相关，仅有相同关键词不算覆盖。
3. 如果存在缺口，最多给出 2 条补充检索查询；查询要换一种表述，不要重复已检索过的。

严格输出 JSON：{"sufficient": true/false, "missing": "缺口说明", "followup_queries": ["..."]}"""

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class DeepState(TypedDict, total=False):
    question: str
    plan: list[str]
    searched: list[str]
    evidence: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    review: dict[str, Any]
    iteration: int
    stopped_reason: str
    error: str | None


@dataclass
class DeepEvent:
    type: Literal["plan", "step", "result"]
    plan: list[str] | None = None
    review: dict[str, Any] | None = None
    step: AgentStepRecord | None = None
    result: AgentLoopResult | None = None


@dataclass
class _Usage:
    total: dict[str, int] = field(default_factory=dict)

    def merge(self, usage: dict[str, int] | None) -> None:
        if not usage:
            return
        for key, value in usage.items():
            self.total[key] = self.total.get(key, 0) + int(value)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return "" if content is None else str(content)


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from a model reply.

    Models wrap JSON in ``` fences or prepend a sentence even when told not to;
    a forgiving parse plus a strict schema check downstream is more robust than
    a fragile prompt. Anything unparsable is a soft failure, never an exception.
    """
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    match = _JSON_BLOCK.search(text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _dedupe_queries(queries: list[str], searched: list[str]) -> list[str]:
    seen = {item.strip().lower() for item in searched}
    result = []
    for query in queries:
        normalized = query.strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        result.append(normalized)
    return result


class DeepResearcher:
    """Compiles and runs the planner → researcher → critic graph."""

    def __init__(
        self,
        db: AsyncSession,
        kb: KnowledgeBase,
        config: RetrievalConfig,
        settings: Settings,
        history: list[tuple[str, str]] | None = None,
    ) -> None:
        self.db = db
        self.kb = kb
        self.config = config
        self.settings = settings
        self.history = history or []
        self.usage = _Usage()
        self._model: Any = None

    # -- model helpers -----------------------------------------------------
    @property
    def model(self) -> Any:
        """Built on first use so a missing API key fails the request, not import."""
        if self._model is None:
            self._model = get_agent_model()
        return self._model

    async def _ask_json(
        self, system: str, user: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        try:
            message = await asyncio.wait_for(
                self.model.ainvoke(
                    [SystemMessage(content=system), HumanMessage(content=user)]
                ),
                timeout=self.settings.agent_turn_timeout_seconds,
            )
        except Exception as exc:
            logger.warning("deep agent model call failed: %s", exc)
            return None, f"{exc.__class__.__name__}: {exc}"
        self.usage.merge(usage_to_dict(message.usage_metadata))
        return parse_json_object(_content_text(message.content)), None

    # -- nodes -------------------------------------------------------------
    async def plan(self, state: DeepState) -> DeepState:
        started = time.perf_counter()
        question = state["question"]
        payload, error = await self._ask_json(
            PLANNER_PROMPT,
            f"用户问题：{question}\n\n请输出子问题 JSON。",
        )

        raw = (payload or {}).get("sub_questions") or []
        queries = _dedupe_queries(
            [str(item) for item in raw if isinstance(item, str)], []
        )[:4]
        if not queries:
            # Fail open: a planner failure must not block a normal retrieval.
            queries = [question]

        step = AgentStepRecord(
            step_index=len(state.get("steps") or []) + 1,
            tool_name="plan_sub_questions",
            arguments={"question": question},
            observation={
                "sub_questions": queries,
                "degraded": payload is None,
                "error": error,
            },
            status="ok" if payload is not None else "degraded",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return {
            "plan": queries,
            "searched": [],
            "steps": [*(state.get("steps") or []), step.to_dict()],
            "iteration": state.get("iteration", 0) + 1,
            "stopped_reason": "degraded_plan" if payload is None else "planned",
            "error": state.get("error") or error,
        }

    async def research(self, state: DeepState) -> DeepState:
        pending = _dedupe_queries(state.get("plan") or [], state.get("searched") or [])
        evidence = list(state.get("evidence") or [])
        index = {str(item.get("chunk_id")): position for position, item in enumerate(evidence)}
        steps = list(state.get("steps") or [])
        searched = list(state.get("searched") or [])

        for query in pending:
            started = time.perf_counter()
            try:
                outcome = await retrieve(
                    self.db,
                    self.kb,
                    query,
                    history=self.history,
                    config=replace(self.config, use_multi_query=False),
                )
                hits = outcome.candidates
                for candidate in hits:
                    chunk_id = str(candidate.get("chunk_id"))
                    if chunk_id in index:
                        # Keep the best-scoring copy of a chunk seen twice.
                        position = index[chunk_id]
                        if _score(candidate) > _score(evidence[position]):
                            evidence[position] = candidate
                    else:
                        index[chunk_id] = len(evidence)
                        evidence.append(candidate)
                step = AgentStepRecord(
                    step_index=len(steps) + 1,
                    tool_name="search_knowledge_base",
                    arguments={"query": query},
                    observation={
                        "hits": len(hits),
                        "chunk_ids": [str(item.get("chunk_id")) for item in hits][:10],
                        "cache_hit": bool(outcome.trace.get("cache_hit")),
                        "total_ms": outcome.trace.get("stage_timings", {}).get("total_ms"),
                    },
                    status="ok",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            except Exception as exc:
                logger.warning("deep research search failed query=%r: %s", query, exc)
                step = AgentStepRecord(
                    step_index=len(steps) + 1,
                    tool_name="search_knowledge_base",
                    arguments={"query": query},
                    observation={"error": f"{exc.__class__.__name__}: {exc}"},
                    status="error",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            steps.append(step.to_dict())
            searched.append(query)

        evidence.sort(key=_score, reverse=True)
        return {
            "evidence": evidence[: self.settings.agent_max_evidence],
            "steps": steps,
            "searched": searched,
        }

    async def review(self, state: DeepState) -> DeepState:
        started = time.perf_counter()
        evidence = state.get("evidence") or []
        iteration = state.get("iteration", 1)

        if not evidence:
            return {
                "review": {"sufficient": False, "missing": "检索不到任何证据"},
                "stopped_reason": "no_evidence",
                "iteration": iteration,
            }

        digest = "\n".join(
            f"[{index}] {item.get('filename')} · {item.get('heading_path') or ''}\n"
            f"{_short(item.get('content') or '', 220)}"
            for index, item in enumerate(evidence, start=1)
        )
        payload, error = await self._ask_json(
            CRITIC_PROMPT,
            f"用户问题：{state['question']}\n\n已检索到的证据：\n{digest}\n\n"
            f"已检索过的查询：{json.dumps(state.get('searched') or [], ensure_ascii=False)}",
        )

        sufficient = bool((payload or {}).get("sufficient"))
        followups = [
            str(item)
            for item in ((payload or {}).get("followup_queries") or [])
            if isinstance(item, str)
        ][:2]
        followups = _dedupe_queries(followups, state.get("searched") or [])

        exhausted = iteration >= self.settings.deep_max_iterations
        review = {
            "sufficient": sufficient,
            "missing": (payload or {}).get("missing"),
            "followup_queries": followups,
            "iteration": iteration,
            "exhausted": exhausted,
            "degraded": payload is None,
            "error": error,
        }
        step = AgentStepRecord(
            step_index=len(state.get("steps") or []) + 1,
            tool_name="review_evidence",
            arguments={"evidence_count": len(evidence)},
            observation=review,
            status="ok" if payload is not None else "degraded",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return {
            "review": review,
            "steps": [*(state.get("steps") or []), step.to_dict()],
            "plan": [*(state.get("plan") or []), *followups],
            "iteration": iteration + (1 if followups and not exhausted else 0),
            "stopped_reason": _stop_reason(sufficient, followups, exhausted, payload),
        }

    # -- graph -------------------------------------------------------------
    def _route_after_review(self, state: DeepState) -> str:
        review = state.get("review") or {}
        if review.get("sufficient") or review.get("exhausted"):
            return "done"
        if not review.get("followup_queries"):
            return "done"
        return "research"

    def build(self):
        graph = StateGraph(DeepState)
        graph.add_node("planner", self.plan)
        graph.add_node("researcher", self.research)
        graph.add_node("critic", self.review)
        graph.add_edge(START, "planner")
        graph.add_edge("planner", "researcher")
        graph.add_edge("researcher", "critic")
        graph.add_conditional_edges(
            "critic", self._route_after_review, {"research": "researcher", "done": END}
        )
        # planner + (research + review) * iterations; the recursion limit is a
        # backstop for a routing bug, the iteration budget is the real control.
        return graph.compile()


def _score(candidate: dict[str, Any]) -> float:
    for key in ("rerank_score", "fused_score", "vector_score"):
        value = candidate.get(key)
        if isinstance(value, int | float):
            return float(value)
    return 0.0


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _stop_reason(
    sufficient: bool,
    followups: list[str],
    exhausted: bool,
    payload: dict[str, Any] | None,
) -> str:
    if payload is None:
        return "llm_error"
    if sufficient:
        return "sufficient"
    if exhausted:
        return "max_iterations"
    if not followups:
        return "critic_stopped"
    return "needs_more_evidence"


def _build_result(
    state: DeepState, config: RetrievalConfig, usage: dict[str, int]
) -> AgentLoopResult:
    steps = [AgentStepRecord(**raw) for raw in state.get("steps") or []]
    searches = [
        {"query": step.arguments.get("query"), "status": step.status}
        for step in steps
        if step.tool_name == "search_knowledge_base"
    ]
    return AgentLoopResult(
        evidence=list(state.get("evidence") or []),
        steps=steps,
        tool_calls=sum(1 for step in steps if step.tool_name == "search_knowledge_base"),
        turns=len(steps),
        # Column is String(40): clamp defensively so a critic explanation can
        # never fail the insert.
        stopped_reason=str(state.get("stopped_reason") or "unknown")[:40],
        error=state.get("error"),
        latency_ms=0,
        token_usage=usage or None,
        config=config,
        searches=searches,
    )


async def stream_deep(
    db: AsyncSession,
    kb: KnowledgeBase,
    question: str,
    history: list[tuple[str, str]],
    config: RetrievalConfig,
    settings: Settings,
) -> AsyncIterator[DeepEvent]:
    """Run the graph and stream progress as it happens.

    ``stream_mode="values"`` yields the full state after every node, which gives
    the SSE layer a simple contract: diff the step list, emit whatever is new.
    That avoids tool-specific callback plumbing and keeps the graph nodes pure
    functions of the state.
    """
    started = time.perf_counter()
    researcher = DeepResearcher(db, kb, config, settings, history=history)
    graph = researcher.build()
    initial: DeepState = {
        "question": question,
        "plan": [],
        "searched": [],
        "evidence": [],
        "steps": [],
        "review": {},
        "iteration": 0,
        "stopped_reason": "running",
        "error": None,
    }

    state: DeepState = initial
    emitted_steps = 0
    emitted_plan: list[str] = []
    try:
        async for snapshot in graph.astream(
            initial,
            stream_mode="values",
            config={"recursion_limit": 4 * settings.deep_max_iterations + 4},
        ):
            state = snapshot
            plan = list(snapshot.get("plan") or [])
            if plan and plan != emitted_plan:
                emitted_plan = plan
                yield DeepEvent(type="plan", plan=plan, review=snapshot.get("review"))
            steps = snapshot.get("steps") or []
            for raw in steps[emitted_steps:]:
                yield DeepEvent(type="step", step=AgentStepRecord(**raw))
            emitted_steps = len(steps)
    except GraphRecursionError:
        logger.warning("deep research hit recursion limit kb=%s", kb.id)
        state = {**state, "stopped_reason": "recursion_limit"}
    except Exception as exc:
        logger.exception("deep research failed kb=%s", kb.id)
        state = {**state, "stopped_reason": "failed", "error": f"{exc.__class__.__name__}: {exc}"}

    result = _build_result(state, config, researcher.usage.total)
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    yield DeepEvent(type="result", result=result)
