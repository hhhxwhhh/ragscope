"""Redis-backed fixed-window rate limiting.

Why fixed window: it is one `INCR` + one `EXPIRE`, atomic inside a single
`MULTI`, and adequate for protecting an internal API. A sliding window or token
bucket gives smoother bursts at the cost of more Redis round trips; the trade-off
belongs in a gateway when it matters, not in every handler.

Failure policy is **open**: if Redis is down, requests are allowed. A rate limiter
that takes the API down together with its own dependency is worse than no rate
limiter, and the outage will already be visible in `/metrics`.
"""

import logging
import time
from dataclasses import dataclass

from app.core.errors import RateLimitedError
from app.db.redis import get_redis_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_after: int


async def hit(key: str, limit: int, window_seconds: int) -> RateLimitResult:
    """Count one hit for ``key`` inside the current window."""
    redis = get_redis_client()
    now = int(time.time())
    bucket = now // window_seconds
    redis_key = f"ratelimit:{key}:{bucket}"
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds + 1)
            count, _ = await pipe.execute()
    except Exception:
        logger.warning("rate limiter unavailable, allowing request key=%s", key)
        return RateLimitResult(allowed=True, remaining=limit, reset_after=0)

    count = int(count or 0)
    reset_after = window_seconds - (now % window_seconds)
    return RateLimitResult(
        allowed=count <= limit,
        remaining=max(0, limit - count),
        reset_after=reset_after,
    )


async def enforce(key: str, limit: int, window_seconds: int = 60) -> RateLimitResult:
    result = await hit(key, limit, window_seconds)
    if not result.allowed:
        raise RateLimitedError(f"请求过于频繁，请 {result.reset_after} 秒后重试")
    return result
