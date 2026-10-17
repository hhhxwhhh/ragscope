from app.core.env import configure_env

configure_env()

import logging  # noqa: E402
import time  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from uuid import uuid4  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from app.api.router import api_router  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.redis import close_redis  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.services.ingestion import recover_stale_documents  # noqa: E402

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


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

    yield
    await close_redis()
    await engine.dispose()


app = FastAPI(
    title="RAGScope API",
    version="0.1.0",
    description="Knowledge base Q&A with retrieval observability and evaluation.",
    lifespan=lifespan,
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
    started = time.perf_counter()

    response = await call_next(request)

    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
    return response


app.include_router(api_router, prefix=settings.api_prefix)
