"""Build the knowledge graph for one knowledge base (or every knowledge base).

Usage:
    PYTHONPATH=. .venv/Scripts/python.exe scripts/build_graph.py --name "星河科技"
    PYTHONPATH=. .venv/Scripts/python.exe scripts/build_graph.py --all
    PYTHONPATH=. .venv/Scripts/python.exe scripts/build_graph.py --id <uuid> --clear

This costs one LLM call per chunk. The demo corpus (15 chunks) is cheap; a
thousand-chunk corpus is not, which is exactly why the HTTP endpoint runs the same
work as a background job and the UI shows progress from ``/graph/stats``.
"""

from app.core.env import configure_env

configure_env()

import argparse  # noqa: E402
import asyncio  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.knowledge_base import KnowledgeBase  # noqa: E402
from app.services import graph_service  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.deepseek_api_key:
        print("缺少 DEEPSEEK_API_KEY，无法抽取实体。")
        return 2

    async with SessionLocal() as db:
        if args.id:
            kb = await db.get(KnowledgeBase, args.id)
            targets = [kb] if kb else []
        elif args.name:
            kb = await db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == args.name))
            targets = [kb] if kb else []
        else:
            targets = list(
                (await db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.created_at))).all()
            )

        if not targets:
            print("没有找到知识库。")
            return 1

        for kb in targets:
            if args.clear:
                await graph_service.clear_graph(db, kb.id)
                print(f"[{kb.name}] 已清空图谱")
                continue
            print(f"[{kb.name}] 开始抽取 …")
            stats = await graph_service.build_for_kb(db, kb)
            print(
                f"[{kb.name}] 文档 {stats.documents} · chunk {stats.chunks} · "
                f"实体提及 {stats.entities} · 关系 {stats.relations} · 失败 {stats.failed_chunks}"
            )
            summary = await graph_service.stats(db, kb.id)
            print(
                f"[{kb.name}] 图谱：实体 {summary['entities']} · "
                f"关系 {summary['relations']} · 提及 {summary['mentions']}"
            )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the GraphRAG knowledge graph")
    parser.add_argument("--id", help="knowledge base UUID")
    parser.add_argument("--name", help="knowledge base name")
    parser.add_argument("--all", action="store_true", help="build every knowledge base")
    parser.add_argument("--clear", action="store_true", help="delete the graph instead of building")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
