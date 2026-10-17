"""One-command demo seed: demo user, KB, five documents, eval dataset.

Usage:
    PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/seed_demo.py

Idempotent: existing user/KB/documents are reused, documents that never reached
``indexed`` are re-uploaded, and the eval dataset is recreated from scratch.
Embedding five Markdown files is the slow part (the model is loaded once).
"""

from app.core.env import configure_env

configure_env()

import asyncio  # noqa: E402
import io  # noqa: E402
import os  # noqa: E402
from pathlib import Path  # noqa: E402

from demo_cases import KB_NAME, seed_eval_dataset  # noqa: E402
from fastapi import UploadFile  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.config import ROOT_DIR, get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.document import Document, DocumentStatus  # noqa: E402
from app.models.knowledge_base import KnowledgeBase  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.kb import KbCreate  # noqa: E402
from app.services import auth_service, document_service, ingestion, kb_service  # noqa: E402

DEMO_EMAIL = "demo@ragscope.dev"
DEMO_PASSWORD = "Passw0rd!123"
DEMO_DISPLAY_NAME = "RAGScope Demo"
# In Docker the repo-root corpus is mounted at /app/data; locally it is next to
# the backend directory.
DOCS_DIR = Path(os.environ.get("DEMO_DOCS_DIR") or ROOT_DIR / "data")


async def _ensure_user(db) -> User:
    user = await auth_service.get_user_by_email(db, DEMO_EMAIL)
    if user is not None:
        print(f"[skip] 演示用户已存在：{DEMO_EMAIL}")
        return user

    user = await auth_service.register_user(
        db,
        email=DEMO_EMAIL,
        password=DEMO_PASSWORD,
        display_name=DEMO_DISPLAY_NAME,
    )
    await db.commit()
    print(f"[new] 演示用户：{DEMO_EMAIL} / {DEMO_PASSWORD}")
    return user


async def _ensure_kb(db, user: User) -> KnowledgeBase:
    kb = await db.scalar(
        select(KnowledgeBase)
        .where(KnowledgeBase.owner_id == user.id, KnowledgeBase.name == KB_NAME)
        .order_by(KnowledgeBase.created_at)
    )
    if kb is not None:
        print(f"[skip] 知识库已存在：{KB_NAME}")
        return kb

    kb = await kb_service.create_kb(
        db,
        user.id,
        KbCreate(
            name=KB_NAME,
            description="星河科技公司介绍、X1/X2 产品手册、售后政策与常见问题。",
            chunk_size=400,
            chunk_overlap=60,
        ),
    )
    await db.commit()
    print(f"[new] 知识库：{KB_NAME}")
    return kb


async def _ensure_documents(db, kb: KnowledgeBase) -> None:
    if not DOCS_DIR.is_dir():
        raise SystemExit(f"找不到演示语料目录：{DOCS_DIR}")

    for path in sorted(DOCS_DIR.glob("*.md")):
        existing = await db.scalar(
            select(Document).where(Document.kb_id == kb.id, Document.filename == path.name)
        )
        if existing is not None and existing.status == DocumentStatus.INDEXED:
            print(f"[skip] 已入库：{path.name}（{existing.chunk_count} chunks）")
            continue

        if existing is not None:
            # A previous attempt failed or was interrupted: clear it and retry.
            await document_service.delete_document(db, existing)
            await db.commit()

        upload = UploadFile(file=io.BytesIO(path.read_bytes()), filename=path.name)
        document = await document_service.create_document(db, kb, upload)
        await db.commit()

        await ingestion.ingest_document(db, document.id)
        if document.status != DocumentStatus.INDEXED:
            raise SystemExit(
                f"入库失败：{path.name}（{document.error_stage}: {document.error_message}）"
            )
        print(f"[new] 已入库：{path.name}（{document.chunk_count} chunks）")


async def seed() -> None:
    settings = get_settings()
    if not settings.deepseek_api_key:
        print("[warn] 未配置 DEEPSEEK_API_KEY：检索与评测可用，聊天生成会失败")

    async with SessionLocal() as db:
        user = await _ensure_user(db)
        kb = await _ensure_kb(db, user)
        await _ensure_documents(db, kb)
        dataset, cases = await seed_eval_dataset(db, kb)

        answerable = sum(1 for case in cases if case.answerable)
        print(
            f"[new] 评测数据集：{dataset.name}，"
            f"{len(cases)} 条（可答 {answerable} / 不可答 {len(cases) - answerable}）"
        )

    print()
    print("演示环境就绪：")
    print(f"  账号：{DEMO_EMAIL}")
    print(f"  密码：{DEMO_PASSWORD}")
    print(f"  知识库：{KB_NAME}")
    print("  下一步：登录 -> 打开知识库 -> 检索调试 / 评测台运行两组配置")


if __name__ == "__main__":
    asyncio.run(seed())
