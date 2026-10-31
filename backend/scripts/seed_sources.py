"""Create one demo data source so the sync loop can be tried immediately.

Usage:
    PYTHONPATH=. .venv/Scripts/python.exe scripts/seed_sources.py --kb "星河科技"
    PYTHONPATH=. .venv/Scripts/python.exe scripts/seed_sources.py --kb "星河科技" \
        --type web --url https://fastapi.tiangolo.com/tutorial/

Defaults to a small, politely-scoped crawl of the public FastAPI tutorial: three
pages, one hop, 1.2s between requests. It is a *demo* — the point is to watch a
run appear in the UI and documents land in the knowledge base, not to mirror a
site.
"""

from app.core.env import configure_env

configure_env()

import argparse  # noqa: E402
import asyncio  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.knowledge_base import KnowledgeBase  # noqa: E402
from app.schemas.source import SourceCreate  # noqa: E402
from app.services import source_service  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    async with SessionLocal() as db:
        kb = await db.scalar(
            select(KnowledgeBase)
            .where(KnowledgeBase.name == args.kb)
            .order_by(KnowledgeBase.created_at)
        )
        if kb is None:
            print(f"找不到知识库：{args.kb}")
            return 1

        if args.type == "github":
            config = {"repo": args.repo, "path": args.path, "max_files": args.limit}
        elif args.type == "rss":
            config = {"feed_url": args.url, "max_items": args.limit}
        elif args.type == "sitemap":
            config = {"sitemap_url": args.url, "max_pages": args.limit}
        else:
            config = {
                "start_urls": [args.url],
                "max_pages": args.limit,
                "max_depth": 1,
                "request_delay_seconds": 1.2,
            }

        source = await source_service.create_source(
            db,
            kb,
            SourceCreate(
                name=args.name or f"演示数据源 · {args.type}",
                source_type=args.type,
                config=config,
                schedule_minutes=args.every,
            ),
        )
        await db.commit()
        interval = source.schedule_minutes or "—"
        print(f"已创建数据源 {source.name}（{source.source_type}，每 {interval} 分钟）")
        if args.sync:
            run_row = await source_service.create_run(db, source, trigger="manual")
            await db.commit()
            await source_service.execute_run(run_row.id)
            print("同步完成，统计：", run_row.stats)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a demo data source")
    parser.add_argument("--kb", required=True, help="knowledge base name")
    parser.add_argument("--type", default="web", choices=["web", "sitemap", "rss", "github"])
    parser.add_argument("--url", default="https://fastapi.tiangolo.com/tutorial/")
    parser.add_argument("--repo", default="tiangolo/fastapi")
    parser.add_argument("--path", default="docs/en/docs/tutorial")
    parser.add_argument("--name", default="")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument(
        "--every", type=int, default=0, help="sync interval in minutes (0 = manual)"
    )
    parser.add_argument("--sync", action="store_true", help="run one sync immediately")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
