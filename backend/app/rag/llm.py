"""DeepSeek client (OpenAI-compatible API)."""

from functools import lru_cache

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.errors import ServiceUnavailableError


@lru_cache(maxsize=1)
def get_llm_client() -> AsyncOpenAI:
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise ServiceUnavailableError("服务端未配置 DEEPSEEK_API_KEY，无法生成回答")
    return AsyncOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=settings.llm_timeout_seconds,
        max_retries=1,
    )
