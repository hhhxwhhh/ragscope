"""Grounded answer generation as an LCEL chain.

``build_answer_prompt(variant) | ChatOpenAI`` is reused by single-shot RAG,
agent runs and generation evaluation. Streaming yields plain text deltas plus
the final usage metadata; the HTTP layer only has to translate them into SSE
events.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage

from app.rag.llm import get_chat_model, usage_to_dict
from app.rag.prompt import build_answer_inputs, build_answer_prompt


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


def _chain(prompt_variant: str | None = None):
    return build_answer_prompt(prompt_variant or "grounded") | get_chat_model()


async def complete_answer(
    question: str,
    candidates: list[dict[str, Any]],
    history: list[tuple[str, str]],
    *,
    max_context_chars: int,
    prompt_variant: str | None = None,
) -> tuple[str, dict[str, int] | None]:
    message: BaseMessage = await _chain(prompt_variant).ainvoke(
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
    prompt_variant: str | None = None,
) -> AsyncIterator[AnswerChunk]:
    inputs = build_answer_inputs(
        question,
        candidates,
        history,
        max_context_chars=max_context_chars,
    )
    async for chunk in _chain(prompt_variant).astream(inputs):
        text = _content_text(chunk.content)
        usage = usage_to_dict(chunk.usage_metadata)
        if text or usage is not None:
            yield AnswerChunk(text=text, usage=usage)
