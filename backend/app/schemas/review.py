from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.review import ISSUE_TAGS
from app.schemas.chat import CitationOut

ReviewVerdictValue = Literal["pass", "needs_fix", "fail"]
ReviewIssueTag = Literal[
    "unsupported_claim",
    "wrong_citation",
    "incomplete",
    "off_topic",
    "refusal_error",
    "formatting",
]


class ReviewIn(BaseModel):
    verdict: ReviewVerdictValue
    issue_tags: list[ReviewIssueTag] = Field(default_factory=list, max_length=len(ISSUE_TAGS))
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("issue_tags")
    @classmethod
    def _dedupe_tags(cls, value: list[str]) -> list[str]:
        # Preserve selection order while preventing double-counted summaries.
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _verdict_and_tags_match(self) -> "ReviewIn":
        if self.verdict == "pass" and self.issue_tags:
            raise ValueError("判定为通过时不应选择问题标签")
        if self.verdict != "pass" and not self.issue_tags:
            raise ValueError("判定为需修改或不通过时，至少选择一个标签")
        return self


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    message_id: UUID
    reviewer_id: UUID
    verdict: str
    issue_tags: list[str] = Field(default_factory=list)
    note: str | None
    created_at: datetime
    updated_at: datetime


class ReviewQueueItem(BaseModel):
    message_id: UUID
    conversation_id: UUID
    conversation_title: str
    question: str
    answer: str
    message_status: str
    prompt_variant: str | None = None
    latency_ms: int | None = None
    citation_stats: dict[str, Any] | None = None
    citations: list[CitationOut] = Field(default_factory=list)
    review: ReviewOut | None = None
    created_at: datetime


class ReviewQueuePage(BaseModel):
    items: list[ReviewQueueItem]
    total: int
    offset: int
    limit: int


class ReviewSummary(BaseModel):
    total_answers: int
    reviewed: int
    unreviewed: int
    coverage: float
    pass_count: int
    needs_fix_count: int
    fail_count: int
    pass_rate: float
    verdict_counts: dict[str, int]
    issue_counts: dict[str, int]
