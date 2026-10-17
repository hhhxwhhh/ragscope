#!/bin/sh
set -e

# Migrations are idempotent and run before the first worker starts, so a fresh
# `docker compose up` needs no manual alembic step.
alembic upgrade head

# One worker by default: every process loads its own embedding/reranker model,
# and the in-process BackgroundTasks (ingestion, eval runs) assume a single API
# container. Scale out with a real queue before raising WEB_CONCURRENCY.
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "${WEB_CONCURRENCY:-1}" \
    --proxy-headers \
    --forwarded-allow-ips "*"
