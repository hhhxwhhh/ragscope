"""Unit tests for the first-party Prometheus exposition layer."""

from app.core import metrics


def test_normalise_route_collapses_identifiers():
    raw = "/api/v1/kbs/2f1c9c1e-7a1b-4c3d-9e2f-0a1b2c3d4e5f/documents/42"
    assert metrics.normalise_route(raw) == "/api/v1/kbs/{id}/documents/{id}"
    assert metrics.normalise_route("/healthz") == "/healthz"


def test_counter_and_histogram_render_valid_exposition_text():
    metrics.observe_http("GET", "/unit-test/{id}", 200, 0.02)
    metrics.observe_http("GET", "/unit-test/{id}", 500, 0.4)

    text = metrics.render()

    assert "# TYPE ragscope_http_requests_total counter" in text
    assert 'ragscope_http_requests_total{method="GET",route="/unit-test/{id}",status="200"} 1' in text
    assert "# TYPE ragscope_http_request_duration_seconds histogram" in text
    assert 'ragscope_http_request_duration_seconds_bucket{method="GET",route="/unit-test/{id}",le="+Inf"}' in text


def test_labels_are_isolated_per_key():
    def current(status: str) -> float:
        for name, labels, value in metrics.INGEST_TOTAL.samples():
            if name == "ragscope_ingest_total" and labels == f'{{status="{status}"}}':
                return value
        return 0.0

    indexed_before, failed_before = current("indexed"), current("failed")
    metrics.observe_ingest("indexed")
    metrics.observe_ingest("indexed")
    metrics.observe_ingest("failed")

    assert current("indexed") == indexed_before + 2
    assert current("failed") == failed_before + 1


def test_retrieval_metrics_record_stage_and_refusal():
    before = metrics.RETRIEVAL_REFUSAL_TOTAL.value()
    metrics.observe_retrieval(
        {
            "stage_timings": {"vector_ms": 12.0, "bm25_ms": 3.0, "total_ms": 20.0},
            "counts": {"vector": 4, "bm25": 2, "fused": 5, "final": 0},
            "cache_hit": True,
        }
    )
    assert metrics.RETRIEVAL_REFUSAL_TOTAL.value() == before + 1
    assert metrics.RETRIEVAL_TOTAL.value() >= 1


def test_snapshot_is_json_safe():
    metrics.observe_http("POST", "/unit-snapshot", 503, 0.01)
    snapshot = metrics.snapshot()

    assert snapshot["http"]["requests_total"] >= 1
    assert snapshot["http"]["errors_total"] >= 1
    assert 0.0 <= snapshot["http"]["error_rate"] <= 1.0
    assert snapshot["retrieval"]["total"] >= 1
    # Percentiles come from bucket interpolation and must stay finite.
    for key in ("p50_seconds", "p95_seconds", "p99_seconds"):
        assert snapshot["http"][key] is None or snapshot["http"][key] >= 0
