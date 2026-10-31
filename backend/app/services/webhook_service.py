"""Webhook delivery.

Events worth telling the outside world about are the ones that happen without a
user watching: a scheduled sync finished, an evaluation run ended, an ingestion
failed. Each delivery is signed so the receiver can prove it came from us.

Delivery semantics are **at-most-once with bounded retries**, not a queue:

* dispatch happens in a background task (asyncio or Celery depending on
  ``TASK_BACKEND``) so the sync/eval path never waits on someone's HTTP endpoint;
* 3 attempts with 1s/3s/9s backoff for timeouts and 5xx;
* 4xx is *not* retried — a 404 means the receiver's URL is wrong, and retrying it
  only makes noise;
* every endpoint stores its last status and error, so "are my webhooks working?"
  is answerable from the UI/API instead of from logs.

A real system eventually wants a durable outbox (a delivery table + worker). This
implementation keeps the payload small and the failure visible; the comment says
where the boundary is rather than pretending it is a message bus.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.webhook import WEBHOOK_EVENTS, WebhookEndpoint

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0
BACKOFF_SECONDS = (1, 3, 9)


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def new_secret() -> str:
    return secrets.token_hex(24)


async def list_endpoints(db: AsyncSession, user_id: UUID) -> list[WebhookEndpoint]:
    rows = await db.scalars(
        select(WebhookEndpoint)
        .where(WebhookEndpoint.user_id == user_id)
        .order_by(WebhookEndpoint.created_at.desc())
    )
    return list(rows)


async def create_endpoint(
    db: AsyncSession,
    user_id: UUID,
    *,
    url: str,
    events: list[str],
) -> WebhookEndpoint:
    from app.core.errors import BadRequestError

    unknown = sorted(set(events) - set(WEBHOOK_EVENTS))
    if unknown:
        raise BadRequestError(f"未知事件：{', '.join(unknown)}")
    if not events:
        raise BadRequestError("至少订阅一个事件")
    endpoint = WebhookEndpoint(
        user_id=user_id, url=url, secret=new_secret(), events=sorted(set(events))
    )
    db.add(endpoint)
    await db.flush()
    await db.refresh(endpoint)
    return endpoint


async def delete_endpoint(db: AsyncSession, endpoint: WebhookEndpoint) -> None:
    await db.delete(endpoint)
    await db.flush()


async def notify(
    db: AsyncSession,
    *,
    owner_id: UUID,
    event: str,
    payload: dict[str, Any],
) -> int:
    """Find matching endpoints and schedule delivery. Never raises."""
    try:
        endpoints = (
            await db.scalars(
                select(WebhookEndpoint).where(
                    WebhookEndpoint.user_id == owner_id,
                    WebhookEndpoint.enabled.is_(True),
                )
            )
        ).all()
        targets = [item for item in endpoints if event in (item.events or [])]
    except Exception:
        logger.warning("webhook lookup failed event=%s", event, exc_info=True)
        return 0

    body_payload = {
        "event": event,
        "sent_at": datetime.now(UTC).isoformat(),
        **payload,
    }
    for endpoint in targets:
        if (get_settings().task_backend or "inline").lower() == "celery":
            try:
                from app.workers.celery_app import celery_app

                celery_app.send_task(
                    "ragscope.webhook",
                    args=[str(endpoint.id), event, body_payload],
                    queue=get_settings().celery_queue,
                )
                continue
            except Exception:  # pragma: no cover - broker down
                logger.exception("celery webhook dispatch failed; falling back to asyncio")
        asyncio.create_task(deliver(endpoint.id, event, body_payload))
    return len(targets)


async def deliver(endpoint_id: UUID, event: str, payload: dict[str, Any]) -> bool:
    async with SessionLocal() as db:
        endpoint = await db.get(WebhookEndpoint, endpoint_id)
        if endpoint is None or not endpoint.enabled:
            return False

        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "RAGScope-Webhook/0.5",
            "X-RAGScope-Event": event,
            "X-RAGScope-Signature": sign(endpoint.secret, body),
            "X-RAGScope-Delivery": secrets.token_hex(8),
        }

        last_error = ""
        for attempt, delay in enumerate((0, *BACKOFF_SECONDS)):
            if delay:
                await asyncio.sleep(delay)
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                    response = await client.post(endpoint.url, content=body, headers=headers)
                endpoint.last_status = str(response.status_code)
                endpoint.last_delivery_at = datetime.now(UTC)
                if response.status_code < 400:
                    endpoint.last_error = None
                    await db.commit()
                    return True
                last_error = f"HTTP {response.status_code}"
                if response.status_code < 500:
                    # 4xx will not get better by retrying.
                    break
            except Exception as exc:
                last_error = f"{exc.__class__.__name__}: {exc}"
                logger.info(
                    "webhook attempt failed endpoint=%s attempt=%s error=%s",
                    endpoint.id,
                    attempt + 1,
                    last_error,
                )

        endpoint.last_status = "failed"
        endpoint.last_error = last_error[:1000]
        endpoint.last_delivery_at = datetime.now(UTC)
        await db.commit()
        logger.warning("webhook delivery failed endpoint=%s event=%s", endpoint.id, event)
        return False


async def send_test(endpoint: WebhookEndpoint) -> dict[str, Any]:
    payload = {
        "event": "webhook.test",
        "sent_at": datetime.now(UTC).isoformat(),
        "message": "这是一条来自 RAGScope 的测试事件",
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(
                endpoint.url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-RAGScope-Event": "webhook.test",
                    "X-RAGScope-Signature": sign(endpoint.secret, body),
                },
            )
        return {"status_code": response.status_code, "ok": response.status_code < 400}
    except Exception as exc:
        return {"status_code": None, "ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
