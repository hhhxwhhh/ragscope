"""Semantic cache primitives: normalisation and cosine similarity."""

import pytest

from app.rag.semantic_cache import cosine, normalize_question


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  X1   电池多大？ ", "x1 电池多大"),
        ("X1 的电池容量是多少。", "x1 的电池容量是多少"),
        ("", ""),
    ],
)
def test_normalize_question(raw, expected):
    assert normalize_question(raw) == expected


def test_normalize_truncates_to_column_width():
    assert len(normalize_question("问" * 1200)) == 500


def test_cosine_identity_and_orthogonality():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    # Opposite vectors are -1, which must never pass a positive threshold.
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_handles_mismatched_or_empty_vectors():
    assert cosine([], [1.0]) == 0.0
    assert cosine([1.0, 2.0], [1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_threshold_separates_paraphrase_from_unrelated():
    """A concrete example of why 0.93 is the default instead of a round 0.8."""
    base = [1.0, 0.0, 0.0]
    paraphrase = [0.97, 0.24, 0.0]   # same intent, different wording
    unrelated = [0.2, 0.98, 0.0]     # shares the domain, different question

    assert cosine(base, paraphrase) > 0.93
    assert cosine(base, unrelated) < 0.93
