"""Pure helpers of the GraphRAG layer: name normalisation and term extraction."""

import pytest

from app.services.graph_service import (
    _MAX_ENTITIES_PER_CHUNK,
    candidate_terms,
    normalize_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  X1   Pro！", "x1 pro"),
        ("星盾 2.0", "星盾 2.0"),
        ("IP68", "ip68"),
        ("（保修）政策", "保修 政策"),
        ("", ""),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def test_candidate_terms_keeps_model_codes_and_words():
    terms = candidate_terms("X1 和 X2 的保修期有什么区别")

    assert "x1" in terms
    assert "x2" in terms
    assert "保修期" in terms
    # Everything is normalised (casefolded) and deduplicated.
    assert len(terms) == len(set(terms))
    assert all(term == term.casefold() for term in terms)


def test_candidate_terms_respects_limit_and_short_queries():
    assert candidate_terms("X1", limit=5) == ["x1"]
    assert candidate_terms("X1 和 X2", limit=3)[0] == "x1"


def test_entity_budget_is_bounded():
    # The per-chunk cap is what keeps one verbose chunk from dominating the graph.
    assert 1 <= _MAX_ENTITIES_PER_CHUNK <= 20
