from pathlib import Path

SUPPORTED_EXTENSIONS = {".md", ".txt"}


class ParserError(ValueError):
    pass


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParserError("无法识别文件编码，请使用 UTF-8 或 GBK")


def parse_document(path: Path) -> str:
    """Extract plain text from a stored document.

    Only Markdown and TXT are supported in v1; PDF/DOCX are a later extension.
    """
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ParserError(f"暂不支持的文件类型：{suffix}")
    return _read_text(path)
