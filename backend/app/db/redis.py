from redis.asyncio import Redis

from app.core.config import get_settings

_client: Redis | None = None


def get_redis_client() -> Redis:
    """Return a process-wide async Redis client."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
