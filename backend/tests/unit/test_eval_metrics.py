import pytest

from app.services.eval_service import (
    aggregate_metrics,
    looks_refused,
    metric_deltas,
    score_case,
)


def _candidate(document_id: str, chunk_index: int) -> dict[str, object]:
    return {"document_id": document_id, "chunk_index": chunk_index}


def test_score_case_first_relevant_chunk() -> None:
    refs = [{"document_id": "doc-1", "chunk_index": 2}]
    retrieved = [
        _candidate("doc-9", 0),
        _candidate("doc-1", 2),
        _candidate("doc-1", 3),
    ]
    metrics = score_case(retrieved, refs, answerable=True)

    assert metrics["first_relevant_rank"] == 2
    assert metrics["recall@1"] == 0.0
    assert metrics["recall@3"] == 1.0
    assert metrics["hit@5"] == 1.0
    assert metrics["mrr"] == 0.5
    assert metrics["refused"] is False


def test_recall_is_fraction_of_labelled_chunks_found() -> None:
    refs = [
        {"document_id": "doc-1", "chunk_index": 0},
        {"document_id": "doc-1", "chunk_index": 5},
    ]
    retrieved = [_candidate("doc-1", 4), _candidate("doc-1", 5)]
    metrics = score_case(retrieved, refs, answerable=True)

    assert metrics["recall@1"] == 0.0
    assert metrics["recall@3"] == 0.5
    assert metrics["mrr"] == 1 / 2
    assert metrics["first_relevant_rank"] == 2


def test_gold_refs_may_span_several_documents() -> None:
    """Comparison questions are labelled across pages that look alike.

    Recall must divide by the total number of labelled chunks regardless of
    which document they live in — otherwise a case labelled across three pages
    could never score 1.0 and the aggregate would quietly under-report.
    """
    refs = [
        {"document_id": "doc-a", "chunk_index": 1},
        {"document_id": "doc-b", "chunk_index": 1},
        {"document_id": "doc-c", "chunk_index": 1},
    ]
    retrieved = [
        _candidate("doc-b", 1),
        _candidate("doc-x", 7),
        _candidate("doc-a", 1),
    ]
    metrics = score_case(retrieved, refs, answerable=True)

    assert metrics["first_relevant_rank"] == 1
    assert metrics["recall@1"] == pytest.approx(1 / 3)
    assert metrics["recall@3"] == pytest.approx(2 / 3)
    # Same chunk index in a different document must not be mistaken for a hit.
    assert metrics["recall@5"] == pytest.approx(2 / 3)
    assert metrics["mrr"] == 1.0


def test_unanswerable_case_scores_refusal_instead_of_recall() -> None:
    metrics = score_case([], [], answerable=False)

    assert metrics["refused"] is True
    assert metrics["first_relevant_rank"] is None
    assert metrics["mrr"] == 0.0
    assert metrics["recall@10"] == 0.0


def test_unanswerable_case_that_retrieves_anything_counts_as_not_refused() -> None:
    metrics = score_case([_candidate("doc-1", 0)], [], answerable=False)

    assert metrics["refused"] is False
    assert metrics["hit@5"] == 0.0
    assert metrics["mrr"] == 0.0


def test_aggregate_metrics_excludes_errors_and_separates_refusal_rates() -> None:
    results = [
        {
            "answerable": True,
            "metrics": {"recall@1": 1.0, "recall@3": 1.0, "recall@5": 1.0, "recall@10": 1.0,
                        "hit@5": 1.0, "mrr": 1.0},
            "refused": False,
            "latency_ms": 10,
            "error": None,
        },
        {
            "answerable": True,
            "metrics": {"recall@1": 0.0, "recall@3": 0.0, "recall@5": 0.0, "recall@10": 0.0,
                        "hit@5": 0.0, "mrr": 0.0},
            "refused": True,
            "latency_ms": 30,
            "error": None,
        },
        {
            "answerable": False,
            "metrics": {},
            "refused": True,
            "latency_ms": 50,
            "error": None,
        },
        {
            "answerable": True,
            "metrics": {},
            "refused": True,
            "latency_ms": 999,
            "error": "timeout",
        },
    ]
    metrics = aggregate_metrics(results, elapsed_seconds=1.234)

    assert metrics["cases_total"] == 4
    assert metrics["cases_scored"] == 3
    assert metrics["errors"] == 1
    assert metrics["answerable_total"] == 2
    assert metrics["unanswerable_total"] == 1
    assert metrics["recall@1"] == 0.5
    assert metrics["mrr"] == 0.5
    assert metrics["refusal_accuracy"] == 1.0
    assert metrics["answerable_refusal_rate"] == 0.5
    assert metrics["avg_latency_ms"] == 30.0
    assert metrics["p50_latency_ms"] == 30.0
    assert metrics["p95_latency_ms"] == 50.0
    assert metrics["elapsed_seconds"] == 1.23


