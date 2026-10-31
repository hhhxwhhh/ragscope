"""Corrective RAG: grade the retrieved evidence, then repair it when weak.

Standard RAG trusts the top-k list: whatever the retriever returns goes straight
into the prompt, so a wrong-but-plausible neighbour is answered as confidently as
a correct one. CRAG adds one decision point before generation — grade the
evidence, and only then decide what to do with it:

* ``correct``   — evidence is on-topic; hand it to the compressor and answer.
* ``ambiguous`` — something matched but weakly; retry with keyword queries and a
  relaxed dense threshold, then merge whatever new chunks come back.
* ``incorrect`` — nothing cleared the bar; retry *and* keep the refusal path
  available downstream (the answer prompt still refuses when the merged list is
  empty or entirely below the score floor).

Why two graders instead of one: the heuristic reads scores the pipeline already
computed (rerank or dense), costs nothing and never fails, so it is the default
and the safety net. The LLM grader answers a question scores cannot — "do these
passages actually *contain* the answer?" — which matters for multi-hop and
comparative questions where every individual chunk is topically close but none of
them is sufficient. It is opt-in because it costs a request.

Everything here is fail-open: a grader error degrades to ``correct`` (answer from
what we have) rather than dropping evidence, and the decision is recorded in the
trace so the behaviour is inspectable instead of mysterious.
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

CORRECT = "correct"
AMBIGUOUS = "ambiguous"
INCORRECT = "incorrect"

GRADE_LABELS = (CORRECT, AMBIGUOUS, INCORRECT)

GRADE_PROMPT = """你是检索质量评估器。判断给定的候选段落是否足以回答用户的问题。

只输出一个词，不要标点、不要解释：
- correct：候选段落中有明确包含答案的内容
- ambiguous：主题相关但证据不足或只沾边
- incorrect：主题无关或候选为空"""

GRADE_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", GRADE_PROMPT),
        ("human", "问题：{query}\n\n候选段落：\n{passages}"),
    ]
)

RETRY_PROMPT = """你是检索纠错助手。上一轮检索没有找到可靠证据。

