import re
from bisect import bisect_right
from dataclasses import dataclass

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
SENTENCE_ENDINGS = "。！？!?；;"
MIN_CHUNK_CHARS = 30


@dataclass(slots=True)
class ChunkDraft:
    content: str
    embedding_text: str
    heading_path: str | None
    char_start: int
    char_end: int
    page_number: int | None = None


def _page_for(offset: int, page_starts: list[tuple[int, int]]) -> int | None:
    """Return the page whose text contains ``offset``."""
    if not page_starts:
        return None
    offsets = [start for start, _ in page_starts]
    index = bisect_right(offsets, offset) - 1
    return page_starts[index][1] if index >= 0 else page_starts[0][1]


def _split_blocks(text: str) -> list[tuple[str, int, str | None]]:
    """Split text into heading-scoped blocks and keep absolute offsets."""
    blocks: list[tuple[str, int, str | None]] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []
    buf_start = 0
    buf_heading: str | None = None
    position = 0

    def flush(end: int) -> None:
        nonlocal buf
        if not buf:
            return
        raw = "".join(buf)
        stripped = raw.strip()
        if stripped:
            leading = len(raw) - len(raw.lstrip())
            blocks.append((stripped, buf_start + leading, buf_heading))
        buf = []

    for line in text.splitlines(keepends=True):
        line_start = position
        position += len(line)

        match = HEADING_RE.match(line.rstrip("\r\n"))
        if match:
            flush(line_start)
            level = len(match.group(1))
            title = match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            buf_heading = " / ".join(title for _, title in stack)
            buf_start = line_start
            continue

        if not buf:
            buf_start = line_start
        buf.append(line)

    flush(position)
    return blocks


def _split_block(
    block: str,
    block_start: int,
    heading_path: str | None,
    chunk_size: int,
    chunk_overlap: int,
    page_starts: list[tuple[int, int]],
) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    length = len(block)
    pos = 0

    while pos < length:
        end = min(pos + chunk_size, length)

        if end < length:
            # Prefer cutting after sentence punctuation in the second half of the window.
            floor = pos + max(chunk_size // 2, chunk_overlap + 1)
            for index in range(end - 1, floor - 1, -1):
                if block[index] in SENTENCE_ENDINGS:
                    end = index + 1
                    break

        raw = block[pos:end]
        content = raw.strip().replace("\r\n", "\n").replace("\r", "\n")
        if content:
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw) - len(raw.rstrip())
            char_start = block_start + pos + leading
            chunks.append(
                ChunkDraft(
                    content=content,
                    embedding_text=f"{heading_path}\n{content}" if heading_path else content,
                    heading_path=heading_path,
                    char_start=char_start,
                    char_end=block_start + end - trailing,
                    page_number=_page_for(char_start, page_starts),
                )
            )

        if end >= length:
            break
        pos = max(end - chunk_overlap, pos + 1)

    return chunks


def build_chunks(
    text: str,
    chunk_size: int = 400,
    chunk_overlap: int = 60,
    page_starts: list[tuple[int, int]] | None = None,
) -> list[ChunkDraft]:
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap 必须小于 chunk_size")

    page_starts = page_starts or []
    drafts: list[ChunkDraft] = []
    for block, block_start, heading_path in _split_blocks(text):
        drafts.extend(
            _split_block(
                block,
                block_start,
                heading_path,
                chunk_size,
                chunk_overlap,
                page_starts,
            )
        )

    return [draft for draft in drafts if len(draft.content) >= MIN_CHUNK_CHARS]
