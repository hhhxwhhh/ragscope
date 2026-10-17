"""Sentence-embedding wrapper.

The model is loaded lazily and kept in a process-wide singleton: loading costs
hundreds of MB and a few seconds, so it must never happen per request. Inference
is CPU/GPU bound and therefore always dispatched to a worker thread.
"""

import logging
import threading
from functools import partial
from typing import TYPE_CHECKING

import anyio

from app.core.config import get_settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# BGE's Chinese models are trained with an instruction prefix for queries.
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

_model: "SentenceTransformer | None" = None
_model_lock = threading.Lock()


def _get_model() -> "SentenceTransformer":
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                # Imported lazily: torch + sentence-transformers is a ~2 GB
                # dependency that CI and unit tests never need.
                from sentence_transformers import SentenceTransformer

                settings = get_settings()
                kwargs = {}
                if settings.embedding_device:
                    kwargs["device"] = settings.embedding_device
                logger.info("loading embedding model %s", settings.embedding_model)
                _model = SentenceTransformer(settings.embedding_model, **kwargs)
                logger.info("embedding model ready")
    return _model


def embed_texts(texts: list[str], *, is_query: bool = False) -> list[list[float]]:
    """Embed documents or a query. Vectors are L2-normalised for cosine search."""
    if not texts:
        return []

    settings = get_settings()
    payload = [f"{QUERY_INSTRUCTION}{text}" for text in texts] if is_query else texts

    model = _get_model()
    vectors = model.encode(
        payload,
        batch_size=settings.embedding_batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return [vector.tolist() for vector in vectors]


async def embed_texts_async(texts: list[str], *, is_query: bool = False) -> list[list[float]]:
    return await anyio.to_thread.run_sync(partial(embed_texts, texts, is_query=is_query))


async def embed_query_async(query: str) -> list[float]:
    vectors = await embed_texts_async([query], is_query=True)
    return vectors[0]