请输出 2 行不同的检索查询，用于二次召回：
1. 每行一行，不要编号、不要引号、不要解释。
2. 用问题里的核心实体与关键词，去掉虚词和口语。
3. 两行要从不同角度切入（例如同义改写 + 关键词罗列）。
4. 不要回答问题本身。"""

RETRY_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", RETRY_PROMPT),
        ("human", "原始问题：{query}"),
    ]
)


@dataclass
class GradeResult:
    action: str
    confidence: float
    signal: str
    reason: str
    grader: str
    error: str | None = None


@dataclass
class RepairQueries:
    queries: list[str] = field(default_factory=list)
    applied: bool = False
    source: str = "none"
    error: str | None = None


def relevance_scores(candidates: list[dict]) -> tuple[list[float], str]:
    """Pick the best available 0..1 relevance signal for grading.

    Rerank scores come first: the cross-encoder saw the query and the passage
    together, so it is the only signal that is comparable across queries. Dense
    cosine is the fallback. RRF/fusion scores are deliberately unused — they are
    rank-derived sums with no absolute meaning.
    """
    rerank = [
        float(item["rerank_score"])
        for item in candidates
        if item.get("rerank_score") is not None
    ]
    if rerank:
        return [_clamp01(value) for value in rerank], "rerank"
    dense = [
        float(item["vector_score"])
        for item in candidates
        if item.get("vector_score") is not None
    ]
    return [_clamp01(value) for value in dense], "vector"


def _clamp01(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def heuristic_grade(
    candidates: list[dict],
    *,
    correct_threshold: float,
    ambiguous_threshold: float,
) -> GradeResult:
    scores, signal = relevance_scores(candidates)
    if not scores:
        return GradeResult(
            action=INCORRECT,
            confidence=0.0,
            signal="none",
            reason="no_scored_candidates",
            grader="heuristic",
        )

    top = max(scores)
    head = scores[:3]
    mean_head = sum(head) / len(head)
    # The best hit decides whether the topic was found; the head average decides
    # whether the evidence is a real cluster or one lucky neighbour.
    confidence = round(0.7 * top + 0.3 * mean_head, 4)
    reason = f"top={top:.3f} mean_top3={mean_head:.3f} signal={signal}"

    if top >= correct_threshold and mean_head >= ambiguous_threshold:
        action = CORRECT
    elif top < ambiguous_threshold:
        action = INCORRECT
    else:
        action = AMBIGUOUS
    return GradeResult(
        action=action,
        confidence=confidence,
        signal=signal,
        reason=reason,
        grader="heuristic",
    )


def _passages_block(candidates: list[dict], limit: int, chars: int) -> str:
    blocks = []
    for index, candidate in enumerate(candidates[:limit], start=1):
        content = (candidate.get("content") or "").replace("\n", " ")[:chars]
        blocks.append(f"[{index}] {content}")
    return "\n".join(blocks)


async def llm_grade(
    query: str,
    candidates: list[dict],
    *,
    timeout_seconds: float,
    passage_chars: int = 320,
    passage_count: int = 5,
) -> GradeResult | None:
    """Ask the model whether the passages can answer the query.

    Returns ``None`` when the grader cannot run (no key, timeout, bad output) so
    the caller falls back to the heuristic grade.
    """
    settings = get_settings()
    if not settings.deepseek_api_key or not candidates:
        return None

    try:
        chain = (
            GRADE_PROMPT_TEMPLATE
            | get_chat_model().bind(temperature=0.0, max_completion_tokens=8)
            | StrOutputParser()
        )
        raw = await asyncio.wait_for(
            chain.ainvoke(
                {
                    "query": query,
                    "passages": _passages_block(candidates, passage_count, passage_chars),
                }
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        logger.warning("crag grader timed out after %.1fs", timeout_seconds)
        return None
    except Exception as exc:  # noqa: BLE001 - grading must never break retrieval
        logger.warning("crag grader failed: %s", exc)
        return None

    action = _parse_grade(raw)
    if action is None:
        return None
    return GradeResult(
        action=action,
        confidence={"correct": 0.9, "ambiguous": 0.5, "incorrect": 0.1}[action],
        signal="llm",
        reason=f"llm={action}",
        grader="llm",
    )


def _parse_grade(raw: str) -> str | None:
    text = (raw or "").strip().lower()
    if not text:
        return None
    for label in GRADE_LABELS:
        if text.startswith(label):
            return label
    # "incorrect" contains "correct" as a substring, so the containment pass
    # must run most-specific-first or a refusal is read as an approval.
    for label in (INCORRECT, AMBIGUOUS, CORRECT):
        if re.search(rf"(?<![a-z]){label}", text):
            return label
    return None


async def grade(
    query: str,
    candidates: list[dict],
    *,
    use_llm: bool,
    timeout_seconds: float,
    correct_threshold: float,
    ambiguous_threshold: float,
) -> GradeResult:
    """Grade evidence, preferring the LLM verdict but never depending on it."""
    fallback = heuristic_grade(
        candidates,
        correct_threshold=correct_threshold,
        ambiguous_threshold=ambiguous_threshold,
    )
    if not use_llm:
        return fallback

    verdict = await llm_grade(query, candidates, timeout_seconds=timeout_seconds)
    if verdict is None:
        fallback.reason = f"{fallback.reason} llm=unavailable"
        return fallback
    # The LLM decides sufficiency; the heuristic still reports the score signal
    # and remains the tie-breaker when the two disagree on the extremes.
    if verdict.action == INCORRECT and fallback.action == CORRECT:
        verdict.reason = f"{verdict.reason} overridden_by_scores({fallback.reason})"
        verdict.action = AMBIGUOUS
        verdict.confidence = 0.5
    return verdict


def keyword_queries(query: str, limit: int = 2) -> list[str]:
    """Cheap lexical fallback: TF-IDF keywords plus the trimmed raw query.

    ``jieba`` is already a dependency for BM25 tokenisation, so this costs no new
    model and works offline — which matters because the repair path is exactly
    the path that runs when things are already going wrong.
    """
    queries: list[str] = []
    stripped = query.strip()
    if stripped:
        queries.append(stripped)
    try:
        import jieba.analyse

        tags = jieba.analyse.extract_tags(stripped, topK=8)
        if tags:
            keyword_query = " ".join(tags)
            if keyword_query not in queries:
                queries.append(keyword_query)
    except Exception as exc:  # noqa: BLE001 - tokenisation is best-effort
        logger.debug("crag keyword extraction failed: %s", exc)
    return queries[:limit]


async def repair_queries(
    query: str,
    *,
    timeout_seconds: float,
    count: int = 2,
) -> RepairQueries:
    """Second-chance queries for the retry pass (LLM first, keywords on failure)."""
    settings = get_settings()
    fallback = RepairQueries(
        queries=keyword_queries(query, limit=count), source="keyword"
    )
    if not settings.deepseek_api_key:
        return fallback

    try:
        chain = (
            RETRY_PROMPT_TEMPLATE
            | get_chat_model().bind(temperature=0.2, max_completion_tokens=128)
            | StrOutputParser()
        )
        raw = await asyncio.wait_for(
            chain.ainvoke({"query": query}),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        logger.warning("crag retry-query generation timed out")
        fallback.error = "retry_query_timeout"
        return fallback
    except Exception as exc:  # noqa: BLE001
        logger.warning("crag retry-query generation failed: %s", exc)
        fallback.error = f"retry_query_error: {exc}"[:160]
        return fallback

    parsed = _parse_retry_queries(raw, count)
    if not parsed:
        fallback.error = "retry_query_invalid"
        return fallback
    return RepairQueries(queries=parsed, applied=True, source="llm")


def _parse_retry_queries(raw: str, count: int) -> list[str]:
    queries: list[str] = []
    for line in (raw or "").splitlines():
        candidate = line.strip().strip("-•*·）。. ").strip("\"'“”`")
        if len(candidate) < 2 or len(candidate) > 200:
            continue
        if candidate not in queries:
            queries.append(candidate)
        if len(queries) >= count:
            break
    return queries
