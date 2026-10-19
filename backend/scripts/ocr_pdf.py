"""Convert a scanned PDF into plain text with local Chinese OCR.

The application parser is intentionally text-only: scanned PDFs are rejected
instead of silently producing an empty document. This offline helper bridges
that gap without adding OCR to the ingestion runtime.

Usage:
    python scripts/ocr_pdf.py input.pdf output.txt --dpi 180
    python scripts/ocr_pdf.py input.pdf output.txt --start 1 --end 20
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import fitz
import numpy as np
from rapidocr_onnxruntime import RapidOCR

PAGE_MARKER = "【第 {page} 页】"


def _page_text(engine: RapidOCR, image: np.ndarray) -> str:
    result, _ = engine(image)
    if not result:
        return ""
    # RapidOCR returns [box, text, score]; sort top-to-bottom then left-to-right
    # so two-column or noisy scans do not interleave lines.
    items = sorted(
        result,
        key=lambda item: (
            min(point[1] for point in item[0]),
            min(point[0] for point in item[0]),
        ),
    )
    return "\n".join(str(item[1]).strip() for item in items if str(item[1]).strip())


def convert(
    source: Path,
    target: Path,
    *,
    dpi: int,
    start: int,
    end: int | None,
) -> None:
    document = fitz.open(source)
    total = document.page_count
    last = min(end or total, total)
    if start < 1 or start > last:
        raise SystemExit(f"页码范围无效：start={start}, end={last}, total={total}")

    engine = RapidOCR()
    target.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    with target.open("w", encoding="utf-8") as output:
        for page_number in range(start, last + 1):
            page = document[page_number - 1]
            pixmap = page.get_pixmap(dpi=dpi)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )
            text = _page_text(engine, image)
            output.write(f"{PAGE_MARKER.format(page=page_number)}\n{text}\n\n")

            elapsed = time.perf_counter() - started
            done = page_number - start + 1
            rate = elapsed / done
            remaining = rate * (last - page_number)
            print(
                f"[{done}/{last - start + 1}] page {page_number}: "
                f"{len(text)} chars, {rate:.1f}s/page, eta {remaining / 60:.1f} min",
                flush=True,
            )

    document.close()
    print(f"written {target} in {(time.perf_counter() - started) / 60:.1f} min")


def main() -> None:
    parser = argparse.ArgumentParser(description="OCR a scanned PDF into UTF-8 text")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=None)
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"文件不存在：{args.source}")
    convert(args.source, args.target, dpi=args.dpi, start=args.start, end=args.end)


if __name__ == "__main__":
    sys.exit(main())
