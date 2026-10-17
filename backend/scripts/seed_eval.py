"""Seed (or refresh) the demo evaluation dataset.

Usage:
    PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/seed_eval.py

The references are (filename, chunk_index) pairs from the five demo documents.
Re-running replaces the previous "演示评测集 v1" dataset and its runs.
"""

from app.core.env import configure_env

configure_env()

import asyncio  # noqa: E402

from demo_cases import KB_NAME, seed_eval_dataset  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.knowledge_base import KnowledgeBase  # noqa: E402


async def seed() -> None:
    async with SessionLocal() as db:
        kb = await db.scalar(
            select(KnowledgeBase)
            .where(KnowledgeBase.name == KB_NAME)
            .order_by(KnowledgeBase.created_at)
        )
        if kb is None:
            raise SystemExit(f"找不到知识库：{KB_NAME}，请先运行 scripts/seed_demo.py")

        dataset, cases = await seed_eval_dataset(db, kb)

        answerable = sum(1 for case in cases if case.answerable)
        print(
            f"数据集 {dataset.name} ({dataset.id})："
            f"{len(cases)} 条用例，可答 {answerable} 条，不可答 {len(cases) - answerable} 条"
        )


if __name__ == "__main__":
    asyncio.run(seed())
