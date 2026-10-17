from app.services.eval_service import aggregate_metrics, metric_deltas, score_case


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


def test_metric_deltas_report_quality_points_and_latency_milliseconds() -> None:
    base = {"recall@1": 0.5, "mrr": 0.6, "p95_latency_ms": 100.0}
    candidate = {"recall@1": 0.75, "mrr": 0.5, "p95_latency_ms": 80.0}

    deltas = {item["key"]: item for item in metric_deltas(base, candidate)}

    assert deltas["recall@1"]["delta"] == 0.25
    assert deltas["mrr"]["delta"] == -0.1
    assert deltas["p95_latency_ms"]["delta"] == -20.0
    assert "elapsed_seconds" not in deltas
