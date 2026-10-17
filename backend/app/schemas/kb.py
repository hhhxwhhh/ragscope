from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class KbCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    chunk_size: int = Field(default=400, ge=100, le=2000)
    chunk_overlap: int = Field(default=60, ge=0, le=500)

    @model_validator(mode="after")
    def _check_overlap(self) -> "KbCreate":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        return self


class KbUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    chunk_size: int | None = Field(default=None, ge=100, le=2000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=500)
    retrieval_config: dict[str, Any] | None = None


class KbOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    chunk_size: int
    chunk_overlap: int
    index_version: int
    retrieval_config: dict[str, Any]
    created_at: datetime
    updated_at: datetime
