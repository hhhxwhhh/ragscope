"""Contextual compression: keep the sentences that carry the evidence.

A chunk is retrieved because *some* part of it matches. The rest — boilerplate,
adjacent sections, a long enumerated list where only one item matters — is paid
for twice: it consumes prompt budget and it dilutes the cross-encoder's view of
what the chunk is about. Compression removes the non-matching sentences before
the prompt is built.

Two implementations share one contract: return the same chunk list with
``content`` shortened, never reordered, never rewritten.

* **Lexical (default)** — token overlap between each sentence and the query.
  Cheap, offline, deterministic, and easy to explain: a sentence survives if it
  shares query terms or if it is the chunk's opening sentence (headings and topic
  sentences rarely contain the query verbatim but anchor the excerpt).
* **LLM (opt-in)** — the model copies out the sentences it would cite. To stop it
  from paraphrasing, only sentences that literally occur in the source are kept;
  anything the model invented is discarded. If that filter empties the chunk the
  original text is restored, so compression can only ever help.

Compression is deliberately monotonic on safety: it never adds text, never moves
chunks, and falls back to the uncompressed chunk on any error.
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

# Sentence boundaries for mixed CN/EN text. Newlines always split: they separate
# list items and headings, which must not be merged into one "sentence".
_SENTENCE_RE = re.compile(r"(?<=[。！？!?；;])\s*|\n+")
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

EXTRACT_PROMPT = """你是证据抽取器。从给定段落中选出与问题直接相关的句子。

