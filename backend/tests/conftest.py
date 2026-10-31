"""Shared pytest setup: isolated database, Redis DB and storage directories.

The environment is configured *before* importing the app because settings are
read once at import time (and the SQLAlchemy engine is created from them).
Every integration test starts from a clean database and an empty Redis DB.
"""

import asyncio
import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DB = "ragscope_test"
TEST_REDIS_DB = "1"
TEST_PASSWORD = "Passw0rd!123"

_tmp_root = Path(tempfile.mkdtemp(prefix="ragscope-tests-"))
os.environ.update(
    {
        "APP_ENV": "test",
        "LOG_LEVEL": "WARNING",
        "POSTGRES_DB": TEST_DB,
        "REDIS_DB": TEST_REDIS_DB,
        "SECRET_KEY": "pytest-only-secret-key-not-used-anywhere-else",
        "CHROMA_DIR": str(_tmp_root / "chroma"),
        "STORAGE_DIR": str(_tmp_root / "storage"),
        "EMBEDDING_DEVICE": "cpu",
        "RERANK_ENABLED": "false",
        "QUERY_REWRITE_ENABLED": "false",
        "MULTI_QUERY_ENABLED": "false",
        "RETRIEVAL_CACHE_ENABLED": "false",
    }
)

from app.core.config import get_settings  # noqa: E402
from app.db.redis import get_redis_client  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402

settings = get_settings()

TABLES = (
    "audit_logs",
    "api_keys",
    "agent_steps",
    "agent_runs",
    "answer_reviews",
    "eval_results",
    "eval_runs",
    "eval_cases",
    "eval_datasets",
    "message_feedbacks",
    "citations",
    "messages",
    "conversations",
    "retrieval_traces",
    "chunks",
    "documents",
    "knowledge_bases",
    "users",
)


async def _ensure_database() -> None:
    """Create the test database on first run (requires CREATEDB privileges)."""
    connection = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database="postgres",
    )
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", settings.postgres_db
        )
        if not exists:
            await connection.execute(f'CREATE DATABASE "{settings.postgres_db}"')
    finally:
        await connection.close()


def _upgrade_schema() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")


def pytest_sessionstart(session: pytest.Session) -> None:
    asyncio.run(_ensure_database())
    _upgrade_schema()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    shutil.rmtree(_tmp_root, ignore_errors=True)


@pytest.fixture(autouse=True)
async def reset_state(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    """Integration tests get a clean DB/Redis; unit tests skip the round trips."""
    if "integration" not in request.keywords:
        yield
        return

    async with engine.begin() as connection:
        await connection.execute(
            text(f"TRUNCATE TABLE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        )

    redis = get_redis_client()
    await redis.flushdb()
    yield


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    # https keeps the Secure refresh cookie inside httpx's cookie jar.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as http:
        yield http


Register = Callable[..., Awaitable[tuple[dict[str, Any], dict[str, str]]]]


@pytest.fixture
def register(client: AsyncClient) -> Register:
    async def _register(
        email: str | None = None,
        password: str = TEST_PASSWORD,
        display_name: str | None = "Test User",
    ) -> tuple[dict[str, Any], dict[str, str]]:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": email or f"user-{uuid4().hex[:10]}@example.com",
                "password": password,
                "display_name": display_name,
            },
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        return payload, {"Authorization": f"Bearer {payload['access_token']}"}

    return _register
