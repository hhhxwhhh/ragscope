from typing import Any

from pydantic import BaseModel, Field


class RetrievalConfigIn(BaseModel):
    vector_top_k: int | None = Field(default=None, ge=1, le=50)
    bm25_top_k: int | None = Field(default=None, ge=1, le=50)
    fusion_top_k: int | None = Field(default=None, ge=1, le=50)
    final_top_k: int | None = Field(default=None, ge=1, le=20)
    rrf_k: int | None = Field(default=None, ge=1, le=200)
    vector_weight: float | None = Field(default=None, ge=0, le=5)
    bm25_weight: float | None = Field(default=None, ge=0, le=5)
    min_vector_score: float | None = Field(default=None, ge=-1, le=1)
    use_rerank: bool | None = None
    rerank_timeout_seconds: float | None = Field(default=None, ge=0.5, le=30)


class DebugRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    config: RetrievalConfigIn | None = None


class CandidateOut(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    chunk_index: int
    heading_path: str | None = None
    content: str
    vector_score: float | None = None
    bm25_score: float | None = None
    vector_rank: int | None = None
    bm25_rank: int | None = None
    fused_score: float | None = None
    rerank_score: float | None = None


class DebugResponse(BaseModel):
    query: str
    rewritten_query: str
    rewrite_applied: bool
    rewrite_error: str | None
    reranked: bool
    rerank_error: str | None
    config: dict[str, Any]
    stage_timings: dict[str, float]
    counts: dict[str, int]
    candidates: dict[str, list[CandidateOut]]
