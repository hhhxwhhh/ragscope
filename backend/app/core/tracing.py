"""OpenTelemetry tracing with a graceful, opt-in setup.

Tracing is deliberately *not* required to run the app. When ``OTEL_ENABLED`` is
false — the default for local development and tests — every helper degrades to a
no-op, so instrumenting a code path never becomes a reason for a request to fail.

Two propagation details matter for a RAG service:

* The W3C ``traceparent`` header is trusted on ingress, so an upstream gateway
  can stitch its spans to ours.
* The active trace id is echoed as ``X-Trace-ID`` so a user-visible request id in
  the UI can be pasted straight into Jaeger/Tempo.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_tracer: Any = None
_configured = False


class _NoopSpan:
    def set_attribute(self, _key: str, _value: Any) -> None:  # pragma: no cover - trivial
        return None

    def record_exception(self, _exc: BaseException) -> None:  # pragma: no cover - trivial
        return None

    def set_status(self, *_args: Any, **_kwargs: Any) -> None:  # pragma: no cover - trivial
        return None


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Start a child span; yields a no-op object when tracing is disabled."""
    if _tracer is None:
        yield _NoopSpan()
        return
    with _tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        yield current


def current_trace_id() -> str | None:
    if _tracer is None:
        return None
    try:
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return None
        return format(context.trace_id, "032x")
    except Exception:  # pragma: no cover - tracing must never break a response
        return None


def configure_tracing(app: Any) -> bool:
    """Install the tracer provider and (optionally) auto-instrumentation.

    Returns True when tracing is active. Any failure is logged and downgraded to
    "tracing off" instead of aborting startup.
    """
    global _tracer, _configured
    settings = get_settings()
    if not settings.otel_enabled:
        return False
    if _configured:
        return _tracer is not None

    _configured = True
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

        resource = Resource.create(
            {
                "service.name": settings.otel_service_name,
                "service.version": settings.app_version,
                "deployment.environment": settings.app_env,
            }
        )
        provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(TraceIdRatioBased(settings.otel_sample_ratio)),
        )

        exporter = _build_exporter(settings, ConsoleSpanExporter)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("ragscope")

        _instrument_libraries(app, settings)
        logger.info(
            "tracing enabled service=%s endpoint=%s ratio=%.2f",
            settings.otel_service_name,
            settings.otel_exporter_endpoint or "console",
            settings.otel_sample_ratio,
        )
        return True
    except Exception:
        _tracer = None
        logger.exception("failed to configure tracing; continuing without spans")
        return False


def _build_exporter(settings: Any, console_class: Any) -> Any:
    endpoint = settings.otel_exporter_endpoint
    if not endpoint:
        return console_class()
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter(
            endpoint=endpoint,
            insecure=endpoint.startswith(("http://", "localhost", "jaeger", "otel-collector")),
            timeout=5,
        )
    except Exception:
        logger.warning("OTLP exporter unavailable, falling back to console spans")
        return console_class()


def _instrument_libraries(app: Any, settings: Any) -> None:
    """Attach auto-instrumentation when the packages are installed.

    Each import is isolated: a deployment that installs only the tracing SDK
    still gets manual spans from ``app.core.tracing.span``.
    """
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls="healthz,readyz,metrics",
        )
    except Exception:
        logger.debug("fastapi instrumentation not installed")

    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        from app.db.session import engine

        SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    except Exception:
        logger.debug("sqlalchemy instrumentation not installed")

    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
    except Exception:
        logger.debug("redis instrumentation not installed")
