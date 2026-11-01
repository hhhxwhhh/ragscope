"""Break a finished evaluation run down by *how hard each case was labelled*.

A single Recall number hides its own composition. After expanding the labelled
set with deliberately hard cases (cross-document gold, semantically interfering
pages), the headline number moved down — without this breakdown that looks like
a regression, when in fact the *easy* subset did not move at all.

This script groups the scored cases of one run by:

* gold chunk count — 1 vs. 2+ (a comparison question needs two passages)
* gold document count — 1 vs. 2+ (pages that look alike are the hard ones)
* answerable vs. unanswerable

and reports retrieval quality per group, so "overall Recall" and "hard-case
Recall" can be read separately.

Usage
-----
    PYTHONPATH=. python scripts/eval_breakdown.py <run_id>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from app.models.evaluation import EvalResult, EvalRun  # noqa: E402

METRICS = ("recall@1", "recall@5", "recall@10", "mrr", "hit@5")


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _groups(rows: list[EvalResult]) -> list[tuple[str, list[EvalResult]]]:
    answerable = [row for row in rows if row.answerable]
    unanswerable = [row for row in rows if not row.answerable]

    def doc_count(row: EvalResult) -> int:
        return len({ref["document_id"] for ref in (row.relevant_refs or [])})

    return [
        (f"全部可答（{len(answerable)}）", answerable),
        (
            f"  gold 单块（{sum(1 for r in answerable if len(r.relevant_refs or []) == 1)}）",
            [r for r in answerable if len(r.relevant_refs or []) == 1],
        ),
        (
            f"  gold 多块（{sum(1 for r in answerable if len(r.relevant_refs or []) > 1)}）",
            [r for r in answerable if len(r.relevant_refs or []) > 1],
        ),
        (
            f"  gold 单篇文档（{sum(1 for r in answerable if doc_count(r) == 1)}）",
            [r for r in answerable if doc_count(r) == 1],
        ),
        (
            f"  gold 跨多文档（{sum(1 for r in answerable if doc_count(r) > 1)}）",
            [r for r in answerable if doc_count(r) > 1],
        ),
        (f"不可答（{len(unanswerable)}）", unanswerable),
    ]


def _render(rows: list[EvalResult]) -> str:
    scored = [row for row in rows if not row.error]
    if not scored:
        return "     （无有效样本）"
    out = []
    for key in METRICS:
        values = [
            float((row.metrics or {}).get(key, 0.0))
            for row in scored
            if key in (row.metrics or {})
        ]
        if values:
            out.append(f"{key}={_mean(values):.3f}")
    if any(not row.answerable for row in scored):
        refused = sum(1 for row in scored if row.refused)
        out.append(f"检索层拒答={refused}/{len(scored)}")
    return "     " + "  ".join(out)


async def breakdown(run_id: str) -> None:
    async with SessionLocal() as db:
        run = await db.get(EvalRun, run_id)
        if run is None:
            raise SystemExit(f"找不到运行 {run_id}")
        rows = (
            await db.execute(
                select(EvalResult).where(EvalResult.run_id == run.id)
            )
        ).scalars().all()
        if not rows:
            raise SystemExit("该运行没有任何结果")

        print(f"运行 {run.id}  状态={run.status}")
        print(
            f"配置：final_top_k={run.config.get('final_top_k')} "
            f"fusion_top_k={run.config.get('fusion_top_k')} "
            f"min_vector_score={run.config.get('min_vector_score')}"
        )
        print(f"\n按用例构成拆分（共 {len(rows)} 条）：")
        for label, subset in _groups(rows):
            print(label)
            print(_render(subset))

        errors = [row for row in rows if row.error]
        if errors:
            print(f"\n{len(errors)} 条用例报错：")
            for row in errors[:5]:
                print(f"   {row.question[:40]} :: {str(row.error)[:80]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", help="评测运行 UUID")
    asyncio.run(breakdown(parser.parse_args().run_id))


if __name__ == "__main__":
    main()
