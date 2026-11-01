"""Inspect the cases a run failed to retrieve anything for.

An aggregate Recall@5 says "half the questions are missed"; it does not say
*why*. This script prints, for every answerable case where nothing relevant
made it into the top-k, the question, the labelled gold chunk(s) and what the
retriever actually returned, so the miss can be classified:

* **标注问题** — the gold chunk does not contain the answer at all. The label
  is wrong, and no retriever change will ever fix it. This is exactly how the
  "Angular 信号" mislabel was found.
* **召回问题** — the gold chunk is right but never entered the candidate pool
  (embedding / chunking problem).
* **排序问题** — the gold chunk was retrieved but ranked below the cut-off.

A crude lexical overlap between the question and the gold chunk is printed as a
sanity signal: zero overlap usually means the label deserves a second look.

Usage
-----
    PYTHONPATH=. python scripts/eval_misses.py <run-uuid> [--top 5] [--limit 20]
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from app.models.chunk import Chunk  # noqa: E402
from app.models.evaluation import EvalCase, EvalResult, EvalRun  # noqa: E402

TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{1,}|[\u4e00-\u9fff]{2,}")
# Words that show up in almost every question and would make every overlap
# score look healthy.
STOPWORDS = {
    "什么", "怎么", "如何", "为什么", "哪些", "哪个", "是否", "可以", "需要", "时候",
    "区别", "吗", "的", "了", "和", "与", "在", "是", "有", "用", "一个", "里面",
    "the", "how", "what", "why", "does", "do", "is", "are", "to", "in", "of", "and",
}


def _terms(text: str) -> set[str]:
    return {t.lower() for t in TOKEN_RE.findall(text) if t.lower() not in STOPWORDS}


def _overlap(question: str, content: str) -> tuple[int, int, list[str]]:
    """Return (hit count, term count, missing terms) for question vs. chunk."""
    q_terms = _terms(question)
    lowered = content.lower()
    missing = sorted(t for t in q_terms if t not in lowered)
    return len(q_terms) - len(missing), len(q_terms), missing


def _shorten(text: str, limit: int) -> str:
    flat = " ".join((text or "").split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


async def _chunk_text(db, document_id: str, chunk_index: int) -> tuple[str, str]:
    chunk = (
        await db.execute(
            select(Chunk).where(
                Chunk.document_id == document_id, Chunk.chunk_index == chunk_index
            )
        )
    ).scalar_one_or_none()
    if chunk is None:
        return "(该块不存在)", ""
    return (chunk.heading_path or "(无标题路径)"), chunk.content


async def main(args: argparse.Namespace) -> int:
    async with SessionLocal() as db:
        run = (
            await db.execute(
                select(EvalRun).where(EvalRun.id == args.run)
            )
        ).scalar_one_or_none()
        if run is None:
            raise SystemExit(f"找不到运行 {args.run}")

        results = (
            await db.execute(
                select(EvalResult)
                .where(EvalResult.run_id == run.id)
                .order_by(EvalResult.question)
            )
        ).scalars().all()

        misses = [
            r
            for r in results
            if r.answerable
            and (r.metrics or {}).get(f"recall@{args.top}", 0) == 0
        ]
        print(
            f"\n运行 {run.id}：{len(results)} 条用例，其中"
            f" {len(misses)} 条可答案例在 top-{args.top} 内一条相关内容都没召回。\n"
        )

        # Annotator notes live on the case, not on the snapshot, so a label can
        # be re-read after the run without re-running it.
        notes_by_case = {
            case.id: case.notes
            for case in (
                await db.execute(
                    select(EvalCase).where(
                        EvalCase.id.in_([r.case_id for r in misses])
                    )
                )
            ).scalars().all()
        }

        flagged = 0
        for result in misses[: args.limit]:
            print("=" * 100)
            print(f"Q: {result.question}")
            if notes_by_case.get(result.case_id):
                print(f"   标注备注: {notes_by_case[result.case_id]}")
            for ref in result.relevant_refs or []:
                heading, content = await _chunk_text(
                    db, ref["document_id"], ref["chunk_index"]
                )
                hit, total, missing = _overlap(result.question, content)
                if total and hit == 0:
                    flagged += 1
                print(
                    f"   GOLD  {ref.get('filename', '?')} #{ref['chunk_index']}"
                    f"  [{heading}]  词面命中 {hit}/{total}"
                    + (f"  缺失: {missing[:8]}" if missing and hit == 0 else "")
                )
                print(f"        {_shorten(content, args.gold_chars)}")
            for item in (result.retrieved or [])[: args.top]:
                score = item.get("rerank_score") or item.get("fused_score") or 0.0
                print(
                    f"   {item.get('rank')}. {item.get('filename', '?')}"
                    f" #{item.get('chunk_index')}"
                    f"  [{item.get('heading_path') or '-'}]"
                    f"  vec={item.get('vector_score')} score={score}"
                )
                print(f"        {_shorten(item.get('content', ''), args.hit_chars)}")
        if len(misses) > args.limit:
            print(f"\n（还有 {len(misses) - args.limit} 条未展示，用 --limit 放宽）")

        print(
            f"\n小结：{len(misses)} 条零召回，其中 {flagged} 条的 gold 块"
            "和问题在词面上完全无重叠——优先怀疑标注，而不是检索。"
        )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", help="运行 UUID")
    parser.add_argument("--top", type=int, default=5, help="判定零召回所用的 k")
    parser.add_argument("--limit", type=int, default=20, help="最多展示多少条")
    parser.add_argument("--gold-chars", type=int, default=260, help="gold 块预览长度")
    parser.add_argument("--hit-chars", type=int, default=140, help="召回块预览长度")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
