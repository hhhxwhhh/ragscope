"""LangChain chat models (DeepSeek via its OpenAI-compatible API).

Only this module knows which provider/model is used. The rest of the RAG layer
works with LangChain's ``BaseChatModel`` interface, so switching to another
provider means changing this factory rather than every prompt chain.

Models cache their underlying HTTP client, so they are process-wide singletons
keyed by the parameters that affect the request. Tests can call
``clear_model_cache()`` after monkeypatching settings.
"""

from functools import lru_cache
from typing import Any

from langchain_openai import ChatOpenAI

from app.core.config import get_settings
from app.core.errors import ServiceUnavailableError


def _build_chat_model(
    *,
    temperature: float,
    max_tokens: int,
    timeout: float,
    max_retries: int,
) -> ChatOpenAI:
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise ServiceUnavailableError("服务端未配置 DEEPSEEK_API_KEY，无法生成回答")
    return ChatOpenAI(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=temperature,
        # langchain-openai maps this to max_completion_tokens, which DeepSeek
        # accepts; stream_usage asks for the final usage chunk.
        max_completion_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
        stream_usage=True,
    )


@lru_cache(maxsize=8)
def _cached_chat_model(
    temperature: float,
    max_tokens: int,
    timeout: float,
    max_retries: int,
) -> ChatOpenAI:
    return _build_chat_model(
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
    )


def get_chat_model() -> ChatOpenAI:
    """Model used for query rewriting, expansion and grounded answers."""
    settings = get_settings()
    return _cached_chat_model(
        temperature=0.2,
        max_tokens=settings.max_answer_tokens,
        timeout=settings.llm_timeout_seconds,
        max_retries=1,
    )


def get_agent_model() -> ChatOpenAI:
    """Model used for the agent's planning decisions.

    The request timeout is the per-turn decision budget; the graph itself
    handles step and tool budgets.
    """
    settings = get_settings()
    return _cached_chat_model(
        temperature=settings.agent_temperature,
        max_tokens=settings.agent_decision_max_tokens,
        timeout=settings.agent_turn_timeout_seconds,
        max_retries=0,
    )


def clear_model_cache() -> None:
    _cached_chat_model.cache_clear()


def usage_to_dict(usage: dict[str, Any] | None) -> dict[str, int] | None:
    """Normalise LangChain's ``usage_metadata`` into a flat, storable dict."""
    if not isinstance(usage, dict) or not usage:
        return None
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    total = int(usage.get("total_tokens") or input_tokens + output_tokens)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
    }
