"""Tests for the rule-based auto router.

The router is deliberately deterministic, so these tests pin both the
promotion signals and the conservative default instead of mocking a model.
"""

from types import SimpleNamespace

import pytest

from app.api.v1 import chat as chat_api
from app.rag.router import choose_mode


@pytest.mark.parametrize(
    "question",
    [
        "X1 的续航是多久？",
        "手表进水了还能保修吗？",
        "忘记充电器了怎么办？",
        "手表怎么升级固件？",
        "如何申请美国旅游签证？",
    ],
)
def test_simple_questions_stay_on_single_shot_rag(question: str) -> None:
    decision = choose_mode(question)

    assert decision.mode == "rag"
    assert decision.signals == ()


@pytest.mark.parametrize(
    ("question", "signal"),
    [
        ("X2 和 X1 有什么区别？", "comparison"),
        ("X1 和 X2 的充电时间", "multiple_entities"),
        ("蒸桑拿导致手表进水，还在保修范围内吗？", "multi_part"),
        ("支持哪些协议？能连几个设备？", "multiple_questions"),
    ],
)
def test_multi_hop_signals_promote_to_agent(question: str, signal: str) -> None:
    decision = choose_mode(question)

    assert decision.mode == "agent"
    assert signal in decision.signals
    assert "多跳信号" in decision.reason


def test_explicit_modes_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_api, "get_settings", lambda: SimpleNamespace(agent_enabled=True))

    assert chat_api._resolve_mode("rag", "X2 和 X1 有什么区别？") == ("rag", None)
    assert chat_api._resolve_mode("agent", "X1 的续航是多久？") == ("agent", None)


def test_auto_keeps_agent_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_api, "get_settings", lambda: SimpleNamespace(agent_enabled=True))

    mode, reason = chat_api._resolve_mode("auto", "X2 和 X1 有什么区别？")

    assert mode == "agent"
    assert reason is not None and "comparison" in reason


def test_auto_falls_back_to_rag_when_agent_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_api, "get_settings", lambda: SimpleNamespace(agent_enabled=False))

    mode, reason = chat_api._resolve_mode("auto", "X2 和 X1 有什么区别？")

    assert mode == "rag"
    assert reason is not None and "回退" in reason


def test_auto_simple_question_ignores_agent_flag() -> None:
    # A missing get_settings patch would blow up if the router consulted the
    # settings for a question that stays on the RAG path.
    mode, reason = chat_api._resolve_mode("auto", "X1 的续航是多久？")
    assert mode == "rag"
    assert reason is not None and "单轮混合检索" in reason
