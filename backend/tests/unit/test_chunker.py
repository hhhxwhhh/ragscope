import pytest

from app.rag.chunker import MIN_CHUNK_CHARS, build_chunks


def test_rejects_overlap_not_smaller_than_chunk_size() -> None:
    with pytest.raises(ValueError, match="chunk_overlap"):
        build_chunks("内容" * 100, chunk_size=50, chunk_overlap=50)


def test_heading_path_is_prefixed_to_embedding_text() -> None:
    text = (
        "# 产品手册\n\n"
        "## 续航\n\n"
        "X1 在标准测试环境下续航时间为十二小时，支持一整天的外出使用。\n"
    )
    chunks = build_chunks(text, chunk_size=200, chunk_overlap=0)

    assert len(chunks) == 1
    assert chunks[0].heading_path == "产品手册 / 续航"
    assert chunks[0].embedding_text.startswith("产品手册 / 续航\n")
    assert chunks[0].content in chunks[0].embedding_text


def test_prefers_sentence_boundary_and_keeps_offsets() -> None:
    text = "".join(
        f"第{index}句话用来测试切块边界，这里放一些足够长的内容。" for index in range(10)
    )
    chunks = build_chunks(text, chunk_size=80, chunk_overlap=20)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.content) <= 80
        assert text[chunk.char_start : chunk.char_end] == chunk.content
    # Every chunk but the last ends on sentence punctuation (the cut is pulled back).
    assert all(chunk.content.endswith("。") for chunk in chunks[:-1])
    # Overlap: the next chunk starts before the previous one ends.
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert previous.char_start < current.char_start < previous.char_end


def test_drops_chunks_shorter_than_minimum() -> None:
    assert MIN_CHUNK_CHARS > 10
    assert build_chunks("# 标题\n\n太短了", chunk_size=400, chunk_overlap=0) == []


def test_chunks_inherit_page_from_character_offset() -> None:
    page_one = "第一页的内容会重复一些描述，用来验证页码映射是否正确，确保切块长度超过最小限制。"
    page_two = "第二页的内容同样需要足够长才能形成切块，并且验证字符偏移落在第二页的范围内。"
    text = f"{page_one}\n\n{page_two}"
    page_two_start = len(page_one) + 2

    chunks = build_chunks(
        text,
        chunk_size=40,
        chunk_overlap=0,
        page_starts=[(0, 1), (page_two_start, 2)],
    )

    assert [chunk.page_number for chunk in chunks] == [1, 2]
    assert chunks[1].char_start >= page_two_start


def test_text_without_page_map_has_no_page_numbers() -> None:
    text = "没有页码映射的纯文本内容，切块以后页码字段应当为空，这样文本格式的文档也能正常入库。"
    chunks = build_chunks(text, chunk_size=400, chunk_overlap=0)

    assert chunks[0].page_number is None


def test_multiple_heading_levels_are_tracked() -> None:
    text = (
        "# 一级\n\n"
        "这一段属于一级标题下的内容，长度需要超过最小块限制，用来验证标题路径。\n"
        "## 二级\n\n"
        "这一段属于二级标题，同时也继承了父级标题，形成完整的标题路径。\n"
        "### 三级\n\n"
        "这一段属于三级标题，路径应当包含一到三级标题，便于引用时定位来源。\n"
    )
    chunks = build_chunks(text, chunk_size=400, chunk_overlap=0)
    paths = [chunk.heading_path for chunk in chunks]

    assert paths == ["一级", "一级 / 二级", "一级 / 二级 / 三级"]
