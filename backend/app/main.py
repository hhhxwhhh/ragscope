from app.core.env import configure_env

configure_env()

import logging  # noqa: E402
import secrets  # noqa: E402
import time  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from uuid import uuid4  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, PlainTextResponse  # noqa: E402

from app.api.router import api_router  # noqa: E402
from app.core import metrics  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.core.logging import configure_logging, request_id_var, trace_id_var  # noqa: E402
from app.core.tracing import configure_tracing, current_trace_id  # noqa: E402
from app.db.redis import close_redis  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.services.eval_service import recover_stale_runs  # noqa: E402
from app.services.ingestion import recover_stale_documents  # noqa: E402

settings = get_settings()
configure_logging(settings.log_level, settings.log_format)
logger = logging.getLogger(__name__)

HEALTH_PATHS = {
    "/healthz",
    "/readyz",
    f"{settings.api_prefix}/healthz",
    f"{settings.api_prefix}/readyz",
    "/metrics",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Recover documents left in an in-progress state by a previous crash/reload.
    async with SessionLocal() as db:
        try:
            recovered = await recover_stale_documents(db)
            if recovered:
                logger.warning("marked %s stale documents as failed", recovered)
        except Exception:
            await db.rollback()
            logger.exception("failed to recover stale documents at startup")

    async with SessionLocal() as db:
        try:
            recovered_runs = await recover_stale_runs(db)
            if recovered_runs:
                logger.warning("marked %s orphaned eval runs as failed", recovered_runs)
        except Exception:
            await db.rollback()
            logger.exception("failed to recover stale eval runs at startup")

    yield
    await close_redis()
    await engine.dispose()


app = FastAPI(
    title="RAGScope API",
    version=settings.app_version,
    description=(
        "Knowledge base Q&A with retrieval observability and evaluation.\n\n"
        "Every answer carries a retrieval trace, every trace can become an "
        "evaluation case, and every configuration change can be compared on a "
        "fixed dataset."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code},
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    request_id_var.set(request_id)
    metrics.HTTP_IN_FLIGHT.inc()
    started = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request failed method=%s path=%s request_id=%s",
            request.method,
            request.url.path,
            request_id,
        )
        raise
    finally:
        metrics.HTTP_IN_FLIGHT.inc(-1)

    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"

    trace_id = current_trace_id()
    if trace_id:
        trace_id_var.set(trace_id)
        response.headers["X-Trace-ID"] = trace_id

    route = getattr(request.scope.get("route"), "path", None) or metrics.normalise_route(
        request.url.path
    )
    metrics.observe_http(request.method, route, response.status_code, elapsed_ms / 1000)

    # Health checks run every few seconds in Compose; logging them adds noise.
    if request.url.path not in HEALTH_PATHS:
        logger.info(
            "%s %s status=%s duration_ms=%.1f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
    return response


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request) -> PlainTextResponse:
    """Prometheus scrape endpoint.

    Binding this to a private port or protecting it with a token is a deployment
    decision, so both are supported: set ``METRICS_TOKEN`` to require a bearer
    token, leave it empty for scrape-from-inside-the-cluster.
    """
    if not settings.metrics_enabled:
        return PlainTextResponse("metrics disabled\n", status_code=404)
    if settings.metrics_token:
        header = request.headers.get("Authorization", "")
        expected = f"Bearer {settings.metrics_token}"
        if not secrets.compare_digest(header, expected):
            return PlainTextResponse("forbidden\n", status_code=403)
    return PlainTextResponse(metrics.render(), media_type=metrics.CONTENT_TYPE_LATEST)


app.include_router(api_router, prefix=settings.api_prefix)

configure_tracing(app)
metrics.mark_info(settings.app_name, settings.app_version, settings.app_env)
