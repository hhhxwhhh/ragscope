from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


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
    page_number: int | None = None
    rank: int
    score: float | None
    snippet: str
    cited: bool


class FeedbackIn(BaseModel):
    rating: Literal[-1, 1]
    reason: str | None = Field(default=None, max_length=40)
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    message_id: UUID
    rating: int
    reason: str | None
    comment: str | None
    updated_at: datetime


class RetrievalTraceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    query: str
    rewritten_query: str
    rewrite_applied: bool
    sub_queries: list[str] = Field(default_factory=list)
    cache_hit: bool = False
    reranked: bool
    rerank_error: str | None
    config: dict[str, Any]
    candidates: dict[str, Any]
    stage_timings: dict[str, Any]
    created_at: datetime


class FeedbackCaseOut(BaseModel):
    dataset_id: UUID
    dataset_name: str
    case_id: UUID
    created: bool


class AgentStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step_index: int
    tool_name: str
    arguments: dict[str, Any]
    observation: dict[str, Any]
    status: str
    latency_ms: int


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    stopped_reason: str | None
    step_count: int
    tool_calls: int
    latency_ms: int | None
    error: str | None
    steps: list[AgentStepOut] = Field(default_factory=list)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: str
    content: str
    status: str
    latency_ms: int | None
    token_usage: dict[str, Any] | None
    citation_stats: dict[str, Any] | None = None
    rewrite_query: str | None
    created_at: datetime
    citations: list[CitationOut] = Field(default_factory=list)
    trace: RetrievalTraceOut | None = None
    feedback: FeedbackOut | None = None
    agent: AgentRunOut | None = None


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None
    # "rag" is the single-shot retrieval path; "agent" runs the bounded
    # tool-calling loop and then generates from the collected evidence.
    # "auto" picks between them with a cheap rule-based router, so simple
    # questions keep the low-latency path and multi-hop questions get tools.
    mode: Literal["rag", "agent", "auto"] = "rag"


class ChatResponse(BaseModel):
    conversation_id: UUID
    message: MessageOut
    # The path actually used. Useful when the caller asked for "auto".
    mode: Literal["rag", "agent"] | None = None
    routing_reason: str | None = None
