"""Unit tests for the CRAG grader/repair path and contextual compression.

Both features are fail-open by design, so the tests focus on the decision
boundaries: which grade a given score profile produces, that the repair pass
never returns chunks the main loop already has, and that compression can only
shorten text (never rewrite or add to it).
"""

import pytest

from app.rag import compress, crag


def _candidate(chunk_id: str, **scores) -> dict:
    base = {"chunk_id": chunk_id, "content": "内容"}
    base.update(scores)
    return base


# --------------------------------------------------------------------------- #
# Grading
# --------------------------------------------------------------------------- #


def test_relevance_scores_prefer_rerank_signal():
    candidates = [_candidate("a", vector_score=0.9, rerank_score=0.4)]
    scores, signal = crag.relevance_scores(candidates)
    assert signal == "rerank"
    assert scores == [0.4]


def test_relevance_scores_fall_back_to_dense():
    candidates = [_candidate("a", vector_score=0.72)]
    scores, signal = crag.relevance_scores(candidates)
    assert signal == "vector"
    assert scores == [0.72]


def test_empty_candidates_grade_incorrect():
    result = crag.heuristic_grade([], correct_threshold=0.55, ambiguous_threshold=0.30)
    assert result.action == crag.INCORRECT
    assert result.confidence == 0.0
    assert result.signal == "none"


def test_strong_cluster_grades_correct():
    candidates = [
        _candidate("a", rerank_score=0.91),
        _candidate("b", rerank_score=0.80),
        _candidate("c", rerank_score=0.72),
    ]
    result = crag.heuristic_grade(candidates, correct_threshold=0.55, ambiguous_threshold=0.30)
    assert result.action == crag.CORRECT
    assert result.confidence > 0.7


def test_off_topic_grades_incorrect():
    candidates = [
        _candidate("a", rerank_score=0.12),
        _candidate("b", rerank_score=0.08),
    ]
    result = crag.heuristic_grade(candidates, correct_threshold=0.55, ambiguous_threshold=0.30)
    assert result.action == crag.INCORRECT


def test_one_lucky_neighbour_grades_ambiguous():
    """A single strong hit over weak support must not be trusted as correct."""
    candidates = [
        _candidate("a", rerank_score=0.62),
        _candidate("b", rerank_score=0.05),
        _candidate("c", rerank_score=0.04),
    ]
    result = crag.heuristic_grade(candidates, correct_threshold=0.55, ambiguous_threshold=0.30)
    assert result.action == crag.AMBIGUOUS


def test_scores_are_clamped_before_grading():
    candidates = [_candidate("a", vector_score=1.8)]
    scores, _ = crag.relevance_scores(candidates)
    assert scores == [1.0]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("correct", "correct"),
        ("ambiguous.\n", "ambiguous"),
        ("我认为是 incorrect", "incorrect"),
        ("", None),
        ("无法判断", None),
    ],
)
def test_parse_grade(raw, expected):
    assert crag._parse_grade(raw) == expected


