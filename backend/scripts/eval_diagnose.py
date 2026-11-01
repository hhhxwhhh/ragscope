"""Split "the retriever missed it" into *how badly* it missed.

Chunk-level Recall@5 treats "returned chunk 13 of the right page" and "returned
a page from a different library" as equally wrong. When most misses turn out to
be neighbours of the gold chunk inside the *same page*, the headline number is
measuring chunk granularity, not retrieval quality — and the fix is a context
window, not a new embedding model.

For one run this script reports, on the answerable cases:

* ``strict`` — the labelled (document, chunk_index) pair is in the top-k.
* ``section`` — same document and same top-level section heading.
* ``document`` — the right document is in the top-k at all.
* ``recall@k`` recomputed at the document level.

and, for cases with no strict hit, how far away the nearest retrieved chunk of
the same document sits (1 = immediate neighbour, which a ±1 window would fix).

Usage
-----
    PYTHONPATH=. python scripts/eval_diagnose.py <run-uuid> [--top 5]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from app.models.chunk import Chunk  # noqa: E402
from app.models.evaluation import EvalResult, EvalRun  # noqa: E402


def _section(heading_path: str | None) -> str:
    return (heading_path or "").split(" / ")[0].strip()


def _covered(item: dict) -> set[tuple[str, int]]:
    """Chunk pairs one retrieved item stands for.

    A window-expanded item carries its neighbours' text, so it covers them.
    """
    doc_id = str(item.get("document_id"))
    covered = {(doc_id, int(item.get("chunk_index", -1)))}
    covered.update(
        (doc_id, int(index)) for index in (item.get("window_indexes") or [])
    )
    return covered


async def _headings(db, refs: list[dict]) -> dict[tuple[str, int], str]:
    """Fetch heading_path for each gold ref, keyed by (document_id, index)."""
    out: dict[tuple[str, int], str] = {}
    for ref in refs:
        doc_id, index = ref["document_id"], ref["chunk_index"]
        row = (
            await db.execute(
                select(Chunk.heading_path).where(
                    Chunk.document_id == doc_id, Chunk.chunk_index == index
                )
            )
        ).scalar_one_or_none()
        out[(str(doc_id), index)] = row or ""
    return out


async def main(args: argparse.Namespace) -> int:
    async with SessionLocal() as db:
        run = (
            await db.execute(select(EvalRun).where(EvalRun.id == args.run))
        ).scalar_one_or_none()
        if run is None:
            raise SystemExit(f"找不到运行 {args.run}")

        results = (
            await db.execute(
                select(EvalResult).where(EvalResult.run_id == run.id)
            )
        ).scalars().all()
        answerable = [r for r in results if r.answerable]

        strict_hits = doc_hits = section_hits = 0
        doc_gold_covered = 0.0
        doc_gold_total = 0.0
        distances: Counter[int] = Counter()

        for result in answerable:
            top = (result.retrieved or [])[: args.top]
            refs = result.relevant_refs or []
            gold_pairs = {(str(r["document_id"]), r["chunk_index"]) for r in refs}
            gold_docs = {str(r["document_id"]) for r in refs}
            gold_sections = await _headings(db, refs)
            gold_section_set = {
                (doc, _section(heading)) for (doc, _), heading in gold_sections.items()
            }

            hit = any(_covered(item) & gold_pairs for item in top)
            doc_hit = any(str(item.get("document_id")) in gold_docs for item in top)
            section_hit = any(
                (str(item.get("document_id")), _section(item.get("heading_path")))
                in gold_section_set
                for item in top
            )
            strict_hits += int(hit)
            doc_hits += int(doc_hit)
            section_hits += int(section_hit)

            seen_docs = {str(item.get("document_id")) for item in top}
            doc_gold_covered += len(gold_docs & seen_docs)
            doc_gold_total += len(gold_docs)

            if not hit:
                best = None
                for doc_id, index in gold_pairs:
                    for item in top:
                        if str(item.get("document_id")) != doc_id:
                            continue
                        gap = min(
                            abs(covered_index - int(index))
                            for _, covered_index in _covered(item)
                        )
                        best = gap if best is None else min(best, gap)
                distances[best if best is not None else -1] += 1

        total = len(answerable)
        print(f"\n运行 {run.id}：可答案例 {total} 条，判定深度 top-{args.top}")
        print(f"  块级严格命中率      {strict_hits / total:.3f}  ({strict_hits}/{total})")
        print(f"  同章节命中率        {section_hits / total:.3f}  ({section_hits}/{total})")
        print(f"  同文档命中率        {doc_hits / total:.3f}  ({doc_hits}/{total})")
        print(f"  文档级 recall@{args.top}     {doc_gold_covered / doc_gold_total:.3f}")

        missed = sum(distances.values())
        print(f"\n  未严格命中的 {missed} 条，最近同文档块的距离分布：")
        for gap in sorted(distances):
            label = "同文档无召回" if gap == -1 else f"相距 {gap} 块"
            print(f"    {label:<14} {distances[gap]} 条")
        near = distances[1]
        print(
            f"\n  其中 {near} 条的最近同文档块只差 1 个块位——"
            "±1 窗口扩展理论上能把这些变成命中。"
        )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", help="运行 UUID")
    parser.add_argument("--top", type=int, default=5, help="判定深度")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
