from dataclasses import replace
from uuid import uuid4

from app.rag.pipeline import RetrievalConfig
from app.rag.retrieval_cache import make_key


def test_same_inputs_produce_same_key() -> None:
    kb_id = uuid4()
    config = RetrievalConfig()

    first = make_key(kb_id, 3, config, "X1 续航", [("user", "你好")])
    second = make_key(kb_id, 3, config, "X1 续航", [("user", "你好")])

    assert first == second


def test_key_changes_with_index_version_and_query() -> None:
    kb_id = uuid4()
    config = RetrievalConfig()

    base = make_key(kb_id, 1, config, "X1 续航", [])
    assert base != make_key(kb_id, 2, config, "X1 续航", [])
    assert base != make_key(kb_id, 1, config, "X2 续航", [])


def test_key_changes_with_retrieval_config_and_history() -> None:
    kb_id = uuid4()
    config = RetrievalConfig()

    base = make_key(kb_id, 1, config, "X1 续航", [])
    assert base != make_key(kb_id, 1, replace(config, use_rerank=False), "X1 续航", [])
    assert base != make_key(kb_id, 1, config, "X1 续航", [("user", "上一轮问题")])
