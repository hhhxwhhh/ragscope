from app.rag.prompt import citation_stats, extract_citation_ranks


def test_counts_valid_and_out_of_range_markers() -> None:
    answer = "X1 续航 14 天 [1]，X2 是 10 天 [2]，另一个说法 [9]。"
    cited = extract_citation_ranks(answer, candidate_count=3)

    stats = citation_stats(answer, 3, cited)

    assert stats["markers"] == 3
    assert stats["valid_markers"] == 2
    assert stats["invalid_markers"] == 1
    assert stats["cited_sources"] == 2
    assert stats["source_count"] == 3
    assert stats["citation_rate"] == 0.6667


def test_no_sources_and_no_markers() -> None:
    stats = citation_stats("根据现有资料无法回答这个问题。", 0, set())

    assert stats["markers"] == 0
    assert stats["invalid_markers"] == 0
    assert stats["citation_rate"] == 0.0


def test_repeated_markers_are_counted_per_occurrence() -> None:
    stats = citation_stats("结论 [1]，补充 [1]。", 2, {1})

    assert stats["markers"] == 2
    assert stats["valid_markers"] == 2
