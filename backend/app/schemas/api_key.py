from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(default_factory=lambda: ["kb:read", "chat"])
    description: str | None = Field(default=None, max_length=500)
    expires_in_days: int | None = Field(default=None, ge=1, le=730)


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    description: str | None
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    usable: bool = True


class ApiKeyCreatedOut(ApiKeyOut):
    # Returned exactly once; there is no endpoint that can reveal it again.
    key: str


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_type: str
    action: str
    resource_type: str | None
    resource_id: str | None
    request_id: str | None
    ip_address: str | None
    detail: dict[str, Any]
    created_at: datetime


class PublicChunk(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    chunk_index: int
    heading_path: str | None
    page_number: int | None
    content: str
    score: float | None = None
    rerank_score: float | None = None


class PublicSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    use_rerank: bool | None = None
    use_multi_query: bool | None = None


class PublicSearchResponse(BaseModel):
    query: str
    rewritten_query: str
    chunks: list[PublicChunk]
    trace: dict[str, Any]
    latency_ms: float


class PublicAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    prompt_variant: str | None = None
    max_context_chars: int | None = Field(default=None, ge=1000, le=20000)


class PublicAskResponse(BaseModel):
    answer: str
    citations: list[PublicChunk]
    refusal: bool
    usage: dict[str, int] | None
    latency_ms: float
    trace: dict[str, Any]
