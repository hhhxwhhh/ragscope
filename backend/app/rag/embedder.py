"""LangChain HuggingFace embedding wrapper for the BGE Chinese model.

The model is loaded lazily and kept in a process-wide singleton: loading costs
hundreds of MB and a few seconds, so it must never happen per request.
Inference is CPU/GPU bound and therefore always dispatched to a worker thread.

``HuggingFaceEmbeddings`` loads the SentenceTransformer in its constructor, so
the import and construction stay lazy. CI installs only ``.[dev]`` and never
touches this module's model path.
"""

import logging
import threading
from typing import TYPE_CHECKING

import anyio

from app.core.config import get_settings

if TYPE_CHECKING:
    from langchain_huggingface import HuggingFaceEmbeddings

logger = logging.getLogger(__name__)

# BGE's Chinese models are trained with an instruction prefix for queries.
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

_embeddings: "HuggingFaceEmbeddings | None" = None
_embeddings_lock = threading.Lock()


def _get_embeddings() -> "HuggingFaceEmbeddings":
    global _embeddings
    if _embeddings is None:
        with _embeddings_lock:
            if _embeddings is None:
                from langchain_huggingface import HuggingFaceEmbeddings

                settings = get_settings()
                model_kwargs: dict[str, object] = {}
                if settings.embedding_device:
                    model_kwargs["device"] = settings.embedding_device
                logger.info("loading embedding model %s", settings.embedding_model)
                _embeddings = HuggingFaceEmbeddings(
                    model_name=settings.embedding_model,
                    model_kwargs=model_kwargs,
                    # Same settings as the previous hand-written wrapper:
                    # cosine search needs L2-normalised vectors.
                    encode_kwargs={
                        "normalize_embeddings": True,
                        "batch_size": settings.embedding_batch_size,
                    },
                    show_progress=False,
                )
                logger.info("embedding model ready")
    return _embeddings


def embed_texts(texts: list[str], *, is_query: bool = False) -> list[list[float]]:
    """Embed documents or queries. Query vectors get the BGE instruction prefix."""
    if not texts:
        return []

    payload = [f"{QUERY_INSTRUCTION}{text}" for text in texts] if is_query else texts
    return _get_embeddings().embed_documents(payload)


async def embed_texts_async(texts: list[str], *, is_query: bool = False) -> list[list[float]]:
    if not texts:
        return []

    payload = [f"{QUERY_INSTRUCTION}{text}" for text in texts] if is_query else texts
    # Model loading is blocking; keep it off the event loop. Encoding itself is
    # already executed in LangChain's thread pool.
    embeddings = await anyio.to_thread.run_sync(_get_embeddings)
    return await embeddings.aembed_documents(payload)


async def embed_query_async(query: str) -> list[float]:
    vectors = await embed_texts_async([query], is_query=True)
    return vectors[0]
