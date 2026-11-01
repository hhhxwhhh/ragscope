from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_name: str = "RAGScope"
    app_version: str = "0.5.0"
    app_env: str = "dev"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_format: str = "text"  # text | json
    cors_origins: str = "http://localhost:5173"

    # Observability
    metrics_enabled: bool = True
    # When set, /metrics requires `Authorization: Bearer <token>`.
    metrics_token: str = ""
    otel_enabled: bool = False
    otel_service_name: str = "ragscope-api"
    otel_exporter_endpoint: str = ""
    otel_sample_ratio: float = 0.2

    # Auth
    secret_key: str = "dev-only-secret-change-me-use-32-bytes-or-more-in-prod"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    refresh_cookie_name: str = "refresh_token"
    login_max_attempts: int = 5
    login_attempt_window_seconds: int = 600

    # PostgreSQL
    postgres_user: str = "ragscope"
    postgres_password: str = "ragscope"
    postgres_db: str = "ragscope"
    postgres_host: str = "localhost"
    postgres_port: int = 5433

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6380
    redis_db: int = 0

    # LLM
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # Models
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_device: str = ""
    embedding_batch_size: int = 32
    reranker_model: str = "BAAI/bge-reranker-base"
    rerank_enabled: bool = True
    rerank_timeout_seconds: float = 3.0

    # Retrieval
    vector_top_k: int = 20
    bm25_top_k: int = 20
    fusion_top_k: int = 20
    final_top_k: int = 5
    rrf_k: int = 60
    min_vector_score: float = 0.45
    query_rewrite_enabled: bool = True
    query_rewrite_timeout_seconds: float = 8.0
    multi_query_enabled: bool = False
    multi_query_count: int = 3
    multi_query_timeout_seconds: float = 8.0
    retrieval_cache_enabled: bool = True
    retrieval_cache_ttl_seconds: int = 300
    # Sentence-window expansion: how many chunks on each side of a selected
    # chunk are pulled into the context. 0 disables it (chunks are returned as
    # indexed); it is off by default because it multiplies prompt tokens by
    # roughly 2 * window_size + 1.
    window_size: int = 0
    # Semantic answer cache (embedding similarity over recent questions)
    # 0 = unlimited. Applies per knowledge base per UTC day.
    daily_token_budget: int = 0
    semantic_cache_enabled: bool = True
    semantic_cache_threshold: float = 0.93
    semantic_cache_max_entries: int = 200

    # GraphRAG: one-hop expansion on top of the fused candidates. It is a no-op
    # until a knowledge base has had its graph built.
    graph_rag_enabled: bool = True
    graph_extra_top_k: int = 3
    graph_max_chunks_per_build: int = 200
    graph_extract_timeout_seconds: float = 60.0

    # CRAG: grade the evidence before answering and retry when it is weak.
    # Off by default because a retry round is an extra embedding + BM25 pass on
    # every ambiguous question; enable it per knowledge base where recall matters
    # more than latency.
    crag_enabled: bool = False
    crag_use_llm_grader: bool = False
    crag_grader_timeout_seconds: float = 4.0
    crag_retry_timeout_seconds: float = 8.0
    # Thresholds are read against the strongest available signal (rerank first,
    # dense cosine otherwise), both normalised to 0..1.
    crag_correct_threshold: float = 0.55
    crag_ambiguous_threshold: float = 0.30
    crag_retry_top_k: int = 10
    # Dense floor for the retry pass: relaxing it is the point, since the first
    # pass may have rejected the right chunk for being phrased differently.
    crag_relax_factor: float = 0.6
    crag_retry_add_limit: int = 3

    # Contextual compression: trim non-matching sentences out of each chunk
    # before the prompt is assembled. "lexical" is free and offline; "llm" asks
    # the model to copy the sentences it would cite (verbatim-checked).
    compress_enabled: bool = False
    compress_mode: str = "lexical"
    compress_timeout_seconds: float = 6.0
    compress_min_chars: int = 160

    # Answer grounding: after an answer is generated, judge each claim against the
    # retrieved context and flag hallucinations. Off by default because it is an
    # extra LLM call per answer; enable it for high-stakes knowledge bases.
    answer_grounding_enabled: bool = False
    # A hard floor: below this grounded ratio the report is marked ungrounded and
    # the UI shows a warning. Only applies when the judge itself ran (source=llm),
    # so a broken judge never falsely flags a good answer.
    answer_grounding_threshold: float = 0.5
    answer_grounding_timeout_seconds: float = 8.0

    # Chat
    llm_timeout_seconds: float = 60.0
    max_answer_tokens: int = 1024
    history_messages: int = 6
    max_context_chars: int = 6000

    # Agent (bounded tool-calling retrieval)
    agent_enabled: bool = True
    agent_max_steps: int = 5
    agent_max_evidence: int = 10
    agent_turn_timeout_seconds: float = 30.0
    # Generous enough for a cold CPU embedder/reranker on the first tool call;
    # fail-open keeps the run useful even if a tool does hit this ceiling.
    agent_tool_timeout_seconds: float = 60.0
    agent_temperature: float = 0.1
    agent_decision_max_tokens: int = 512

    # Deep research (planner / researcher / critic graph)
    deep_enabled: bool = True
    deep_max_iterations: int = 2

    # Connectors / scheduled sync
    sync_enabled: bool = True
    sync_scheduler_interval_seconds: int = 120
    sync_max_items_per_run: int = 50
    sync_max_document_chars: int = 200_000
    github_token: str = ""

    # Background tasks
    task_backend: str = "inline"  # inline | celery
    celery_queue: str = "ragscope"

    # Public (API key) surface
    public_rate_limit_per_minute: int = 60

    # Storage
    chroma_dir: str = str(BACKEND_DIR / "data" / "chroma")
    storage_dir: str = str(BACKEND_DIR / "data" / "storage")
    # Offline corpus packs produced by scripts/crawl_corpus.py
    corpus_dir: str = str(ROOT_DIR / "data" / "corpus")
    max_upload_bytes: int = 5 * 1024 * 1024

    @property
    def database_url(self) -> str:
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
