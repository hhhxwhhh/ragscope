"""Grounded answer generation as an LCEL chain.

``ANSWER_PROMPT | ChatOpenAI`` is reused by single-shot RAG, agent runs and the
non-streaming JSON endpoint. Streaming yields plain text deltas plus the final
usage metadata; the HTTP layer only has to translate them into SSE events.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage

from app.rag.llm import get_chat_model, usage_to_dict
from app.rag.prompt import ANSWER_PROMPT, build_answer_inputs


@dataclass
class AnswerChunk:
    text: str
    usage: dict[str, int] | None = None


def _content_text(content: Any) -> str:
    """OpenAI chat content is a string; some providers stream content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in {"text", "text_delta"}:
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return "" if content is None else str(content)


def _chain():
    return ANSWER_PROMPT | get_chat_model()


async def complete_answer(
    question: str,
    candidates: list[dict[str, Any]],
    history: list[tuple[str, str]],
    *,
    max_context_chars: int,
) -> tuple[str, dict[str, int] | None]:
    message: BaseMessage = await _chain().ainvoke(
        build_answer_inputs(
            question,
            candidates,
            history,
            max_context_chars=max_context_chars,
        )
    )
    return _content_text(message.content), usage_to_dict(message.usage_metadata)


async def stream_answer(
    question: str,
    candidates: list[dict[str, Any]],
    history: list[tuple[str, str]],
    *,
    max_context_chars: int,
) -> AsyncIterator[AnswerChunk]:
    inputs = build_answer_inputs(
        question,
        candidates,
        history,
        max_context_chars=max_context_chars,
    )
    async for chunk in _chain().astream(inputs):
        text = _content_text(chunk.content)
        usage = usage_to_dict(chunk.usage_metadata)
        if text or usage is not None:
            yield AnswerChunk(text=text, usage=usage)
