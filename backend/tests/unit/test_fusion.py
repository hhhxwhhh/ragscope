from app.rag.fusion import reciprocal_rank_fusion


def test_empty_rankings_produce_empty_result() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_single_ranking_keeps_order() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"]])
    assert [chunk_id for chunk_id, _ in fused] == ["a", "b", "c"]
    assert fused[0][1] > fused[1][1] > fused[2][1]


def test_duplicate_ids_accumulate_scores_from_both_rankings() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]], k=60)
    scores = dict(fused)

    assert len(fused) == 3
    # "a" appears at ranks 1 and 3, "c" at ranks 3 and 2, so a > c.
    assert scores["a"] > scores["c"]
    assert scores["a"] == 1 / 61 + 1 / 63


def test_weights_shift_the_fused_order() -> None:
    unweighted = dict(reciprocal_rank_fusion([["a"], ["b"]]))
    assert unweighted["a"] == unweighted["b"]

    weighted = reciprocal_rank_fusion([["a"], ["b"]], weights=[1.0, 3.0])
    assert [chunk_id for chunk_id, _ in weighted] == ["b", "a"]


def test_larger_k_flattens_rank_differences() -> None:
    small_k = dict(reciprocal_rank_fusion([["a", "b"]], k=1))
    large_k = dict(reciprocal_rank_fusion([["a", "b"]], k=60))

    assert small_k["a"] - small_k["b"] > large_k["a"] - large_k["b"]
