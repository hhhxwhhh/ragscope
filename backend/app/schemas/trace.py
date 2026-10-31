"""Read models for the retrieval-trace explorer.

The stored trace is a JSONB blob that grew a field at a time (graph, then CRAG,
then compression). Rather than mirroring every field into a strict model — which
would break the moment a stage is added — the detail view keeps ``candidates``,
``stage_timings``, ``crag`` and ``compress`` as raw dicts and lets the UI render
whatever keys are present. Only the fields the API itself filters and sorts on
are typed.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TraceSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    message_id: UUID | None = None
    query: str
    rewritten_query: str
    rewrite_applied: bool = False
    cache_hit: bool = False
    reranked: bool = False
    created_at: datetime
    # Denormalised for the list view so the table can sort and colour by them
    # without loading every candidate blob.
    total_ms: float = 0.0
    final_count: int = 0
    retrieval_mode: str = "rag"


class TraceDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kb_id: UUID
    message_id: UUID | None = None
    query: str
    rewritten_query: str
    rewrite_applied: bool
    sub_queries: list[str] = Field(default_factory=list)
    cache_hit: bool = False
    reranked: bool = False
    rerank_error: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    candidates: dict[str, Any] = Field(default_factory=dict)
    stage_timings: dict[str, Any] = Field(default_factory=dict)
    graph: dict[str, Any] = Field(default_factory=dict)
    crag: dict[str, Any] = Field(default_factory=dict)
    compress: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