要求：
1. 逐行输出原句，必须与原段落中的文字完全一致，不得改写、概括或翻译。
2. 只输出原句中出现过的句子，不要自己造句。
3. 最多 {max_sentences} 行，按原文顺序输出。
4. 如果没有相关句子，输出 NONE。"""

EXTRACT_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", EXTRACT_PROMPT),
        ("human", "问题：{query}\n\n段落：\n{passage}"),
    ]
)


@dataclass
class CompressResult:
    candidates: list[dict]
    applied: bool
    mode: str
    original_chars: int = 0
    compressed_chars: int = 0
    dropped_sentences: int = 0
    error: str | None = None
    per_chunk: list[dict] = field(default_factory=list)


def split_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_RE.split(text or "")]
    return [part for part in parts if part]


def _tokens(text: str) -> set[str]:
    """Token set for overlap scoring.

    A regex over ``[\\u4e00-\\u9fff]+`` would swallow a whole Chinese sentence as
    one token, so CJK runs go through jieba (already a dependency for BM25).
    Character bigrams are added on top because segmentation disagreements are
    the common failure mode here: the query says 退款, the passage says 退款申请,
    and jieba may cut it as 退款/申请 or 退款申请 depending on context.
    """
    tokens: set[str] = set()
    for match in _TOKEN_RE.finditer(text or ""):
        raw = match.group(0)
        if _CJK_RE.fullmatch(raw):
            for word in _jieba_cut(raw):
                if len(word) >= 2:
                    tokens.add(word)
            tokens.update(raw[index : index + 2] for index in range(len(raw) - 1))
        else:
            tokens.add(raw.lower())
    return tokens


def _jieba_cut(text: str) -> list[str]:
    try:
        import jieba

        return [word for word in jieba.lcut(text) if word.strip()]
    except Exception:  # noqa: BLE001 - segmentation is an optimisation, not a requirement
        return [text]


def lexical_compress(
    content: str,
    query_tokens: set[str],
    *,
    min_keep: int = 2,
    min_chars: int = 120,
) -> tuple[str, int]:
    """Return (compressed_text, dropped_sentence_count).

    The rule is deliberately simple and auditable: a sentence survives if it
    shares at least one content term with the query. A soft score band was tried
    first and rejected — with length-normalised scores the cut-off landed in a
    different place for every chunk length, so the same evidence was kept in a
    short chunk and dropped from a long one. Binary overlap is less elegant but
    behaves identically on every chunk, which is what a debugging engineer needs.

    Short chunks and chunks where nothing matches are returned untouched: the
    goal is to trim long, partly-relevant passages, not to shrink everything.
    """
    if len(content) <= min_chars:
        return content, 0
    sentences = split_sentences(content)
    if len(sentences) <= min_keep:
        return content, 0

    keep_indexes = {
        index
        for index, sentence in enumerate(sentences)
        if query_tokens & _tokens(sentence)
    }
    if not keep_indexes:
        return content, 0

    kept = [sentences[index] for index in sorted(keep_indexes)]
    dropped = len(sentences) - len(kept)
    if not kept or len("".join(kept)) >= len(content):
        return content, 0
    return "\n".join(kept), dropped


async def llm_extract(
    query: str,
    content: str,
    *,
    timeout_seconds: float,
    max_sentences: int = 6,
) -> str | None:
    """Model-selected sentences, filtered back against the source text.

    Returns the joined kept sentences, or ``None`` when the model is unavailable,
    produced nothing usable, or every line failed the verbatim check.
    """
    settings = get_settings()
    if not settings.deepseek_api_key:
        return None
    try:
        chain = (
            EXTRACT_PROMPT_TEMPLATE
            | get_chat_model().bind(temperature=0.0, max_completion_tokens=512)
            | StrOutputParser()
        )
        raw = await asyncio.wait_for(
            chain.ainvoke({"query": query, "passage": content, "max_sentences": max_sentences}),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        logger.warning("llm compress timed out")
        return None
    except Exception as exc:  # noqa: BLE001 - compression is best-effort
        logger.warning("llm compress failed: %s", exc)
        return None

    sentences = split_sentences(content)
    lookup = {sentence.strip(): sentence for sentence in sentences}
    kept: list[str] = []
    for line in (raw or "").splitlines():
        candidate = line.strip().strip("-•*·")
        if not candidate or candidate.upper() == "NONE":
            continue
        source = lookup.get(candidate)
        if source is None:
            # Tolerate minor trailing punctuation differences, nothing else.
            source = lookup.get(candidate.rstrip("。！？；.!?;"))
        if source is not None and source not in kept:
            kept.append(source)
    if not kept:
        return None
    return "\n".join(kept)


async def compress(
    query: str,
    candidates: list[dict],
    *,
    enabled: bool,
    mode: str,
    timeout_seconds: float,
    min_chars: int = 160,
) -> CompressResult:
    """Shorten every candidate's content in place-preserving order."""
    if not enabled or not candidates:
        return CompressResult(candidates=candidates, applied=False, mode="off")

    query_tokens = _tokens(query)
    compressed: list[dict] = []
    original_chars = 0
    compressed_chars = 0
    dropped_total = 0
    per_chunk: list[dict] = []
    error: str | None = None

    for candidate in candidates:
        content = candidate.get("content") or ""
        original_chars += len(content)
        new_content = content

        if mode == "llm" and len(content) > min_chars:
            extracted = await llm_extract(query, content, timeout_seconds=timeout_seconds)
            if extracted:
                new_content = extracted
            else:
                error = error or "llm_extract_unavailable"
                new_content, _ = lexical_compress(
                    content, query_tokens, min_chars=min_chars
                )
        elif len(content) > min_chars:
            new_content, _ = lexical_compress(content, query_tokens, min_chars=min_chars)

        dropped = 0
        if new_content != content:
            dropped = max(len(split_sentences(content)) - len(split_sentences(new_content)), 0)
            dropped_total += dropped

        item = dict(candidate)
        item["content"] = new_content
        item["compressed"] = new_content != content
        compressed.append(item)
        compressed_chars += len(new_content)
        per_chunk.append(
            {
                "chunk_id": str(candidate.get("chunk_id")),
                "before": len(content),
                "after": len(new_content),
                "dropped_sentences": dropped,
            }
        )

    applied = compressed_chars < original_chars
    return CompressResult(
        candidates=compressed,
        applied=applied,
        mode=mode,
        original_chars=original_chars,
        compressed_chars=compressed_chars,
        dropped_sentences=dropped_total,
        error=error,
        per_chunk=per_chunk,
    )
