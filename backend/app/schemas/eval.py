from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.rag.prompt import DEFAULT_PROMPT_VARIANT, get_prompt_variant
from app.schemas.retrieval import RetrievalConfigIn


class RelevantRef(BaseModel):
    document_id: UUID
    chunk_index: int = Field(ge=0)
    filename: str | None = Field(default=None, max_length=255)


class EvalDatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)


class EvalDatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kb_id: UUID
    name: str
    description: str | None
    chunk_size: int
    chunk_overlap: int
    case_count: int = 0
    run_count: int = 0
    created_at: datetime
    updated_at: datetime


class EvalCaseCreate(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    relevant_refs: list[RelevantRef] = Field(default_factory=list)
    answerable: bool = True
    # Optional human label used by the routing report. Null keeps the case out
    # of routing accuracy, precision and recall.
    expected_mode: Literal["rag", "agent"] | None = None
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _check_refs(self) -> "EvalCaseCreate":
        if self.answerable and not self.relevant_refs:
            raise ValueError("可答案例至少需要标注一个相关 chunk")
        if not self.answerable and self.relevant_refs:
            raise ValueError("不可答案例不应标注相关 chunk")
        return self


class EvalCaseBulkCreate(BaseModel):
    cases: list[EvalCaseCreate] = Field(min_length=1, max_length=200)


class EvalCaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dataset_id: UUID
    question: str
    relevant_refs: list[dict[str, Any]]
    answerable: bool
    expected_mode: Literal["rag", "agent"] | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class EvalCaseUpdate(BaseModel):
    expected_mode: Literal["rag", "agent"] | None = None


class EvalRunCreate(BaseModel):
    config: RetrievalConfigIn | None = None
    base_run_id: UUID | None = None
    # Optional generation pass. Disabled by default so the normal evaluation
    # path stays deterministic and free.
    include_generation: bool = False
    prompt_variant: str = DEFAULT_PROMPT_VARIANT

    @field_validator("prompt_variant")
    @classmethod
    def _known_prompt_variant(cls, value: str) -> str:
        return get_prompt_variant(value).id


class EvalRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dataset_id: UUID
    kb_id: UUID
    base_run_id: UUID | None
    status: str
    config: dict[str, Any]
    metrics: dict[str, Any]
    progress: dict[str, Any]
    index_version: int
    include_generation: bool = False
    prompt_variant: str | None = None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    dataset_name: str | None = None


class EvalResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    case_id: UUID
    question: str
    answerable: bool
    relevant_refs: list[dict[str, Any]]
    retrieved: list[dict[str, Any]]
    metrics: dict[str, Any]
    refused: bool
    latency_ms: int
    answer: str | None = None
    error: str | None


class EvalRunDetail(EvalRunOut):
    results: list[EvalResultOut] = Field(default_factory=list)


class MetricDelta(BaseModel):
    key: str
    label: str
    base: float | None
    candidate: float | None
    delta: float | None


class CompareCase(BaseModel):
    case_id: UUID
    question: str
    answerable: bool
    base_rank: int | None
    candidate_rank: int | None
    base_refused: bool
    candidate_refused: bool
    outcome: Literal["better", "worse", "same"]
    base_metrics: dict[str, Any]
    candidate_metrics: dict[str, Any]


class EvalCompareOut(BaseModel):
    base: EvalRunOut
    candidate: EvalRunOut
    metric_deltas: list[MetricDelta]
    cases: list[CompareCase]
    improved: int
    degraded: int
    unchanged: int


class RoutingCaseOut(BaseModel):
    case_id: UUID
    question: str
    answerable: bool
    expected_mode: Literal["rag", "agent"] | None
    predicted_mode: Literal["rag", "agent"]
    correct: bool | None
    signals: list[str]
    reason: str


class RoutingReportOut(BaseModel):
    dataset_id: UUID
    total: int
    labeled: int
    unlabeled: int
    # None when there are no labelled cases (the report is still useful for
    # inspecting predictions and the agent escalation rate).
    accuracy: float | None
    agent_precision: float | None
    agent_recall: float | None
    agent_f1: float | None
    predicted_agent_rate: float
    expected_agent_rate: float | None
    # Keys: rag->rag, rag->agent, agent->rag, agent->agent.
    confusion: dict[str, int]
    cases: list[RoutingCaseOut]
