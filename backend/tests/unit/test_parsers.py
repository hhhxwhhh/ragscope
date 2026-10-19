from pathlib import Path

import pytest
from docx import Document as DocxDocument
from pypdf import PdfWriter

from app.rag.parsers import ParserError, parse_document


def test_markdown_has_no_page_map(tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_text("# 标题\n\n正文内容。", encoding="utf-8")

    parsed = parse_document(path)

    assert "正文内容" in parsed.text
    assert parsed.page_starts == []
    assert parsed.page_count is None


def test_docx_preserves_headings_and_tables(tmp_path: Path) -> None:
    path = tmp_path / "doc.docx"
    document = DocxDocument()
    document.add_heading("产品手册", level=1)
    document.add_paragraph("X1 的续航是 14 天。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "型号"
    table.cell(0, 1).text = "续航"
    table.cell(1, 0).text = "X1"
    table.cell(1, 1).text = "14 天"
    document.save(path)

    parsed = parse_document(path)

    assert "# 产品手册" in parsed.text
    assert "X1 的续航是 14 天。" in parsed.text
    assert "型号 | 续航" in parsed.text
    assert parsed.page_starts == []


def test_txt_page_markers_become_page_offsets(tmp_path: Path) -> None:
    text = "【第 1 页】\n第一页的正文内容。\n\n【第 7 页】\n第七页的正文内容。\n"
    path = tmp_path / "book_ocr.txt"
    path.write_text(text, encoding="utf-8")

    parsed = parse_document(path)

    assert [page for _, page in parsed.page_starts] == [1, 7]
    assert parsed.page_starts[0][0] == 0
    assert parsed.page_starts[1][0] == parsed.text.index("【第 7 页】")
    assert parsed.page_count == 7


def test_txt_without_page_markers_has_no_page_map(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("普通文本没有页码标记。", encoding="utf-8")

    parsed = parse_document(path)

    assert parsed.page_starts == []
    assert parsed.page_count is None


def test_pdf_without_text_layer_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "scanned.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(ParserError, match="扫描件"):
        parse_document(path)


def test_unsupported_extension(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("a,b", encoding="utf-8")

    with pytest.raises(ParserError, match="暂不支持"):
        parse_document(path)
