"""Celery application for long-running background work.

Why Celery at all when FastAPI already has ``BackgroundTasks``? Because the two
have different failure domains:

* ``BackgroundTasks`` runs *inside* the API process. A deploy or crash kills an
  in-flight ingestion, and the API's event loop shares CPU with the embedding
  model. That is acceptable for a single-user demo and wrong for a shared
  deployment.
* A Celery worker is a separate process with its own concurrency, retries and
  visibility timeout. Ingesting a PDF that takes 40 seconds no longer competes
  with answering a question.

The switch is a setting (``TASK_BACKEND=inline|celery``) rather than a fork: local
development and CI keep the zero-infrastructure path, production uses the worker.
"""

from app.core.env import configure_env

configure_env()

from celery import Celery  # noqa: E402

from app.core.config import get_settings  # noqa: E402

settings = get_settings()

celery_app = Celery(
    "ragscope",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_default_queue="ragscope",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    # Acknowledge after completion: a killed worker re-queues the ingestion
    # instead of leaving the document stuck in "parsing" until the stale sweep
    # notices it 15 minutes later.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_time_limit=1800,
    task_soft_time_limit=1500,
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    # Periodic recovery mirrors the startup sweep; it only matters when the API
    # process is not restarted after a worker dies.
    beat_schedule={
        "recover-stale-work": {
            "task": "ragscope.recover_stale",
            "schedule": 300.0,
        }
    },
)

__all__ = ["celery_app"]
