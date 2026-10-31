"""Source schemas: per-connector config validation at the API boundary.

The database stores connector config as JSONB, so validation has to happen
somewhere. Doing it in Pydantic means the fetchers can assume their config is
well-formed — a crawler that crashes halfway through because ``max_pages`` was a
string leaves a half-updated knowledge base and a confusing error.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

SourceType = Literal["web", "sitemap", "rss", "github"]


class WebConfig(BaseModel):
    start_urls: list[str] = Field(min_length=1, max_length=10)
    allowed_domains: list[str] = Field(default_factory=list)
    max_pages: int = Field(default=10, ge=1, le=200)
    max_depth: int = Field(default=1, ge=0, le=3)
    include_patterns: list[str] = Field(default_factory=list, max_length=10)
    exclude_patterns: list[str] = Field(default_factory=list, max_length=10)
    request_delay_seconds: float = Field(default=0.8, ge=0, le=10)
    respect_robots: bool = True
    # Escape hatch for corporate TLS-inspecting proxies; off by default.
    verify_tls: bool = True
    user_agent: str | None = Field(default=None, max_length=200)


class SitemapConfig(BaseModel):
    sitemap_url: str
    max_pages: int = Field(default=20, ge=1, le=200)
    include_patterns: list[str] = Field(default_factory=list, max_length=10)
    request_delay_seconds: float = Field(default=0.5, ge=0, le=10)
    respect_robots: bool = True
    verify_tls: bool = True
    user_agent: str | None = Field(default=None, max_length=200)


class RssConfig(BaseModel):
    feed_url: str
    max_items: int = Field(default=20, ge=1, le=100)
    verify_tls: bool = True
    user_agent: str | None = Field(default=None, max_length=200)


class GithubConfig(BaseModel):
    repo: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    path: str = Field(default="", max_length=300)
    ref: str | None = Field(default=None, max_length=100)
    glob: list[str] = Field(default_factory=lambda: ["*.md", "*.mdx", "*.txt", "*.rst"])
    max_files: int = Field(default=30, ge=1, le=100)
    verify_tls: bool = True


CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "web": WebConfig,
    "sitemap": SitemapConfig,
    "rss": RssConfig,
    "github": GithubConfig,
}


def validate_config(source_type: str, config: dict[str, Any]) -> dict[str, Any]:
    model = CONFIG_MODELS.get(source_type)
    if model is None:
        raise ValueError(f"未知的数据源类型：{source_type}")
    try:
        return model.model_validate(config or {}).model_dump()
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "config"
        raise ValueError(f"{location}: {first['msg']}") from exc


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source_type: SourceType
    config: dict[str, Any] = Field(default_factory=dict)
    schedule_minutes: int = Field(default=0, ge=0, le=10080)
    enabled: bool = True

    @model_validator(mode="after")
    def _validate(self) -> "SourceCreate":
        self.config = validate_config(self.source_type, self.config)
        return self


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: dict[str, Any] | None = None
    schedule_minutes: int | None = Field(default=None, ge=0, le=10080)
    enabled: bool | None = None


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kb_id: UUID
    name: str
    source_type: str
    config: dict[str, Any]
    schedule_minutes: int
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    last_status: str
    last_error: str | None
    created_at: datetime


class SyncRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_id: UUID
    status: str
    trigger: str
    stats: dict[str, Any]
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class SourcePreviewRequest(BaseModel):
    source_type: SourceType
    config: dict[str, Any]


class SourcePreviewOut(BaseModel):
    items: list[dict[str, Any]]
    errors: list[dict[str, str]]
    pages_fetched: int
