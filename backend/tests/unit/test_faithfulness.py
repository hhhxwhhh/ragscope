"""Unit tests for the answer-grounding / faithfulness guard.

These are pure-logic tests: they never call the LLM, so they run offline. The
LLM path is covered by the live smoke test, not here.
"""

import pytest

from app.rag import faithfulness as f


class _StubSettings:
    deepseek_api_key = "test-key"
    answer_grounding_enabled = True
    answer_grounding_threshold = 0.5
    answer_grounding_timeout_seconds = 8.0
    # Same budget the answer prompt uses — see test_judge_sees_every_source_*.
    max_context_chars = 6000


@pytest.fixture
def stub_settings(monkeypatch):
    stub = _StubSettings()
    monkeypatch.setattr(f, "get_settings", lambda: stub)
    return stub


def test_parse_report_computes_grounded_ratio_and_flag():
    raw = (
        '[{"claim": "a", "supported": true, "reason": "ok"},'
        ' {"claim": "b", "supported": false, "reason": "missing"}]'
    )
    report = f._parse_report(raw, threshold=0.6)
    assert report.total == 2
    assert report.supported_count == 1
    assert report.grounded_ratio == 0.5
    assert report.grounded is False  # 0.5 < 0.6 threshold
    assert report.source == "llm"
    assert report.claims[0].supported is True
    assert report.claims[1].supported is False


def test_parse_report_above_threshold_is_grounded():
    raw = '[{"claim": "a", "supported": true}, {"claim": "b", "supported": true}]'
    report = f._parse_report(raw, threshold=0.5)
    assert report.grounded_ratio == 1.0
    assert report.grounded is True


def test_parse_report_handles_text_around_json():
    raw = '好的，以下是核查：\n[{"claim": "x", "supported": true}]'
    report = f._parse_report(raw, threshold=0.5)
    assert report.total == 1
    assert report.source == "llm"


def test_parse_report_without_json_falls_back():
    report = f._parse_report("模型拒绝输出结构", threshold=0.5)
    assert report.source == "fallback"
    assert report.grounded is True  # fail open, never alarm
    assert report.error == "no_json_array"


def test_context_block_truncates_to_max_chars():
    candidates = [{"content": "x" * 2000} for _ in range(5)]
    block = f._context_block(candidates, max_chars=1000)
    assert len(block) <= 1050
    assert "[文档 1]" in block


def test_judge_sees_every_source_the_answer_may_cite():
    """The judge's evidence set must match the answer prompt's.

    An earlier version capped the judge at the first 8 candidates and 3000
    chars while the prompt used ``max_context_chars`` (6000). With
    sentence-window expansion that mismatch made grounded claims look
    ungrounded — the rate fell 12 points for a reason that had nothing to do
    with the model. Trimming the evidence a judge may look at is not an
    optimisation, it is a different question being asked.
    """
    candidates = [{"content": f"doc{i} content"} for i in range(20)]
    block = f._context_block(candidates)
    assert "[文档 20]" in block

    windowed = [{"content": "x" * 900} for _ in range(5)]
    block = f._context_block(windowed)
    # 5 x 900 = 4500 chars: inside the 6000 budget, so nothing may be dropped.
    assert "[文档 5]" in block
    assert "…" not in block


async def test_check_faithfulness_fail_open_without_api_key(stub_settings):
    stub_settings.deepseek_api_key = ""
    report = await f.check_faithfulness(
        "这是一个答案。", [{"content": "这是上下文。"}], timeout_seconds=1
    )
    assert report.source == "fallback"
    assert report.grounded is True
    assert report.error == "no_api_key"


async def test_check_faithfulness_fail_open_on_empty_answer(stub_settings):
    report = await f.check_faithfulness("", [{"content": "ctx"}], timeout_seconds=1)
    assert report.source == "fallback"
    assert report.error == "empty_answer"


async def test_check_faithfulness_fail_open_on_empty_context(stub_settings):
    report = await f.check_faithfulness("答案", [], timeout_seconds=1)
    assert report.source == "fallback"
    assert report.error == "empty_context"


def test_context_block_reads_the_content_key_used_by_retrieval():
    """Candidates carry their chunk body under ``content``, not ``text``.

    ``text`` was the first guess and it made every real answer report
    ``error="empty_context"``: the guard silently passed without ever judging
    anything. Retrieval, the compressor, the CRAG grader and the prompt builder
    all read ``content``; this guard cannot be the one place that disagrees.
    """
    assert f._context_block([{"content": "正文内容"}]).startswith("[文档 1] 正文内容")
    assert f._context_block([{"text": "错误的键"}]) == ""
