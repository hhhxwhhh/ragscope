import hashlib
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.errors import BadRequestError, PayloadTooLargeError, UnsupportedMediaTypeError
from app.rag.parsers import SUPPORTED_EXTENSIONS

READ_BLOCK_BYTES = 1024 * 1024


def storage_root() -> Path:
    return Path(get_settings().storage_dir).resolve()


def sanitize_filename(filename: str | None) -> str:
    name = Path(filename or "").name.strip()
    if not name:
        raise BadRequestError("缺少文件名")
    return name


async def save_upload(
    kb_id: UUID,
    document_id: UUID,
    upload: UploadFile,
) -> tuple[Path, int, str]:
    """Stream an upload to disk, enforcing size limits and computing sha256."""
    filename = sanitize_filename(upload.filename)
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedMediaTypeError("仅支持 .md / .txt / .pdf / .docx 文件")

    target = storage_root() / str(kb_id) / f"{document_id}{ext}"
    target.parent.mkdir(parents=True, exist_ok=True)

    max_bytes = get_settings().max_upload_bytes
    hasher = hashlib.sha256()
    size = 0
    try:
        with target.open("wb") as handle:
            while block := await upload.read(READ_BLOCK_BYTES):
                size += len(block)
                if size > max_bytes:
                    raise PayloadTooLargeError(f"文件不能超过 {max_bytes // (1024 * 1024)} MB")
                hasher.update(block)
                handle.write(block)
        if size == 0:
            raise BadRequestError("文件内容为空")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    return target, size, hasher.hexdigest()


def remove_stored_file(path: str) -> None:
    """Delete a file only if it lives under the configured storage root."""
    root = storage_root()
    try:
        target = Path(path).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        return
    target.unlink(missing_ok=True)


def save_text(kb_id: UUID, document_id: UUID, filename: str, text: str) -> tuple[Path, int, str]:
    """Persist connector output as a UTF-8 markdown file.

    Connectors and uploads go through the same ``storage_root``/sha256 contract,
    so the ingestion pipeline does not care where a document came from — it only
    sees a path on disk.
    """
    safe = sanitize_filename(filename)
    if not safe.lower().endswith((".md", ".txt")):
        safe = f"{safe}.md"
    target = storage_root() / str(kb_id) / f"{document_id}{Path(safe).suffix.lower()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode("utf-8")
    target.write_bytes(payload)
    return target, len(payload), hashlib.sha256(payload).hexdigest()
