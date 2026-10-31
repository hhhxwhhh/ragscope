"""Outbound webhook endpoints.

Events are signed with HMAC-SHA256 over the raw body, and the signature header
carries the digest only — never the secret. Receivers must verify it, otherwise a
third party can forge a "sync completed" call.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

WEBHOOK_EVENTS = ("sync.completed", "sync.failed", "eval.completed", "ingest.failed")


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(1000))
    secret: Mapped[str] = mapped_column(String(64))
    events: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_delivery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
