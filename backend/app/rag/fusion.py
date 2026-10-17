"""Reciprocal Rank Fusion (RRF).

RRF combines rankings without needing comparable scores, which is exactly the
problem when fusing cosine similarity with BM25. The constant k dampens the
influence of the very top ranks; 60 is the value from the original paper.
"""

from collections import defaultdict


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    *,
    k: int = 60,
    weights: list[float] | None = None,
) -> list[tuple[str, float]]:
    """Fuse ranked chunk-id lists into ``[(chunk_id, score)]``, best first."""
    if weights is None:
        weights = [1.0] * len(rankings)

    scores: dict[str, float] = defaultdict(float)
    for ranking, weight in zip(rankings, weights, strict=False):
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += weight / (k + rank)

    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
