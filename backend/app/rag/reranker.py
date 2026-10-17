"""Cross-encoder reranker with a hard timeout and graceful fallback.

The reranker is optional in the retrieval chain: if the model is unavailable or
inference exceeds the budget, the caller keeps the RRF order and records the
degradation in the trace instead of failing the request.
"""

import asyncio
import logging
import math
import threading
from dataclasses import dataclass

import anyio
from sentence_transformers import CrossEncoder

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_model: CrossEncoder | None = None
_model_lock = threading.Lock()


@dataclass
class RerankResult:
    scores: list[float]
    applied: bool
    error: str | None = None


def _get_model() -> CrossEncoder:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                settings = get_settings()
                logger.info("loading reranker model %s", settings.reranker_model)
                _model = CrossEncoder(settings.reranker_model)
                logger.info("reranker model ready")
    return _model


def _predict(model: CrossEncoder, pairs: list[tuple[str, str]]) -> list[float]:
    raw = model.predict(pairs, batch_size=16, show_progress_bar=False)
    return [1.0 / (1.0 + math.exp(-float(score))) for score in raw]


async def rerank(
    query: str,
    passages: list[str],
    *,
    timeout_seconds: float,
) -> RerankResult:
    if not passages:
        return RerankResult(scores=[], applied=False)

    try:
        model = await anyio.to_thread.run_sync(_get_model)
        scores = await asyncio.wait_for(
            anyio.to_thread.run_sync(_predict, model, [(query, passage) for passage in passages]),
            timeout=timeout_seconds,
        )
        return RerankResult(scores=scores, applied=True)
    except TimeoutError:
        logger.warning("reranker timed out after %.1fs; falling back to RRF order", timeout_seconds)
        return RerankResult(scores=[], applied=False, error="rerank_timeout")
    except Exception as exc:
        logger.warning("reranker failed: %s; falling back to RRF order", exc)
        return RerankResult(scores=[], applied=False, error=f"rerank_error: {exc}"[:200])
