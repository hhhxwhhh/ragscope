"""Prometheus metrics for the API and the RAG pipeline.

This module implements the small subset of the Prometheus client that this
service actually needs — counters, gauges and histograms with labels, rendered in
the official text exposition format. It exists as a first-party module instead of
a dependency for a practical reason: the runtime has to stay installable in an
offline/air-gapped deployment, and a metrics client is ~200 lines of well-defined
behaviour. The exposition format is unchanged, so Prometheus, Grafana Agent,
VictoriaMetrics and the OpenTelemetry collector all scrape it as-is.

Label discipline is the hard part of metrics work: HTTP labels use the *route
template* (``/api/v1/kbs/{kb_id}``) rather than the raw path, otherwise every
knowledge base creates its own time series and the scrape grows without bound.
"""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Iterable
from typing import Any

CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

_LOCK = threading.Lock()
_METRICS: list[_Metric] = []


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_value(value: float) -> str:
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if value == int(value):
        return str(int(value))
    return repr(float(value))


class _Metric:
    kind = "untyped"

    def __init__(self, name: str, help_text: str, labelnames: Iterable[str] = ()) -> None:
        self.name = name
        self.help = help_text
        self.labelnames = tuple(labelnames)
        self._lock = threading.Lock()
        _METRICS.append(self)

    def _key(self, labels: dict[str, str]) -> tuple[str, ...]:
        return tuple(str(labels.get(name, "")) for name in self.labelnames)

    def _label_text(self, key: tuple[str, ...], extra: dict[str, str] | None = None) -> str:
        pairs = [
            f'{name}="{_escape(value)}"'
            for name, value in zip(self.labelnames, key, strict=False)
        ]
        if extra:
            pairs.extend(f'{name}="{_escape(value)}"' for name, value in extra.items())
        return "{" + ",".join(pairs) + "}" if pairs else ""

    def samples(self) -> list[tuple[str, str, float]]:
        """Return ``(metric_name, label_text, value)`` triples."""
        raise NotImplementedError


class Counter(_Metric):
    kind = "counter"

    def __init__(self, name: str, help_text: str, labelnames: Iterable[str] = ()) -> None:
        super().__init__(name, help_text, labelnames)
        self._values: dict[tuple[str, ...], float] = {}

    def labels(self, **labels: str) -> _CounterChild:
        return _CounterChild(self, self._key(labels))

    def inc(self, amount: float = 1.0) -> None:
        self.labels().inc(amount)

    def value(self) -> float:
        return sum(self._values.values())

    def samples(self) -> list[tuple[str, str, float]]:
        with self._lock:
            rows = list(self._values.items())
        return [(self.name, self._label_text(key), value) for key, value in rows]


class _CounterChild:
    __slots__ = ("_parent", "_key")

    def __init__(self, parent: Counter, key: tuple[str, ...]) -> None:
        self._parent = parent
        self._key = key

    def inc(self, amount: float = 1.0) -> None:
        parent = self._parent
        with parent._lock:
            parent._values[self._key] = parent._values.get(self._key, 0.0) + amount

    def value(self) -> float:
        """Current value of this label combination (0 before the first ``inc``).

        ``Counter.value()`` sums every combination; callers that already selected
        a label set need the single series, which is what ``snapshot()`` reads
        for the semantic-cache hit/miss split.
        """
        parent = self._parent
        with parent._lock:
            return parent._values.get(self._key, 0.0)


class Gauge(_Metric):
    kind = "gauge"

    def __init__(self, name: str, help_text: str, labelnames: Iterable[str] = ()) -> None:
        super().__init__(name, help_text, labelnames)
        self._values: dict[tuple[str, ...], float] = {}

    def labels(self, **labels: str) -> _GaugeChild:
        return _GaugeChild(self, self._key(labels))

    def set(self, value: float) -> None:
        self.labels().set(value)

    def inc(self, amount: float = 1.0) -> None:
        self.labels().inc(amount)

    def dec(self, amount: float = 1.0) -> None:
        self.labels().inc(-amount)

    def value(self) -> float:
        return sum(self._values.values())

    def samples(self) -> list[tuple[str, str, float]]:
        with self._lock:
            rows = list(self._values.items())
        return [(self.name, self._label_text(key), value) for key, value in rows]


