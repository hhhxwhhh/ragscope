from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kb_id: UUID
    filename: str
    ext: str
    size_bytes: int
    status: str
    error_stage: str | None
    error_message: str | None
    chunk_count: int
    indexed_at: datetime | None
    created_at: datetime


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chunk_index: int
    content: str
    heading_path: str | None
    page_number: int | None
    char_start: int
    char_end: int
