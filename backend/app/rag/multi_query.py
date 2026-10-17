"""Multi-query expansion as a LangChain LCEL chain.

A single embedding of "X1 和 X2 的续航和充电有什么区别" may favour one topic.
The LLM rewrites it into complementary queries; each is retrieved separately and
all rankings are fused with RRF. Expansion is fail-open: no API key, timeout or
malformed output simply means single-query retrieval.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.core.config import get_settings
from app.rag.llm import get_chat_model

logger = logging.getLogger(__name__)

MULTI_QUERY_PROMPT = """你是检索查询扩展器。把用户问题改写成 {count} 个不同角度的检索 query，
用于在知识库中召回不同表述的相关资料。

要求：
1. 每行一个 query，不要编号、不要引号、不要解释。
2. 保持原问题中的专有名词、型号和数字不变。
3. 覆盖不同的关键词表述或子问题，但不要引入新事实。
4. 不要回答问题。"""

MULTI_QUERY_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", MULTI_QUERY_PROMPT),
        ("human", "{question}"),
    ]
)

_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.、)])\s*")
_WS_RE = re.compile(r"\s+")
MAX_QUERY_CHARS = 200


@dataclass
class MultiQueryResult:
    queries: list[str] = field(default_factory=list)
    applied: bool = False
    error: str | None = None


def parse_queries(raw: str, original: str, limit: int) -> list[str]:
    """Normalise LLM output into unique, non-trivial queries."""
    seen = {_WS_RE.sub(" ", original.strip())}
    queries: list[str] = []
    for line in raw.splitlines():
        candidate = _BULLET_RE.sub("", line).strip().strip("「」“”\"'")
        candidate = _WS_RE.sub(" ", candidate)
        if not candidate or len(candidate) > MAX_QUERY_CHARS:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        queries.append(candidate)
        if len(queries) >= limit:
            break
    return queries


async def expand_queries(query: str, count: int, timeout_seconds: float) -> MultiQueryResult:
    settings = get_settings()
    if not settings.deepseek_api_key:
        return MultiQueryResult(error="no_api_key")

    count = min(max(count, 1), 5)
    try:
        chain = MULTI_QUERY_PROMPT_TEMPLATE | get_chat_model().bind(
            temperature=0.3,
            max_completion_tokens=256,
        ) | StrOutputParser()
        raw = await asyncio.wait_for(
            chain.ainvoke({"count": count, "question": query}),
            timeout=timeout_seconds,
        )
        queries = parse_queries(raw or "", query, count)
        if not queries:
            return MultiQueryResult(error="invalid_expansion")
        return MultiQueryResult(queries=queries, applied=True)
    except TimeoutError:
        logger.warning("multi-query expansion timed out")
        return MultiQueryResult(error="multi_query_timeout")
    except Exception as exc:
        logger.warning("multi-query expansion failed: %s", exc)
        return MultiQueryResult(error=f"multi_query_error: {exc}"[:200])
