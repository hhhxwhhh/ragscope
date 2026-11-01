"""Answer faithfulness / grounding check.

After an answer is generated we ask the LLM to break it into atomic claims and
judge each one against the retrieved context: is this claim *directly* supported
by the evidence, or is it an unsupported addition (a hallucination)? The result
is a :class:`FaithfulnessReport` — supported/total counts, a grounded ratio, and
the per-claim breakdown for the UI.

This is a *guard*, not a gate, by default. On any failure (no key, timeout,
unparseable output) it fails open: the answer is kept and the report is marked
``source="fallback"`` with ``grounded=True``. The threshold only bites when the
judge itself succeeded, so a broken judge can never silently flag a good answer.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

from langchain_core.output_parsers import StrOutputParser

from app.core.config import get_settings
from app.rag.llm import get_chat_model
from app.rag.prompt import FAITHFULNESS_PROMPT_TEMPLATE

LOGGER = logging.getLogger("ragscope.faithfulness")


@dataclass
class ClaimVerdict:
    claim: str
    supported: bool
    reason: str = ""


@dataclass
class FaithfulnessReport:
    claims: list[ClaimVerdict] = field(default_factory=list)
    supported_count: int = 0
    total: int = 0
    grounded_ratio: float = 1.0
    grounded: bool = True
    error: str | None = None
    source: str = "fallback"  # "llm" | "fallback"

    def to_dict(self) -> dict:
        return {
            "claims": [
                {"claim": c.claim, "supported": c.supported, "reason": c.reason}
                for c in self.claims
            ],
            "supported_count": self.supported_count,
            "total": self.total,
            "grounded_ratio": self.grounded_ratio,
            "grounded": self.grounded,
            "error": self.error,
            "source": self.source,
        }


def _context_block(candidates: list[dict], max_chars: int | None = None) -> str:
    """Render the evidence the way the answer prompt rendered it.

    The budget defaults to ``max_context_chars`` — the same cap the answer
    prompt uses — because a judge that sees *less* than the model did calls
    grounded claims ungrounded. That is exactly what happened when
    sentence-window expansion tripled the context: generation used ~4.2k chars
    while the judge still truncated at 3k, and the grounding rate fell 12 points
    for a reason that had nothing to do with the model.

    There is deliberately no cap on the number of candidates either: trimming
    the tail would silently drop sources the answer is allowed to cite.
    """
    limit = max_chars if max_chars is not None else get_settings().max_context_chars
    parts: list[str] = []
    used = 0
    for index, candidate in enumerate(candidates, start=1):
        # Retrieval candidates carry their chunk body under "content" — the same
        # key the compressor, the CRAG grader and the prompt builder all use.
        text = (candidate.get("content") or "").strip()
        if not text:
            continue
        if used + len(text) > limit:
            text = text[: max(0, limit - used)].rstrip() + "…"
        parts.append(f"[文档 {index}] {text}")
        used += len(text)
        if used >= limit:
            break
    return "\n\n".join(parts)


async def check_faithfulness(
    answer: str,
    candidates: list[dict],
    *,
    timeout_seconds: float | None = None,
    max_context_chars: int | None = None,
) -> FaithfulnessReport:
    """Judge whether ``answer`` is grounded in ``candidates``.

    Always returns a report; on any failure it is a fail-open fallback so the
    caller can store it without special-casing errors. Pass
    ``max_context_chars`` when the caller knows the budget the answer was
    generated under; otherwise the setting is used.
    """
    settings = get_settings()
    timeout_seconds = timeout_seconds or settings.answer_grounding_timeout_seconds

    answer = (answer or "").strip()
    if not answer:
        return FaithfulnessReport(source="fallback", error="empty_answer")
    context = _context_block(candidates, max_chars=max_context_chars)
    if not context:
        return FaithfulnessReport(source="fallback", error="empty_context")

    if not settings.deepseek_api_key:
        return FaithfulnessReport(source="fallback", error="no_api_key")

    try:
        chain = (
            FAITHFULNESS_PROMPT_TEMPLATE
            | get_chat_model().bind(temperature=0.0, max_completion_tokens=1024)
            | StrOutputParser()
        )
        raw = await asyncio.wait_for(
            chain.ainvoke({"answer": answer, "context": context}),
            timeout=timeout_seconds,
        )
        return _parse_report(raw, threshold=settings.answer_grounding_threshold)
    except TimeoutError:
        LOGGER.warning("faithfulness check timed out after %.1fs", timeout_seconds)
        return FaithfulnessReport(source="fallback", error="timeout")
    except Exception as exc:  # noqa: BLE001 - never let the guard break the answer
        LOGGER.warning("faithfulness check failed: %s", exc)
        return FaithfulnessReport(source="fallback", error=type(exc).__name__)


def _parse_report(raw: str, *, threshold: float) -> FaithfulnessReport:
    text = raw or ""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return FaithfulnessReport(source="fallback", error="no_json_array")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return FaithfulnessReport(source="fallback", error="bad_json")

    claims: list[ClaimVerdict] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        if not claim:
            continue
        claims.append(
            ClaimVerdict(
                claim=claim,
                supported=bool(item.get("supported")),
                reason=str(item.get("reason") or "").strip(),
            )
        )

    total = len(claims)
    supported_count = sum(1 for claim in claims if claim.supported)
    ratio = round(supported_count / total, 4) if total else 1.0
    return FaithfulnessReport(
        claims=claims,
        supported_count=supported_count,
        total=total,
        grounded_ratio=ratio,
        grounded=(total == 0) or (ratio >= threshold),
        source="llm",
    )
