from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kb_id: UUID
    title: str
    last_message_at: datetime
    created_at: datetime


class CitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chunk_id: UUID | None
    document_id: UUID | None
    filename: str
    chunk_index: int
    heading_path: str | None
    rank: int
    score: float | None
    snippet: str
    cited: bool


class RetrievalTraceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    query: str
    rewritten_query: str
    rewrite_applied: bool
    reranked: bool
    rerank_error: str | None
    config: dict[str, Any]
    candidates: dict[str, Any]
    stage_timings: dict[str, Any]
    created_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: str
    content: str
    status: str
    latency_ms: int | None
    token_usage: dict[str, Any] | None
    rewrite_query: str | None
    created_at: datetime
    citations: list[CitationOut] = Field(default_factory=list)
    trace: RetrievalTraceOut | None = None


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None


class ChatResponse(BaseModel):
    conversation_id: UUID
    message: MessageOut