class _GaugeChild:
    __slots__ = ("_parent", "_key")

    def __init__(self, parent: Gauge, key: tuple[str, ...]) -> None:
        self._parent = parent
        self._key = key

    def set(self, value: float) -> None:
        with self._parent._lock:
            self._parent._values[self._key] = float(value)

    def inc(self, amount: float = 1.0) -> None:
        with self._parent._lock:
            current = self._parent._values.get(self._key, 0.0)
            self._parent._values[self._key] = current + amount


class Histogram(_Metric):
    kind = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        labelnames: Iterable[str] = (),
        buckets: Iterable[float] = (),
    ) -> None:
        super().__init__(name, help_text, labelnames)
        self.buckets = tuple(sorted(buckets)) or (
            0.005,
            0.01,
            0.025,
            0.05,
            0.1,
            0.25,
            0.5,
            1.0,
            2.5,
            5.0,
            10.0,
        )
        # key -> [per-bucket counts, sum, count]
        self._values: dict[tuple[str, ...], list[Any]] = {}

    def labels(self, **labels: str) -> _HistogramChild:
        return _HistogramChild(self, self._key(labels))

    def observe(self, value: float) -> None:
        self.labels().observe(value)

    def samples(self) -> list[tuple[str, str, float]]:
        rows: list[tuple[str, str, float]] = []
        with self._lock:
            entries = [(key, list(state)) for key, state in self._values.items()]
        for key, state in entries:
            counts: list[int] = state[0]
            cumulative = 0
            for bound, count in zip(self.buckets, counts, strict=True):
                cumulative += count
                rows.append(
                    (
                        f"{self.name}_bucket",
                        self._label_text(key, {"le": _format_value(bound)}),
                        float(cumulative),
                    )
                )
            rows.append(
                (
                    f"{self.name}_bucket",
                    self._label_text(key, {"le": "+Inf"}),
                    float(state[2]),
                )
            )
            rows.append((f"{self.name}_sum", self._label_text(key), float(state[1])))
            rows.append((f"{self.name}_count", self._label_text(key), float(state[2])))
        return rows

    def distribution(self) -> list[tuple[float, float]]:
        """Merged cumulative buckets across every label set (for the ops panel)."""
        merged: list[float] = [0.0] * len(self.buckets)
        total = 0.0
        with self._lock:
            for state in self._values.values():
                total += state[2]
                for index, count in enumerate(state[0]):
                    merged[index] += count
        rows: list[tuple[float, float]] = []
        cumulative = 0.0
        for bound, count in zip(self.buckets, merged, strict=True):
            cumulative += count
            rows.append((bound, cumulative))
        rows.append((math.inf, total))
        return rows


class _HistogramChild:
    __slots__ = ("_parent", "_key")

    def __init__(self, parent: Histogram, key: tuple[str, ...]) -> None:
        self._parent = parent
        self._key = key

    def observe(self, value: float) -> None:
        parent = self._parent
        with parent._lock:
            state = parent._values.get(self._key)
            if state is None:
                state = [[0] * len(parent.buckets), 0.0, 0]
                parent._values[self._key] = state
            for index, bound in enumerate(parent.buckets):
                if value <= bound:
                    state[0][index] += 1
                    break
            state[1] += value
            state[2] += 1


def _counter(name: str, help_text: str, labels: Iterable[str] = ()) -> Counter:
    return Counter(name, help_text, labels)


def _gauge(name: str, help_text: str, labels: Iterable[str] = ()) -> Gauge:
    return Gauge(name, help_text, labels)


def _histogram(
    name: str,
    help_text: str,
    labels: Iterable[str] = (),
    buckets: Iterable[float] = (),
) -> Histogram:
    return Histogram(name, help_text, labels, buckets)


