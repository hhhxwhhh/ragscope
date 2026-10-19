"""Cheap, deterministic routing between single-shot RAG and the agent.

Auto mode is intentionally rule-based instead of calling an LLM classifier:
the routing decision runs before every answer, and an extra model turn would
add latency and cost to exactly the cheap path we are trying to protect. The
rules only promote a question when it carries an explicit multi-entity or
multi-hop signal; everything else stays on the single-shot pipeline.
"""

import re
from dataclasses import dataclass
from typing import Literal

RouteMode = Literal["rag", "agent"]

# "X2 和 X1 有什么区别", "哪个型号防水", "分别支持什么协议"
_COMPARISON = re.compile(r"对比|比较|区别|差异|不同|哪个|分别|各自|vs\.?", re.IGNORECASE)
# Explicit multi-part or conditional questions.
_MULTI_PART = re.compile(r"并且|同时|另外|以及|还要|还需要|再加上|顺便|导致|还算|仍然")
# Two model-like entities joined by 和/与/、/跟: "X1 和 X2 的续航".
_TWO_ENTITIES = re.compile(r"[A-Za-z]+\d+.*(?:和|与|、|跟).*[A-Za-z]+\d+")
_MULTI_QUESTION = re.compile(r"[？?][^？?]*[？?]")


@dataclass(frozen=True)
class RouteDecision:
    mode: RouteMode
    reason: str
    signals: tuple[str, ...] = ()


def choose_mode(question: str) -> RouteDecision:
    """Pick a path for auto mode without an extra model call.

    The bias is deliberate: a false ``rag`` costs one retrieval pass, while a
    false ``agent`` costs several planning turns and tool calls. Only questions
    with clear multi-hop signals are promoted.
    """
    text = question.strip()
    signals: list[str] = []
    if _COMPARISON.search(text):
        signals.append("comparison")
    if _TWO_ENTITIES.search(text):
        signals.append("multiple_entities")
    if _MULTI_PART.search(text):
        signals.append("multi_part")
    if _MULTI_QUESTION.search(text):
        signals.append("multiple_questions")

    if signals:
        return RouteDecision(
            mode="agent",
            reason="检测到多实体或多跳信号：" + "、".join(signals),
            signals=tuple(signals),
        )
    return RouteDecision(mode="rag", reason="未检测到多跳信号，使用单轮混合检索")
