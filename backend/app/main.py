from app.core.env import configure_env

configure_env()

import time  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from uuid import uuid4  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app.api.router import api_router  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.session import engine  # noqa: E402

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
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
