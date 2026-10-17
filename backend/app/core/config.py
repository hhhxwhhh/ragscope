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
    app_env: str = "dev"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

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

    # Chat
    llm_timeout_seconds: float = 60.0
    max_answer_tokens: int = 1024
    history_messages: int = 6
    max_context_chars: int = 6000

    # Storage
    chroma_dir: str = str(BACKEND_DIR / "data" / "chroma")
    storage_dir: str = str(BACKEND_DIR / "data" / "storage")
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
