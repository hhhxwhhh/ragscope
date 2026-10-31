from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

WebhookEvent = Literal["sync.completed", "sync.failed", "eval.completed", "ingest.failed"]


class WebhookCreate(BaseModel):
    url: str = Field(min_length=8, max_length=1000, pattern=r"^https?://")
    events: list[WebhookEvent] = Field(min_length=1, max_length=8)


class WebhookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    # Returned on purpose: the receiver needs it to verify the HMAC signature.
    secret: str
    events: list[str]
    enabled: bool
    last_status: str | None
    last_error: str | None
    last_delivery_at: datetime | None
    created_at: datetime


class WebhookTestOut(BaseModel):
    status_code: int | None
    ok: bool
    error: str | None = None
