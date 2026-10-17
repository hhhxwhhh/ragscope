from app.rag.multi_query import parse_queries


def test_strips_numbering_bullets_and_duplicates() -> None:
    raw = """1. X1 的续航时间
- X1 电池容量
• X1 的续航时间
2) X2 的续航时间

X1 的续航是多长时间？"""

    queries = parse_queries(raw, original="X1 的续航是多长时间？", limit=5)

    assert queries == ["X1 的续航时间", "X1 电池容量", "X2 的续航时间"]


def test_respects_limit_and_drops_overlong_lines() -> None:
    raw = "\n".join(["查询一", "查询二", "查询三", "查询四"])
    raw += "\n" + "很长的查询" * 100

    queries = parse_queries(raw, original="原问题", limit=2)

    assert queries == ["查询一", "查询二"]


def test_empty_output_returns_no_queries() -> None:
    assert parse_queries("   \n\n", original="原问题", limit=3) == []
