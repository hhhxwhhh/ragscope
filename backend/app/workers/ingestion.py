import logging
from uuid import UUID

from app.db.session import SessionLocal
from app.services.ingestion import ingest_document

logger = logging.getLogger(__name__)


async def run_ingestion(document_id: UUID) -> None:
    """Background entry point. Never reuse a request-scoped session here."""
    async with SessionLocal() as db:
        try:
            await ingest_document(db, document_id)
        except Exception:
            await db.rollback()
            logger.exception("unexpected ingestion error for %s", document_id)
