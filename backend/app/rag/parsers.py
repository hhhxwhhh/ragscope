"""Document parsing for Markdown, TXT, PDF, DOCX, PPTX, XLSX, CSV/TSV and HTML.

The parser returns plain text plus a page-offset map. Chunking uses raw
character offsets, so ``page_starts`` lets every chunk inherit the page it
starts on without changing the chunker's text coordinates. "Page" means whatever
the format's natural unit is: PDF page, slide number, worksheet index.

PDF extraction is text-only: scanned pages without an embedded text layer come
out empty (OCR is deliberately out of scope). DOCX headings are converted to
Markdown ``#`` markers so the existing structure-aware chunker can reuse them.
Plain-text files may carry ``【第 N 页】`` markers (the output of
``scripts/ocr_pdf.py``); those markers become page offsets for citations.

Office and HTML formats are imported lazily inside their own parser: python-pptx
and openpyxl are heavy enough that importing them at module load slows every
request, including ones that never touch a document.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

SUPPORTED_EXTENSIONS = {
    ".md",
    ".txt",
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".xlsm",
    ".csv",
    ".tsv",
    ".html",
    ".htm",
}
PAGE_SEPARATOR = "\n\n"

_HEADING_STYLE_RE = re.compile(r"^(?:heading|标题)\s*(\d+)$", re.IGNORECASE)
# Offline OCR writes one marker line per page; treat those as page boundaries so
# chunks from the generated .txt still expose a page number in citations.
_PAGE_MARKER_RE = re.compile(r"^【第\s*(\d+)\s*页】\s*$", re.MULTILINE)


class ParserError(ValueError):
    pass


@dataclass(slots=True)
class ParsedDocument:
    text: str
    # (character offset where the page starts, 1-based page number)
    page_starts: list[tuple[int, int]] = field(default_factory=list)
    page_count: int | None = None


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParserError("无法识别文件编码，请使用 UTF-8 或 GBK")


def _parse_text_file(path: Path) -> ParsedDocument:
    text = _read_text(path)
    starts = [
        (match.start(), int(match.group(1)))
        for match in _PAGE_MARKER_RE.finditer(text)
    ]
    page_count = max((page for _, page in starts), default=None)
    return ParsedDocument(text=text, page_starts=starts, page_count=page_count)


def _parse_pdf(path: Path) -> ParsedDocument:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # malformed or encrypted PDF
        raise ParserError(f"PDF 解析失败：{exc}") from exc

    if reader.is_encrypted:
        try:
            # Empty password covers the common "owner password only" case.
            reader.decrypt("")
        except Exception as exc:
            raise ParserError("PDF 已加密，无法读取") from exc

    parts: list[str] = []
    starts: list[tuple[int, int]] = []
    cursor = 0
    page_count = len(reader.pages)

    for page_number, page in enumerate(reader.pages, start=1):
        try:
            page_text = (page.extract_text() or "").strip()
        except Exception as exc:  # one broken page should not kill the document
            raise ParserError(f"PDF 第 {page_number} 页解析失败：{exc}") from exc
        if not page_text:
            continue
        starts.append((cursor, page_number))
        parts.append(page_text)
        cursor += len(page_text) + len(PAGE_SEPARATOR)

    text = PAGE_SEPARATOR.join(parts)
    if not text.strip():
        raise ParserError("PDF 没有可提取的文本（可能是扫描件，需要 OCR）")
    return ParsedDocument(text=text, page_starts=starts, page_count=page_count)


def _docx_heading_prefix(paragraph) -> str:
    try:
        style_name = paragraph.style.name or ""
    except Exception:
        return ""
    match = _HEADING_STYLE_RE.match(style_name.strip())
    if match:
        level = min(max(int(match.group(1)), 1), 6)
        return "#" * level + " "
    return ""


def _docx_table_text(table) -> str:
    lines: list[str] = []
    for row in table.rows:
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _parse_docx(path: Path) -> ParsedDocument:
    from docx import Document as DocxDocument
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = DocxDocument(str(path))
    except Exception as exc:
        raise ParserError(f"DOCX 解析失败：{exc}") from exc

    blocks: list[str] = []
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if text:
                blocks.append(f"{_docx_heading_prefix(paragraph)}{text}")
        elif tag == "tbl":
            table_text = _docx_table_text(Table(child, document))
            if table_text.strip():
                blocks.append(table_text)

    text = PAGE_SEPARATOR.join(blocks)
    if not text.strip():
        raise ParserError("DOCX 没有可索引的文本")
    return ParsedDocument(text=text)


# --- OOXML (PPTX / XLSX) ---------------------------------------------------- #
#
# Both formats are ZIP packages of XML, and the only thing ingestion needs out of
# them is the visible text. python-pptx / openpyxl would be the obvious choice,
# but they are heavy imports for a read-only text pass — and this project runs in
# environments where PyPI is unreachable, so a parser that refuses to work
# offline is a parser that cannot be deployed. The standard library is enough:
# ``zipfile`` for the container, ``ElementTree`` for the parts.
#
# What this buys beyond zero dependencies: no Office-version-specific API
# surface, and no silent failure when a deck uses a feature the library models
# differently. What it costs: no computed formula results (XLSX stores the last
# cached value, which is what is read here) and no SmartArt/diagram text.

_DRAWINGML = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_PRESENTATIONML = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
# Two different namespaces, easy to confuse: ``.rels`` files use the *package*
# one, while the ``r:id`` attribute that points at them uses the
# *officeDocument* one. Mixing them silently yields "no slides found".
_RELATIONSHIPS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_OFFICE_RELATIONSHIPS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_SPREADSHEETML = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

_MAX_TABLE_ROWS = 20_000


def _zip_read(archive, member: str) -> bytes | None:
    try:
        return archive.read(member)
    except KeyError:
        return None


def _rels_map(archive, part: str) -> dict[str, str]:
    """``rId -> target`` for one package part (``_rels/<part>.rels``)."""
    directory, name = part.rsplit("/", 1) if "/" in part else ("", part)
    member = f"{directory}/_rels/{name}.rels" if directory else f"_rels/{name}.rels"
    raw = _zip_read(archive, member)
    if raw is None:
        return {}
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return {}
    return {
        node.get("Id"): node.get("Target")
        for node in root.findall(f"{_RELATIONSHIPS}Relationship")
        if node.get("Id") and node.get("Target")
    }


def _normalize_member(base: str, target: str) -> str:
    """Resolve a relationship target (may be absolute or ../relative)."""
    if target.startswith("/"):
        return target.lstrip("/")
    parts = (base.rsplit("/", 1)[0] if "/" in base else "").split("/")
    for segment in target.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    return "/".join(parts)


def _paragraph_text(node) -> str:
    return "".join(run.text or "" for run in node.iter(f"{_DRAWINGML}t")).strip()


def _walk_slide(node, lines: list[str]) -> None:
    """Depth-first text collection that keeps tables tabular.

    Recursion is required rather than a flat ``iter("a:p")`` because PowerPoint
    nests content in groups (``p:grpSp``) and inside graphic frames; a flat scan
    also double-counts table cells, whose text lives in ``a:p`` nodes too.
    """
    for child in node:
        if child.tag == f"{_DRAWINGML}p":
            text = _paragraph_text(child)
            if text:
                lines.append(text)
        elif child.tag == f"{_DRAWINGML}tbl":
            for row in child.findall(f"{_DRAWINGML}tr"):
                cells: list[str] = []
                for cell in row.findall(f"{_DRAWINGML}tc"):
                    cell_lines: list[str] = []
                    _walk_slide(cell, cell_lines)
                    cells.append(" ".join(cell_lines).strip())
                if any(cells):
                    lines.append(" | ".join(cells))
        else:
            _walk_slide(child, lines)


def _parse_pptx(path: Path) -> ParsedDocument:
    import zipfile

    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ParserError(f"PPTX 解析失败：不是有效的 Office 文件（{exc}）") from exc

    with archive:
        slide_members = _ordered_slide_members(archive)
        if not slide_members:
            raise ParserError("PPTX 没有找到任何幻灯片")

        parts: list[str] = []
        starts: list[tuple[int, int]] = []
        cursor = 0
        for page_number, member in enumerate(slide_members, start=1):
            raw = _zip_read(archive, member)
            if raw is None:
                continue
            try:
                root = ElementTree.fromstring(raw)
            except ElementTree.ParseError:
                continue
            lines: list[str] = []
            _walk_slide(root, lines)

            # Speaker notes usually hold the explanation the slide only gestures
            # at; skipping them loses the most quotable part of a deck.
            notes = _read_notes(archive, member)
            if notes:
                lines.append(f"备注：{notes}")

            text = "\n".join(lines).strip()
            if not text:
                continue
            starts.append((cursor, page_number))
            parts.append(text)
            cursor += len(text) + len(PAGE_SEPARATOR)

    text = PAGE_SEPARATOR.join(parts)
    if not text.strip():
        raise ParserError("PPTX 没有可索引的文本（可能只有图片）")
    return ParsedDocument(text=text, page_starts=starts, page_count=len(slide_members))


def _ordered_slide_members(archive) -> list[str]:
    """Slide order from presentation.xml, falling back to filename order.

    ``slide3.xml`` is not necessarily the third slide: the deck's order lives in
    ``p:sldIdLst``. Reading it through the relationship map is the only correct
    order, and the fallback exists because hand-built files sometimes omit it.
    """
    raw = _zip_read(archive, "ppt/presentation.xml")
    if raw is not None:
        try:
            root = ElementTree.fromstring(raw)
            rels = _rels_map(archive, "ppt/presentation.xml")
            members: list[str] = []
            for slide_id in root.iter(f"{_PRESENTATIONML}sldId"):
                target = rels.get(slide_id.get(f"{_OFFICE_RELATIONSHIPS}id") or "")
                if target:
                    members.append(_normalize_member("ppt/presentation.xml", target))
            if members:
                return members
        except ElementTree.ParseError:
            pass

    return sorted(
        (name for name in archive.namelist() if name.startswith("ppt/slides/slide")),
        key=_member_number,
    )


def _member_number(name: str) -> int:
    digits = "".join(char for char in name.rsplit("/", 1)[-1] if char.isdigit())
    return int(digits) if digits else 0


def _read_notes(archive, slide_member: str) -> str:
    rels = _rels_map(archive, slide_member)
    for target in rels.values():
        if "notesSlide" not in (target or ""):
            continue
        raw = _zip_read(archive, _normalize_member(slide_member, target))
        if raw is None:
            continue
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError:
            continue
        lines: list[str] = []
        for paragraph in root.iter(f"{_DRAWINGML}p"):
            text = _paragraph_text(paragraph)
            # Slide-number placeholders live in notes too; they carry no meaning.
            if text and not text.isdigit():
                lines.append(text)
        if lines:
            return "\n".join(lines)
    return ""


def _column_index(cell) -> int | None:
    """``A1`` -> 0. Cells without a ref are positional and handled by the caller."""
    ref = cell.get("r") or ""
    letters = "".join(char for char in ref if char.isalpha())
    if not letters:
        return None
    index = 0
    for char in letters:
        index = index * 26 + (ord(char.upper()) - 64)
    return index - 1


def _parse_xlsx(path: Path, max_rows: int = _MAX_TABLE_ROWS) -> ParsedDocument:
    """One worksheet per "page", rows rendered as pipe-delimited lines.

    Spreadsheets are the format RAG handles worst: a bare cell dump loses which
    column a value belongs to. Keeping the row as one line preserves that
    grouping, and the sheet name becomes an ``##`` heading so the
    structure-aware chunker can split per sheet.
    """
    import zipfile

    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ParserError(f"XLSX 解析失败：不是有效的 Office 文件（{exc}）") from exc

    with archive:
        shared = _read_shared_strings(archive)
        sheets = _ordered_sheets(archive)
        if not sheets:
            raise ParserError("XLSX 没有找到任何工作表")

        parts: list[str] = []
        starts: list[tuple[int, int]] = []
        cursor = 0
        truncated = False
        for page_number, (title, member) in enumerate(sheets, start=1):
            raw = _zip_read(archive, member)
            if raw is None:
                continue
            try:
                root = ElementTree.fromstring(raw)
            except ElementTree.ParseError:
                continue
            lines = [f"## {title}"]
            for row in root.iter(f"{_SPREADSHEETML}row"):
                cells = _row_cells(row, shared)
                if not any(cells):
                    continue
                lines.append(" | ".join(cells))
                if len(lines) - 1 >= max_rows:
                    truncated = True
                    break
            if len(lines) == 1:
                continue
            text = "\n".join(lines)
            starts.append((cursor, page_number))
            parts.append(text)
            cursor += len(text) + len(PAGE_SEPARATOR)

    text = PAGE_SEPARATOR.join(parts)
    if not text.strip():
        raise ParserError("XLSX 没有可索引的文本")
    if truncated:
        text += f"\n\n（工作表行数超过 {max_rows} 行，已截断）"
    return ParsedDocument(text=text, page_starts=starts, page_count=len(parts))


def _read_shared_strings(archive) -> list[str]:
    raw = _zip_read(archive, "xl/sharedStrings.xml")
    if raw is None:
        return []
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return []
    # A shared string may be split across runs (<r><t>), so join every <t>.
    return ["".join(node.text or "" for node in item.iter(f"{_SPREADSHEETML}t")) for item in root]


def _ordered_sheets(archive) -> list[tuple[str, str]]:
    raw = _zip_read(archive, "xl/workbook.xml")
    if raw is None:
        return []
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return []
    rels = _rels_map(archive, "xl/workbook.xml")
    sheets: list[tuple[str, str]] = []
    for node in root.iter(f"{_SPREADSHEETML}sheet"):
        target = rels.get(node.get(f"{_OFFICE_RELATIONSHIPS}id") or "")
        if target:
            member = _normalize_member("xl/workbook.xml", target)
            sheets.append((node.get("name") or "Sheet", member))
    return sheets


def _row_cells(row, shared: list[str]) -> list[str]:
    """Expand a row into a dense list, filling gaps left by empty cells."""
    values: list[str] = []
    position = 0
    for cell in row.findall(f"{_SPREADSHEETML}c"):
        index = _column_index(cell)
        if index is None:
            index = position
        while len(values) < index:
            values.append("")
        values.append(_cell_text(cell, shared))
        position = index + 1
    return [value.strip() for value in values]


def _cell_text(cell, shared: list[str]) -> str:
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{_SPREADSHEETML}t"))
    value = cell.find(f"{_SPREADSHEETML}v")
    raw = value.text if value is not None and value.text else ""
    if cell_type == "s":
        # Shared-string index. Cached formula results and numbers are plain <v>.
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    return raw


def _parse_delimited(path: Path, delimiter: str, max_rows: int = 20_000) -> ParsedDocument:
    """CSV/TSV as a Markdown table: the header row names every column.

    Dumping "a,b,c" gives the embedder no idea what b means. A Markdown header
    row keeps that association inside the chunk, which is the difference between
    a retrievable spreadsheet and noise.
    """
    import csv
    import io

    text = _read_text(path)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows: list[list[str]] = []
    truncated = False
    for index, row in enumerate(reader):
        if index >= max_rows:
            truncated = True
            break
        cells = [cell.strip() for cell in row]
        if any(cells):
            rows.append(cells)
    if not rows:
        raise ParserError("表格文件没有可索引的内容")

    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    header = padded[0]
    lines = [" | ".join(header), " | ".join(["---"] * width)]
    lines.extend(" | ".join(row) for row in padded[1:])
    if truncated:
        lines.append(f"（表格超过 {max_rows} 行，已截断）")
    return ParsedDocument(text="\n".join(lines))


def _parse_html_file(path: Path) -> ParsedDocument:
    """Reuse the crawler's readability extractor on a locally uploaded HTML file.

    Uploading a saved page is the same problem as crawling one — nav bars,
    sidebars and scripts — so it uses the same extractor rather than a second
    implementation that would drift.
    """
    from app.services.sources.html_extract import extract

    html = _read_text(path)
    page = extract(html)
    # The extractor emits Markdown, not plain text: headings survive as ``#``
    # markers, which the structure-aware chunker uses for splitting.
    text = (page.markdown or "").strip()
    if not text:
        raise ParserError("HTML 没有可索引的正文")
    if page.title and not text.startswith("#"):
        text = f"# {page.title}\n\n{text}"
    return ParsedDocument(text=text)


_PARSERS = {
    ".pdf": lambda path: _parse_pdf(path),
    ".docx": lambda path: _parse_docx(path),
    ".pptx": lambda path: _parse_pptx(path),
    ".xlsx": lambda path: _parse_xlsx(path),
    ".xlsm": lambda path: _parse_xlsx(path),
    ".csv": lambda path: _parse_delimited(path, ","),
    ".tsv": lambda path: _parse_delimited(path, "\t"),
    ".html": lambda path: _parse_html_file(path),
    ".htm": lambda path: _parse_html_file(path),
}


def parse_document(path: Path) -> ParsedDocument:
    """Extract text and page offsets from a stored document."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ParserError(f"暂不支持的文件类型：{suffix}")
    return _PARSERS.get(suffix, _parse_text_file)(path)
