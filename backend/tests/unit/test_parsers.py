import zipfile
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
    path = tmp_path / "data.bin"
    path.write_text("a,b", encoding="utf-8")

    with pytest.raises(ParserError, match="暂不支持"):
        parse_document(path)


# --------------------------------------------------------------------------- #
# Office formats: fixtures are hand-built OOXML packages, because tests must run
# without python-pptx / openpyxl installed (the parser itself is stdlib-only).
# --------------------------------------------------------------------------- #

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _rels(*pairs: tuple[str, str]) -> str:
    rel_type = f"{R_NS}/officeDocument"
    rows = "".join(
        f'<Relationship Id="{rid}" Type="{rel_type}" Target="{target}"/>'
        for rid, target in pairs
    )
    return f'<Relationships xmlns="{PKG_REL_NS}">{rows}</Relationships>'


def _build_pptx(path: Path) -> None:
    """Two slides, deliberately declared out of filename order in the deck."""
    # Deck order is the reverse of filename order, so a parser that sorted by
    # filename would produce the wrong page order and still pass a weaker test.
    presentation = (
        f'<p:presentation xmlns:p="{P_NS}" xmlns:r="{R_NS}">'
        '<p:sldIdLst>'
        '<p:sldId id="256" r:id="rIdA"/>'
        '<p:sldId id="257" r:id="rIdB"/>'
        "</p:sldIdLst></p:presentation>"
    )
    slide_one = (
        f'<p:sld xmlns:a="{A_NS}" xmlns:p="{P_NS}"><p:cSld><p:spTree>'
        "<p:sp><p:txBody>"
        f'<a:p><a:r><a:t>第二页：续航说明</a:t></a:r></a:p>'
        "</p:txBody></p:sp>"
        "</p:spTree></p:cSld></p:sld>"
    )
    slide_two = (
        f'<p:sld xmlns:a="{A_NS}" xmlns:p="{P_NS}"><p:cSld><p:spTree>'
        "<p:sp><p:txBody>"
        '<a:p><a:r><a:t>第一页：产品概览</a:t></a:r></a:p>'
        '<a:p><a:r><a:t>X1 支持心率监测。</a:t></a:r></a:p>'
        "</p:txBody></p:sp>"
        "<p:graphicFrame><a:graphic><a:graphicData>"
        "<a:tbl>"
        "<a:tr>"
        f'<a:tc><a:txBody><a:p><a:r><a:t>型号</a:t></a:r></a:p></a:txBody></a:tc>'
        f'<a:tc><a:txBody><a:p><a:r><a:t>续航</a:t></a:r></a:p></a:txBody></a:tc>'
        "</a:tr>"
        "<a:tr>"
        f'<a:tc><a:txBody><a:p><a:r><a:t>X1</a:t></a:r></a:p></a:txBody></a:tc>'
        f'<a:tc><a:txBody><a:p><a:r><a:t>14 天</a:t></a:r></a:p></a:txBody></a:tc>'
        "</a:tr>"
        "</a:tbl>"
        "</a:graphicData></a:graphic></p:graphicFrame>"
        "</p:spTree></p:cSld></p:sld>"
    )
    notes = (
        f'<p:notes xmlns:a="{A_NS}" xmlns:p="{P_NS}"><p:cSld><p:spTree>'
        "<p:sp><p:txBody>"
        '<a:p><a:r><a:t>口头补充：竞品只有 7 天。</a:t></a:r></a:p>'
        '<a:p><a:r><a:t>1</a:t></a:r></a:p>'
        "</p:txBody></p:sp>"
        "</p:spTree></p:cSld></p:notes>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            _rels(("rIdA", "slides/slide2.xml"), ("rIdB", "slides/slide1.xml")),
        )
        # rIdA -> slide2.xml ("第一页") first, rIdB -> slide1.xml ("第二页").
        archive.writestr("ppt/slides/slide1.xml", slide_one)
        archive.writestr("ppt/slides/slide2.xml", slide_two)
        archive.writestr(
            "ppt/slides/_rels/slide2.xml.rels",
            _rels(("rIdN", "../notesSlides/notesSlide1.xml")),
        )
        archive.writestr("ppt/notesSlides/notesSlide1.xml", notes)


def test_pptx_follows_deck_order_not_filename_order(tmp_path: Path) -> None:
    path = tmp_path / "deck.pptx"
    _build_pptx(path)

    parsed = parse_document(path)

    # presentation.xml puts slide2.xml ("第一页") ahead of slide1.xml; sorting by
    # filename would invert the deck and fail this assertion.
    assert parsed.text.index("第一页：产品概览") < parsed.text.index("第二页：续航说明")
    assert parsed.page_count == 2


