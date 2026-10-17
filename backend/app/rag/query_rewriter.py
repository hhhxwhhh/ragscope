"""Conversational query rewriting as a LangChain LCEL chain.

Follow-up questions such as “那它续航多久？” are meaningless to a retriever on
their own. When there is conversation history we ask the LLM for a standalone
query; any failure falls back to the original text so chat still works.
"""

import asyncio
import logging
from dataclasses import dataclass

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.core.config import get_settings
from app.rag.llm import get_chat_model

logger = logging.getLogger(__name__)

REWRITE_PROMPT = """你是检索查询改写器。根据对话历史，把用户最后一句改写成可以独立检索的完整问题。

要求：
1. 只输出改写后的问题，不要解释、不要引号、不要前缀。
2. 补全代词与省略的主语/对象，保留专有名词、型号和数字。
3. 如果最后一句已经完整，原样输出。
4. 不要回答这个问题。"""

REWRITE_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", REWRITE_PROMPT),
        (
            "human",
            "<对话历史>\n{history}\n</对话历史>\n\n用户最后一句：{question}",
        ),
    ]
)


@dataclass
class RewriteResult:
    query: str
    applied: bool
    error: str | None = None


def _history_block(history: list[tuple[str, str]]) -> str:
    labels = {"user": "用户", "assistant": "助手"}
    lines = [f"{labels.get(role, role)}：{content}" for role, content in history]
    return "\n".join(lines)


async def rewrite_query(
    question: str,
    history: list[tuple[str, str]],
) -> RewriteResult:
    settings = get_settings()
    if not settings.query_rewrite_enabled or not history:
        return RewriteResult(query=question, applied=False)
    if not settings.deepseek_api_key:
        return RewriteResult(query=question, applied=False, error="no_api_key")

    try:
        chain = REWRITE_PROMPT_TEMPLATE | get_chat_model().bind(
            temperature=0.0,
            max_completion_tokens=128,
        ) | StrOutputParser()
        rewritten = await asyncio.wait_for(
            chain.ainvoke(
                {
                    "history": _history_block(history),
                    "question": question,
                }
            ),
            timeout=settings.query_rewrite_timeout_seconds,
        )
        rewritten = (rewritten or "").strip().strip("「」“”\"'").strip()
        if not rewritten or len(rewritten) > 300:
            return RewriteResult(query=question, applied=False, error="invalid_rewrite")
        return RewriteResult(
            query=rewritten,
            applied=rewritten != question,
        )
    except TimeoutError:
        logger.warning("query rewrite timed out")
        return RewriteResult(query=question, applied=False, error="rewrite_timeout")
    except Exception as exc:
        logger.warning("query rewrite failed: %s", exc)
        return RewriteResult(query=question, applied=False, error=f"rewrite_error: {exc}"[:200])
