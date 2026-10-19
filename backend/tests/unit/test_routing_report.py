from uuid import uuid4

from app.services.eval_service import build_routing_report


def _row(question: str, expected_mode: str | None, answerable: bool = True) -> dict:
    return {
        "case_id": uuid4(),
        "question": question,
        "answerable": answerable,
        "expected_mode": expected_mode,
    }


def test_perfect_labels_produce_perfect_scores() -> None:
    rows = [
        _row("X1 的续航多久？", "rag"),
        _row("X1 和 X2 的屏幕有什么区别？", "agent"),
    ]

    report = build_routing_report(rows)

    assert report["total"] == 2
    assert report["labeled"] == 2
    assert report["unlabeled"] == 0
    assert report["accuracy"] == 1.0
    assert report["agent_precision"] == 1.0
    assert report["agent_recall"] == 1.0
    assert report["agent_f1"] == 1.0
    assert report["predicted_agent_rate"] == 0.5
    assert report["expected_agent_rate"] == 0.5
    assert report["confusion"] == {
        "rag->rag": 1,
        "rag->agent": 0,
        "agent->rag": 0,
        "agent->agent": 1,
    }
    assert all(case["correct"] is True for case in report["cases"])


def test_false_positive_and_false_negative_are_both_counted() -> None:
    rows = [
        _row("X1 的续航多久？", "rag"),
        # "导致" is a multi-hop signal, so the router sends this to the Agent.
        _row("手表进水导致的故障能保修吗？", "rag"),
        _row("X1 和 X2 的屏幕有什么区别？", "agent"),
        # Deliberately under-routed: same simple question labelled as agent.
        _row("X1 的续航多久？", "agent"),
    ]

    report = build_routing_report(rows)

    assert report["accuracy"] == 0.5
    assert report["agent_precision"] == 0.5
    assert report["agent_recall"] == 0.5
    assert report["agent_f1"] == 0.5
    assert report["confusion"] == {
        "rag->rag": 1,
        "rag->agent": 1,
        "agent->rag": 1,
        "agent->agent": 1,
    }
    mismatch = [case for case in report["cases"] if case["correct"] is False]
    assert len(mismatch) == 2
    false_positive = next(case for case in mismatch if case["expected_mode"] == "rag")
    assert "comparison" not in false_positive["signals"]
    assert false_positive["predicted_mode"] == "agent"


def test_unlabelled_cases_are_reported_but_not_scored() -> None:
    rows = [
        _row("X1 的续航多久？", "rag"),
        _row("X1 和 X2 的屏幕有什么区别？", None),
        _row("X9 的保修政策是什么？", "unknown"),  # invalid label -> unlabelled
    ]

    report = build_routing_report(rows)

    assert report["total"] == 3
    assert report["labeled"] == 1
    assert report["unlabeled"] == 2
    assert report["accuracy"] == 1.0
    assert report["predicted_agent_rate"] == round(1 / 3, 4)
    assert report["expected_agent_rate"] == 0.0
    # predicted_agent_rate counts every case, including unlabelled ones.
    assert sum(report["confusion"].values()) == 1
    unlabelled = [case for case in report["cases"] if case["correct"] is None]
    assert len(unlabelled) == 2
    assert all(case["expected_mode"] is None for case in unlabelled)


def test_empty_dataset_returns_none_metrics() -> None:
    report = build_routing_report([])

    assert report["total"] == 0
    assert report["labeled"] == 0
    assert report["unlabeled"] == 0
    assert report["accuracy"] is None
    assert report["agent_precision"] is None
    assert report["agent_recall"] is None
    assert report["agent_f1"] is None
    assert report["predicted_agent_rate"] == 0.0
    assert report["expected_agent_rate"] is None
    assert report["cases"] == []