def test_pptx_keeps_tables_and_notes(tmp_path: Path) -> None:
    path = tmp_path / "deck.pptx"
    _build_pptx(path)

    parsed = parse_document(path)

    assert "型号 | 续航" in parsed.text
    assert "X1 | 14 天" in parsed.text
    assert "备注：口头补充：竞品只有 7 天。" in parsed.text
    # Slide-number placeholders must not leak into the text.
    assert "\n备注：1" not in parsed.text


def test_pptx_maps_slide_numbers(tmp_path: Path) -> None:
    path = tmp_path / "deck.pptx"
    _build_pptx(path)

    parsed = parse_document(path)

    assert [page for _, page in parsed.page_starts] == [1, 2]
    assert parsed.page_starts[1][0] > 0


def _build_xlsx(path: Path) -> None:
    workbook = (
        f'<workbook xmlns="{S_NS}" xmlns:r="{R_NS}"><sheets>'
        '<sheet name="设备清单" sheetId="1" r:id="rId1"/>'
        '<sheet name="空表" sheetId="2" r:id="rId2"/>'
        "</sheets></workbook>"
    )
    shared = (
        f'<sst xmlns="{S_NS}">'
        "<si><t>型号</t></si><si><t>续航</t></si><si><t>X1</t></si><si><t>14 天</t></si>"
        "</sst>"
    )
    # Row 2 skips column B on purpose: the gap must survive as an empty cell.
    sheet_one = (
        f'<worksheet xmlns="{S_NS}"><sheetData>'
        f'<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        f'<row r="2"><c r="A2" t="s"><v>2</v></c><c r="C2" t="s"><v>3</v></c></row>'
        "</sheetData></worksheet>"
    )
    sheet_two = f'<worksheet xmlns="{S_NS}"><sheetData><row r="1"/></sheetData></worksheet>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            _rels(("rId1", "worksheets/sheet1.xml"), ("rId2", "worksheets/sheet2.xml")),
        )
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_one)
        archive.writestr("xl/worksheets/sheet2.xml", sheet_two)


def test_xlsx_resolves_shared_strings_and_column_gaps(tmp_path: Path) -> None:
    path = tmp_path / "spec.xlsx"
    _build_xlsx(path)

    parsed = parse_document(path)

    assert "## 设备清单" in parsed.text
    assert "型号 | 续航" in parsed.text
    assert "X1 |  | 14 天" in parsed.text


def test_xlsx_skips_empty_sheets(tmp_path: Path) -> None:
    path = tmp_path / "spec.xlsx"
    _build_xlsx(path)

    parsed = parse_document(path)

    assert "空表" not in parsed.text
    assert parsed.page_count == 1


def test_xlsx_numbers_without_shared_strings(tmp_path: Path) -> None:
    sheet = (
        f'<worksheet xmlns="{S_NS}"><sheetData>'
        '<row r="1"><c r="A1"><v>2025</v></c><c r="B1"><v>3.5</v></c></row>'
        "</sheetData></worksheet>"
    )
    path = tmp_path / "nums.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{S_NS}" xmlns:r="{R_NS}"><sheets>'
            '<sheet name="数据" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr("xl/_rels/workbook.xml.rels", _rels(("rId1", "worksheets/sheet1.xml")))
        archive.writestr("xl/worksheets/sheet1.xml", sheet)

    parsed = parse_document(path)
    assert "2025 | 3.5" in parsed.text


def test_not_an_office_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broken.pptx"
    path.write_bytes(b"not a zip file")

    with pytest.raises(ParserError, match="不是有效的 Office 文件"):
        parse_document(path)


def test_csv_becomes_a_markdown_table(tmp_path: Path) -> None:
    path = tmp_path / "prices.csv"
    path.write_text("型号,价格\nX1,1999\nX2,2999\n", encoding="utf-8")

    parsed = parse_document(path)

    assert "型号 | 价格" in parsed.text
    assert "--- | ---" in parsed.text
    assert "X1 | 1999" in parsed.text


def test_tsv_uses_tab_delimiter(tmp_path: Path) -> None:
    path = tmp_path / "prices.tsv"
    path.write_text("型号\t价格\nX1\t1999\n", encoding="utf-8")

    parsed = parse_document(path)

    assert "型号 | 价格" in parsed.text
    assert "\t" not in parsed.text


def test_html_file_strips_chrome(tmp_path: Path) -> None:
    path = tmp_path / "page.html"
    path.write_text(
        "<html><head><title>接入指南</title></head><body>"
        '<nav class="navbar">首页 文档 下载</nav>'
        "<article><h1>接入指南</h1><p>先在控制台创建 API Key。</p></article>"
        "</body></html>",
        encoding="utf-8",
    )

    parsed = parse_document(path)

    assert "先在控制台创建 API Key。" in parsed.text
    assert "接入指南" in parsed.text
