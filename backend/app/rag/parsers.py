"""Document parsing for Markdown, TXT, PDF and DOCX.

The parser returns plain text plus a page-offset map. Chunking uses raw
character offsets, so ``page_starts`` lets every chunk inherit the page it
starts on without changing the chunker's text coordinates.

PDF extraction is text-only: scanned pages without an embedded text layer come
out empty (OCR is deliberately out of scope). DOCX headings are converted to
Markdown ``#`` markers so the existing structure-aware chunker can reuse them.
Plain-text files may carry ``【第 N 页】`` markers (the output of
``scripts/ocr_pdf.py``); those markers become page offsets for citations.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}
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


def parse_document(path: Path) -> ParsedDocument:
    """Extract text and page offsets from a stored document."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ParserError(f"暂不支持的文件类型：{suffix}")
    if suffix == ".pdf":
        return _parse_pdf(path)
    if suffix == ".docx":
        return _parse_docx(path)
    return _parse_text_file(path)
