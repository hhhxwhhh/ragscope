from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class RecentRunOut(BaseModel):
    id: UUID
    dataset_id: UUID
    dataset_name: str
    status: str
    recall_5: float | None
    mrr: float | None
    cases_total: int
    created_at: datetime


class DashboardSummary(BaseModel):
    kb_count: int
    document_count: int
    indexed_document_count: int
    chunk_count: int
    conversation_count: int
    message_count: int
    eval_dataset_count: int
    eval_case_count: int
    eval_run_count: int
    avg_latency_ms: float
    feedback_positive: int
    feedback_negative: int
    feedback_positive_rate: float
    avg_citation_rate: float
    invalid_citation_markers: int
    recent_runs: list[RecentRunOut]


class TrendPoint(BaseModel):
    date: str
    messages: int
    conversations: int
    eval_runs: int


class DashboardTrend(BaseModel):
    days: int
    points: list[TrendPoint]