# --- HTTP layer -----------------------------------------------------------
HTTP_REQUESTS_TOTAL = _counter(
    "ragscope_http_requests_total",
    "HTTP requests by route template and status code.",
    ("method", "route", "status"),
)
HTTP_REQUEST_DURATION = _histogram(
    "ragscope_http_request_duration_seconds",
    "End-to-end request latency, including SSE stream duration.",
    ("method", "route"),
    (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)
HTTP_IN_FLIGHT = _gauge(
    "ragscope_http_in_flight",
    "Requests currently being served.",
)

# --- Retrieval ------------------------------------------------------------
RETRIEVAL_STAGE_DURATION = _histogram(
    "ragscope_retrieval_stage_seconds",
    "Per-stage retrieval latency.",
    ("stage",),
    (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
RETRIEVAL_TOTAL = _counter(
    "ragscope_retrieval_total",
    "Retrieval requests by cache outcome.",
    ("cache",),
)
RETRIEVAL_CANDIDATES = _histogram(
    "ragscope_retrieval_candidates",
    "Candidate count per retrieval stage.",
    ("stage",),
    (0, 1, 3, 5, 10, 20, 30, 50, 100),
)
RETRIEVAL_REFUSAL_TOTAL = _counter(
    "ragscope_retrieval_refusal_total",
    "Answers refused because no candidate survived thresholds.",
)

# --- LLM ------------------------------------------------------------------
LLM_TOKENS_TOTAL = _counter(
    "ragscope_llm_tokens_total",
    "Token usage by direction.",
    ("kind",),
)
LLM_ANSWER_DURATION = _histogram(
    "ragscope_llm_answer_duration_seconds",
    "Wall-clock time of a grounded answer generation.",
    (),
    (0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# --- Agent ----------------------------------------------------------------
AGENT_RUNS_TOTAL = _counter(
    "ragscope_agent_runs_total",
    "Agent runs by mode and stop reason.",
    ("mode", "stopped_reason"),
)
AGENT_STEPS = _histogram(
    "ragscope_agent_steps",
    "Tool-calling steps per agent run.",
    ("mode",),
    (0, 1, 2, 3, 4, 5, 6, 8, 10),
)
AGENT_TOOL_DURATION = _histogram(
    "ragscope_agent_tool_duration_seconds",
    "Per-tool latency inside an agent run.",
    ("tool",),
    (0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# --- Pipeline stages ------------------------------------------------------
INGEST_TOTAL = _counter(
    "ragscope_ingest_total",
    "Document ingestion outcomes.",
    ("status",),
)
GRAPHRAG_TOTAL = _counter(
    "ragscope_graphrag_total",
    "Graph extraction and expansion outcomes.",
    ("operation", "outcome"),
)
SEMANTIC_CACHE_TOTAL = _counter(
    "ragscope_semantic_cache_total",
    "Semantic answer cache outcomes.",
    ("result",),
)
EVAL_RUNS_TOTAL = _counter(
    "ragscope_eval_runs_total",
    "Evaluation runs by terminal status.",
    ("status",),
)
INFO = _gauge(
    "ragscope_build_info",
    "Static build information; the value is always 1.",
    ("service", "version", "environment"),
)

_UUID_RE = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_DIGITS_RE = re.compile(r"/\d+(?=/|$)")


def normalise_route(path: str) -> str:
    """Collapse identifiers so a route produces exactly one time series.

    Starlette exposes the matched route template on ``scope["route"]``; this
    helper only runs for 404s and for middleware that sees the raw path before
    routing has completed.
    """
    return _DIGITS_RE.sub("/{id}", _UUID_RE.sub("/{id}", path))


def observe_http(method: str, route: str, status_code: int, duration_seconds: float) -> None:
    HTTP_REQUESTS_TOTAL.labels(method=method, route=route, status=str(status_code)).inc()
    HTTP_REQUEST_DURATION.labels(method=method, route=route).observe(duration_seconds)


def observe_retrieval(trace: dict[str, Any]) -> None:
    """Translate one retrieval trace into stage latency and candidate metrics."""
    timings = trace.get("stage_timings") or {}
    for stage in ("rewrite", "multi_query", "cache", "embed", "vector", "bm25", "fusion", "rerank"):
        value = timings.get(f"{stage}_ms")
        if isinstance(value, int | float) and value >= 0:
            RETRIEVAL_STAGE_DURATION.labels(stage=stage).observe(float(value) / 1000.0)

    counts = trace.get("counts") or {}
    for stage in ("vector", "bm25", "fused", "final"):
        value = counts.get(stage)
        if isinstance(value, int):
            RETRIEVAL_CANDIDATES.labels(stage=stage).observe(value)

    RETRIEVAL_TOTAL.labels(cache="hit" if trace.get("cache_hit") else "miss").inc()
    if not counts.get("final"):
        RETRIEVAL_REFUSAL_TOTAL.inc()


def observe_llm_usage(usage: dict[str, Any] | None) -> None:
    if not usage:
        return
    prompt = int(usage.get("input_tokens") or 0)
    completion = int(usage.get("output_tokens") or 0)
    if prompt:
        LLM_TOKENS_TOTAL.labels(kind="prompt").inc(prompt)
    if completion:
        LLM_TOKENS_TOTAL.labels(kind="completion").inc(completion)


def observe_agent_run(mode: str, steps: int, stopped_reason: str | None) -> None:
    AGENT_RUNS_TOTAL.labels(mode=mode, stopped_reason=stopped_reason or "unknown").inc()
    AGENT_STEPS.labels(mode=mode).observe(steps)


def observe_agent_tool(tool_name: str, latency_ms: float | int) -> None:
    AGENT_TOOL_DURATION.labels(tool=tool_name).observe(float(latency_ms) / 1000.0)


def observe_ingest(status: str) -> None:
    INGEST_TOTAL.labels(status=status).inc()


def observe_graph(operation: str, outcome: str) -> None:
    GRAPHRAG_TOTAL.labels(operation=operation, outcome=outcome).inc()


def observe_semantic_cache(result: str) -> None:
    SEMANTIC_CACHE_TOTAL.labels(result=result).inc()


def observe_eval_run(status: str) -> None:
    EVAL_RUNS_TOTAL.labels(status=status).inc()


def mark_info(service: str, version: str, environment: str) -> None:
    INFO.labels(service=service, version=version, environment=environment).set(1)


def render() -> str:
    lines: list[str] = []
    for metric in _METRICS:
        lines.append(f"# HELP {metric.name} {metric.help}")
        lines.append(f"# TYPE {metric.name} {metric.kind}")
        for sample_name, labels, value in metric.samples():
            if labels:
                lines.append(f"{sample_name}{labels} {_format_value(value)}")
            else:
                lines.append(f"{sample_name} {_format_value(value)}")
    return "\n".join(lines) + "\n"


def _percentile(buckets: list[tuple[float, float]], quantile: float) -> float | None:
    """Linear interpolation over cumulative histogram buckets.

    Good enough for an ops summary endpoint; Prometheus/Grafana should still be
    the source of truth for SLOs, which use ``histogram_quantile`` over the raw
    buckets.
    """
    if not buckets:
        return None
    total = buckets[-1][1]
    if total <= 0:
        return None
    target = total * quantile
    previous_bound, previous_count = 0.0, 0.0
    for bound, count in buckets:
        if count >= target:
            if count == previous_count:
                return bound
            ratio = (target - previous_count) / (count - previous_count)
            return round(previous_bound + (bound - previous_bound) * ratio, 4)
        previous_bound, previous_count = bound, count
    return buckets[-1][0]


def snapshot() -> dict[str, Any]:
    """Condense the registry into a JSON payload for the ops dashboard."""
    request_values = list(HTTP_REQUESTS_TOTAL._values.items())  # noqa: SLF001 - same module
    status_index = HTTP_REQUESTS_TOTAL.labelnames.index("status")
    requests_total = sum(value for _, value in request_values)
    errors_total = sum(
        value for key, value in request_values if str(key[status_index]).startswith("5")
    )
    ordered = HTTP_REQUEST_DURATION.distribution()

    return {
        "http": {
            "requests_total": int(requests_total),
            "errors_total": int(errors_total),
            "error_rate": round(errors_total / requests_total, 4) if requests_total else 0.0,
            "p50_seconds": _percentile(ordered, 0.5),
            "p95_seconds": _percentile(ordered, 0.95),
            "p99_seconds": _percentile(ordered, 0.99),
        },
        "retrieval": {
            "total": int(RETRIEVAL_TOTAL.value()),
            "refusals": int(RETRIEVAL_REFUSAL_TOTAL.value()),
        },
        "llm": {"tokens_total": int(LLM_TOKENS_TOTAL.value())},
        "agent": {"runs_total": int(AGENT_RUNS_TOTAL.value())},
        "ingest": {"total": int(INGEST_TOTAL.value())},
        "graphrag": {"total": int(GRAPHRAG_TOTAL.value())},
        "semantic_cache": {
            "hit": int(SEMANTIC_CACHE_TOTAL.labels(result="hit").value()),
            "miss": int(SEMANTIC_CACHE_TOTAL.labels(result="miss").value()),
            "store": int(SEMANTIC_CACHE_TOTAL.labels(result="store").value()),
        },
        "eval": {"runs_total": int(EVAL_RUNS_TOTAL.value())},
    }


__all__ = [
    "CONTENT_TYPE_LATEST",
    "mark_info",
    "normalise_route",
    "observe_agent_run",
    "observe_agent_tool",
    "observe_eval_run",
    "observe_graph",
    "observe_http",
    "observe_ingest",
    "observe_llm_usage",
    "observe_retrieval",
    "observe_semantic_cache",
    "render",
    "snapshot",
]
