"""Seed a demo evaluation dataset for the demo knowledge base.

Usage:
    PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/seed_eval.py

The references are (filename, chunk_index) pairs from the five demo documents.
Re-running replaces the previous "演示评测集 v1" dataset and its runs.
"""

from app.core.env import configure_env

configure_env()

import asyncio  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.chunk import Chunk  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.evaluation import EvalDataset  # noqa: E402
from app.models.knowledge_base import KnowledgeBase  # noqa: E402
from app.schemas.eval import EvalCaseCreate, RelevantRef  # noqa: E402
from app.services import eval_service  # noqa: E402

KB_NAME = "星河科技产品库"
DATASET_NAME = "演示评测集 v1"

# (question, [(filename, chunk_index), ...], answerable)
CASES: list[tuple[str, list[tuple[str, int]], bool]] = [
    ("星河科技是什么时候成立的，总部在哪里？", [("01_公司介绍.md", 0)], True),
    ("星河科技主要做什么业务？", [("01_公司介绍.md", 0)], True),
    ("星河科技有多少员工？", [("01_公司介绍.md", 0)], True),
    ("X1 的屏幕和重量是多少？", [("02_智能手表X1产品手册.md", 0)], True),
    ("X1 的续航时间有多久？", [("02_智能手表X1产品手册.md", 0)], True),
    ("X1 支持独立 GPS 定位吗？", [("02_智能手表X1产品手册.md", 1)], True),
    ("X1 从零充满电需要多长时间？", [("02_智能手表X1产品手册.md", 2)], True),
    ("X2 的续航时间有多久？", [("03_智能手表X2产品手册.md", 0)], True),
    ("X2 支持离线音乐播放吗？", [("03_智能手表X2产品手册.md", 1)], True),
    ("X2 的充电时间是多少？", [("03_智能手表X2产品手册.md", 2)], True),
    (
        "X1 和 X2 的屏幕有什么区别？",
        [("02_智能手表X1产品手册.md", 0), ("03_智能手表X2产品手册.md", 0)],
        True,
    ),
    ("X2 内置多少存储空间，能存多少首歌？", [("03_智能手表X2产品手册.md", 1)], True),
    ("手表主机的保修期是多久？", [("04_售后服务政策.md", 0)], True),
    ("表带坏了可以免费保修吗？", [("04_售后服务政策.md", 0)], True),
    ("手表进水导致的故障能保修吗？", [("04_售后服务政策.md", 0)], True),
    ("手表支持几天无理由退货？", [("04_售后服务政策.md", 1)], True),
    ("15 天内出现性能故障可以换新吗？", [("04_售后服务政策.md", 1)], True),
    ("怎么申请寄修，运费谁承担？", [("04_售后服务政策.md", 2)], True),
    ("X1 可以连接蓝牙耳机吗？", [("05_常见问题FAQ.md", 0)], True),
    ("X2 可以连接蓝牙耳机听歌吗？", [("05_常见问题FAQ.md", 0)], True),
    ("为什么手表的实际续航和宣传不一致？", [("05_常见问题FAQ.md", 1)], True),
    ("手表怎么和手机同步数据？", [("05_常见问题FAQ.md", 2)], True),
    ("手表固件怎么升级？", [("05_常见问题FAQ.md", 3)], True),
    (
        "充电器丢了怎么办，X1 和 X2 的充电底座通用吗？",
        [("05_常见问题FAQ.md", 4)],
        True,
    ),
    ("量子计算机的纠错码是怎么实现的？", [], False),
    ("用 Python 写一个快速排序算法。", [], False),
    ("杭州明天会下雨吗？", [], False),
    ("红烧肉的家常做法是什么？", [], False),
    ("如何申请美国旅游签证？", [], False),
]


async def seed() -> None:
    async with SessionLocal() as db:
        kb = await db.scalar(
            select(KnowledgeBase)
            .where(KnowledgeBase.name == KB_NAME)
            .order_by(KnowledgeBase.created_at)
        )
        if kb is None:
            raise SystemExit(f"找不到知识库：{KB_NAME}，请先运行演示数据种子脚本")

        existing = await db.scalar(
            select(EvalDataset).where(
                EvalDataset.kb_id == kb.id,
                EvalDataset.name == DATASET_NAME,
            )
        )
        if existing is not None:
            await db.delete(existing)
            await db.commit()
            print(f"已删除旧数据集：{DATASET_NAME}")

        dataset = await eval_service.create_dataset(
            db,
            kb,
            DATASET_NAME,
            "基于星河科技演示文档的 29 条检索用例，含 5 条不可答问题。",
        )

        rows = (
            await db.execute(
                select(Document.filename, Chunk.chunk_index, Chunk.document_id)
                .join(Chunk, Chunk.document_id == Document.id)
                .where(Chunk.kb_id == kb.id)
            )
        ).all()
        chunk_index = {
            (filename, int(chunk_index)): document_id for filename, chunk_index, document_id in rows
        }

        payloads: list[EvalCaseCreate] = []
        for question, refs, answerable in CASES:
            relevant_refs = []
            for filename, chunk_no in refs:
                document_id = chunk_index.get((filename, chunk_no))
                if document_id is None:
                    raise SystemExit(f"chunk 不存在：{filename}#{chunk_no}")
                relevant_refs.append(
                    RelevantRef(
                        document_id=document_id,
                        chunk_index=chunk_no,
                        filename=filename,
                    )
                )
            payloads.append(
                EvalCaseCreate(
                    question=question,
                    relevant_refs=relevant_refs,
                    answerable=answerable,
                )
            )

        cases = await eval_service.create_cases(db, dataset, payloads)
        await db.commit()
        answerable = sum(1 for case in cases if case.answerable)
        print(
            f"数据集 {dataset.name} ({dataset.id})："
            f"{len(cases)} 条用例，可答 {answerable} 条，不可答 {len(cases) - answerable} 条"
        )


if __name__ == "__main__":
    asyncio.run(seed())