def _retrieval_metrics(recall: float = 1.0) -> dict[str, float]:
    return {
        "recall@1": recall,
        "recall@3": recall,
        "recall@5": recall,
        "recall@10": recall,
        "hit@5": recall,
        "mrr": recall,
    }


def test_looks_refused_matches_fixed_sentence_and_common_phrasings() -> None:
    assert looks_refused("根据现有资料无法回答这个问题。") is True
    assert looks_refused("资料不足，无法确认该型号的保修期。") is True
    assert looks_refused("X1 的续航是十二小时 [1]。") is False
    assert looks_refused("") is False


def test_aggregate_metrics_adds_generation_metrics_only_when_present() -> None:
    results = [
        {
            "answerable": True,
            "metrics": {
                **_retrieval_metrics(),
                "generation_latency_ms": 100,
                "generation_tokens": 50,
                "answer_refused": False,
                "answer_grounded": True,
            },
            "refused": False,
            "latency_ms": 10,
            "error": None,
        },
        {
            "answerable": True,
            "metrics": {
                **_retrieval_metrics(0.0),
                "generation_latency_ms": 200,
                "generation_tokens": 30,
                "answer_refused": True,
                "answer_grounded": False,
            },
            "refused": False,
            "latency_ms": 12,
            "error": None,
        },
        {
            "answerable": False,
            "metrics": {
                **_retrieval_metrics(0.0),
                "generation_latency_ms": 50,
                "generation_tokens": 0,
                "answer_refused": True,
                "answer_grounded": False,
            },
            "refused": True,
            "latency_ms": 8,
            "error": None,
        },
        {
            "answerable": True,
            "metrics": {
                **_retrieval_metrics(),
                "generation_error": "timeout",
                "generation_latency_ms": 10,
                "generation_tokens": 0,
            },
            "refused": False,
            "latency_ms": 9,
            "error": None,
        },
    ]

    metrics = aggregate_metrics(results, elapsed_seconds=1.0)

    assert metrics["generation_cases"] == 4
    assert metrics["generation_errors"] == 1
    assert metrics["generation_refusal_accuracy"] == 1.0
    # One of the three answerable generations refused.
    assert metrics["generation_false_refusal_rate"] == 0.3333
    # The error case has no answer_refused and is treated as answered.
    assert metrics["answer_citation_rate"] == 0.5
    assert metrics["avg_generation_latency_ms"] == 116.7
    assert metrics["generation_total_tokens"] == 80


def test_sentence_window_counts_as_coverage() -> None:
    """A window-expanded item really does carry the neighbour's text.

    Scoring it as a hit is not metric inflation: the gold chunk is in the
    context that reaches the prompt.
    """
    refs = [{"document_id": "doc-1", "chunk_index": 5}]
    retrieved = [
        {
            "document_id": "doc-1",
            "chunk_index": 4,
            "window_indexes": [3, 4, 5],
        }
    ]

    metrics = score_case(retrieved, refs, answerable=True)

    assert metrics["recall@1"] == 1.0
    assert metrics["mrr"] == 1.0
    assert metrics["hit@5"] == 1.0


def test_doc_hit_separates_wrong_chunk_from_wrong_page() -> None:
    refs = [{"document_id": "doc-1", "chunk_index": 5}]
    retrieved = [_candidate("doc-1", 9)]

    metrics = score_case(retrieved, refs, answerable=True)

    # Right page, wrong chunk: chunk-level recall stays 0 so the two failure
    # modes never get conflated.
    assert metrics["recall@1"] == 0.0
    assert metrics["hit@5"] == 0.0
    assert metrics["doc_hit@5"] == 1.0


def test_retrieval_only_run_has_no_generation_metrics() -> None:
    results = [
        {
            "answerable": True,
            "metrics": _retrieval_metrics(),
            "refused": False,
            "latency_ms": 10,
            "error": None,
        }
    ]

    metrics = aggregate_metrics(results, elapsed_seconds=0.5)

    assert "generation_cases" not in metrics
    assert "generation_refusal_accuracy" not in metrics
    assert "avg_generation_latency_ms" not in metrics


def test_metric_deltas_report_quality_points_and_latency_milliseconds() -> None:
    base = {"recall@1": 0.5, "mrr": 0.6, "p95_latency_ms": 100.0}
    candidate = {"recall@1": 0.75, "mrr": 0.5, "p95_latency_ms": 80.0}

    deltas = {item["key"]: item for item in metric_deltas(base, candidate)}

    assert deltas["recall@1"]["delta"] == 0.25
    assert deltas["mrr"]["delta"] == -0.1
    assert deltas["p95_latency_ms"]["delta"] == -20.0
    assert "elapsed_seconds" not in deltas
