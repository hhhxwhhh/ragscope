"""Run a retrieval evaluation from the command line, without the HTTP server.

Evaluation is only useful if it is cheap to repeat: the same command has to
reproduce a run after a retriever change, otherwise every number in the README is
an unrepeatable anecdote. This script wires together the same service functions
the API uses (``create_run`` + ``execute_run``) so a run can be replayed after
restarting the machine or changing a config knob.

Usage
-----
    PYTHONPATH=. python scripts/run_eval.py --kb <uuid>
        [--candidate-top-k 20] [--final-top-k 5] [--min-score 0.45]
        [--no-rerank] [--no-crag] [--no-multi-query] [--no-compress] [--generation]

Override flags map onto :class:`~app.schemas.eval.RetrievalConfigIn`; anything
left out falls back to the knowledge base's own settings, which is what makes
"baseline vs. changed" comparisons honest.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from app.models.evaluation import EvalDataset  # noqa: E402
from app.schemas.eval import EvalRunCreate, RetrievalConfigIn  # noqa: E402
from app.services import eval_service  # noqa: E402

LOGGER = logging.getLogger("run_eval")

# The subset of aggregate metrics worth reading after a run; printing all of
# them buries the four numbers that actually move.
HEADLINE = (
    "recall@1",
    "recall@3",
    "recall@5",
    "recall@10",
    "mrr",
    "hit@5",
    "refusal_accuracy",
    "false_refusal_rate",
    "citation_compliance",
    "faithfulness_ratio",
    "faithfulness_grounded_rate",
)


def _print_summary(run) -> None:
    metrics = run.metrics or {}
    print(f"\n运行 {run.id}  状态={run.status}  用例={metrics.get('case_count')}")
    print(json.dumps(run.config, ensure_ascii=False, indent=2))
    print("\n--- 指标 ---")
    for key in HEADLINE:
        if key in metrics:
            print(f"  {key:<28} {metrics[key]}")
    if "latency_ms" in metrics:
        print(f"  延迟 {metrics['latency_ms']}")
    extras = sorted(k for k in metrics if k not in HEADLINE and k != "latency_ms")
    if extras:
        print(f"\n  （其余 {len(extras)} 项：{', '.join(extras)}）")


async def main(args: argparse.Namespace) -> int:
    async with SessionLocal() as db:
        query = select(EvalDataset).where(EvalDataset.kb_id == args.kb)
        if args.dataset:
            query = query.where(EvalDataset.name == args.dataset)
        dataset = (await db.execute(query)).scalar_one_or_none()
        if dataset is None:
            raise SystemExit(f"知识库 {args.kb} 下找不到评测集，先跑 make_eval_dataset.py")

        overrides: dict[str, object] = {}
        for flag, key in (
            ("candidate_top_k", "fusion_top_k"),
            ("final_top_k", "final_top_k"),
            ("min_score", "min_vector_score"),
        ):
            value = getattr(args, flag)
            if value is not None:
                overrides[key] = value
        for flag, key in (
            ("no_rerank", "use_rerank"),
            ("no_crag", "use_crag"),
            ("no_multi_query", "use_multi_query"),
            ("no_compress", "use_compress"),
        ):
            if getattr(args, flag):
                overrides[key] = False

        payload = EvalRunCreate(
            config=RetrievalConfigIn(**overrides) if overrides else None,
            include_generation=args.generation,
        )
        run = await eval_service.create_run(db, dataset, payload)
        await db.commit()
        print(f"已创建运行 {run.id}，开始执行 {run.progress.get('total')} 条用例…")
        await eval_service.execute_run(run.id)

        await db.refresh(run)
        _print_summary(run)
        if run.status != "completed":
            print(f"\n运行未正常结束：{run.status}")
            return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb", required=True, help="知识库 UUID")
    parser.add_argument("--dataset", help="评测集名称（默认取该 KB 下唯一的一个）")
    parser.add_argument("--candidate-top-k", type=int, help="融合后保留的候选数")
    parser.add_argument("--final-top-k", type=int, help="截断到最终上下文的条数")
    parser.add_argument("--min-score", type=float, help="向量相似度下限")
    parser.add_argument("--no-rerank", action="store_true", help="关闭重排")
    parser.add_argument("--no-crag", action="store_true", help="关闭 CRAG 证据分级")
    parser.add_argument("--no-multi-query", action="store_true", help="关闭多查询扩展")
    parser.add_argument("--no-compress", action="store_true", help="关闭上下文压缩")
    parser.add_argument("--generation", action="store_true", help="同时跑生成侧评测（消耗 LLM）")
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(asyncio.run(main(parse_args())))