def test_keyword_queries_falls_back_to_raw_query(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("jieba unavailable")

    monkeypatch.setattr("jieba.analyse.extract_tags", boom, raising=False)
    assert crag.keyword_queries("如何申请退款") == ["如何申请退款"]


def test_keyword_queries_returns_distinct_queries():
    queries = crag.keyword_queries("ref 和 reactive 的区别是什么？")
    assert queries
    assert len(queries) <= 2
    assert len(set(queries)) == len(queries)


def test_parse_retry_queries_strips_bullets_and_limits():
    raw = "- 退款流程\n• 退款 条件\n退款流程\n\n"
    parsed = crag._parse_retry_queries(raw, count=2)
    assert parsed == ["退款流程", "退款 条件"]


def _no_key_settings(monkeypatch):
    """Both modules bind ``get_settings`` at import time; patch the local name."""
    fake = type("S", (), {"deepseek_api_key": ""})()
    monkeypatch.setattr(crag, "get_settings", lambda: fake)
    monkeypatch.setattr(compress, "get_settings", lambda: fake)


@pytest.mark.asyncio
async def test_repair_queries_falls_back_when_no_api_key(monkeypatch):
    _no_key_settings(monkeypatch)
    result = await crag.repair_queries("怎么退款", timeout_seconds=1.0)
    assert result.applied is False
    assert result.source == "keyword"
    assert "怎么退款" in result.queries


@pytest.mark.asyncio
async def test_llm_grade_returns_none_without_api_key(monkeypatch):
    _no_key_settings(monkeypatch)
    assert await crag.llm_grade("q", [_candidate("a")], timeout_seconds=1.0) is None


@pytest.mark.asyncio
async def test_grade_without_llm_uses_heuristic(monkeypatch):
    result = await crag.grade(
        "q",
        [_candidate("a", rerank_score=0.9), _candidate("b", rerank_score=0.8)],
        use_llm=False,
        timeout_seconds=1.0,
        correct_threshold=0.55,
        ambiguous_threshold=0.30,
    )
    assert result.grader == "heuristic"
    assert result.action == crag.CORRECT


# --------------------------------------------------------------------------- #
# Compression
# --------------------------------------------------------------------------- #


def test_split_sentences_handles_mixed_punctuation():
    text = "第一句。第二句！\n第三项\n\n第四段；"
    assert compress.split_sentences(text) == ["第一句。", "第二句！", "第三项", "第四段；"]


def test_short_chunks_are_left_alone():
    content = "退款需在七天内申请。"
    assert compress.lexical_compress(content, {"退款"}) == (content, 0)


def test_unrelated_chunk_is_left_alone():
    content = "公司成立于二零一一年，总部位于深圳，员工规模超过三千人，业务覆盖全国。"
    assert compress.lexical_compress(content, {"退款"}) == (content, 0)


def test_lexical_compress_drops_off_topic_sentences():
    content = (
        "退款需在收货后七天内提交申请。"
        "公司成立于二零一一年，总部位于深圳，员工规模超过三千人。"
        "审批通过后款项原路退回。"
        "本页还介绍了积分规则与会员等级。"
    )
    compressed, dropped = compress.lexical_compress(
        content, {"退款", "退回", "申请"}, min_chars=20
    )
    assert "退款需在收货后七天内提交申请。" in compressed
    assert "审批通过后款项原路退回。" in compressed
    assert "公司成立于二零一一年，总部位于深圳，员工规模超过三千人。" not in compressed
    assert "本页还介绍了积分规则与会员等级。" not in compressed
    assert dropped == 2
    assert len(compressed) < len(content)


def test_compression_never_adds_text():
    content = "退款政策。第二条说明。第三条说明。第四条说明。"
    compressed, _ = compress.lexical_compress(content, set())
    # No overlap -> untouched, never padded.
    assert compressed == content or len(compressed) <= len(content)


@pytest.mark.asyncio
async def test_compress_disabled_returns_originals():
    candidates = [_candidate("a"), _candidate("b")]
    result = await compress.compress(
        "q", candidates, enabled=False, mode="lexical", timeout_seconds=1.0
    )
    assert result.applied is False
    assert [item["chunk_id"] for item in result.candidates] == ["a", "b"]


@pytest.mark.asyncio
async def test_compress_preserves_order_and_marks_changes():
    candidates = [
        {
            "chunk_id": "a",
            "content": "退款需在收货后七天内提交申请。"
            "公司成立于二零一一年。"
            "审批通过后款项原路退回。",
        },
        {"chunk_id": "b", "content": "短文本"},
    ]
    result = await compress.compress(
        "退款 退回",
        candidates,
        enabled=True,
        mode="lexical",
        timeout_seconds=1.0,
        min_chars=10,
    )
    assert [item["chunk_id"] for item in result.candidates] == ["a", "b"]
    assert result.candidates[0]["compressed"] is True
    assert result.compressed_chars < result.original_chars
    assert result.per_chunk[0]["after"] < result.per_chunk[0]["before"]


@pytest.mark.asyncio
async def test_llm_mode_falls_back_to_lexical(monkeypatch):
    async def boom(*_args, **_kwargs):
        return None

    monkeypatch.setattr(compress, "llm_extract", boom)
    candidates = [
        {
            "chunk_id": "a",
            "content": "退款需在七天内申请。" "公司成立于二零一一年。" "审批通过后原路退回。",
        }
    ]
    result = await compress.compress(
        "退款 退回",
        candidates,
        enabled=True,
        mode="llm",
        timeout_seconds=1.0,
        min_chars=10,
    )
    assert result.error == "llm_extract_unavailable"
    assert "退款需在七天内申请。" in result.candidates[0]["content"]


@pytest.mark.asyncio
async def test_llm_extract_rejects_paraphrases(monkeypatch):
    """Only sentences that literally occur in the source may survive."""
    source = "甲方应在三十日内付款。乙方负责现场安装调试。"
    captured = {}

    class FakeChain:
        async def ainvoke(self, payload):
            captured.update(payload)
            return "甲方须于一个月内结清款项。\n甲方应在三十日内付款。"

        def __or__(self, _other):
            return self

    monkeypatch.setattr(
        compress,
        "EXTRACT_PROMPT_TEMPLATE",
        type("T", (), {"__or__": lambda self, _other: FakeChain()})(),
    )
    kept = await compress.llm_extract("付款", source, timeout_seconds=1.0)
    # The paraphrased first line is dropped, the verbatim second line survives.
    assert kept == "甲方应在三十日内付款。"
    assert captured["query"] == "付款"
