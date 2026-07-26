"""Redis client wrapper — docs/11-coding-standard.md §13.

The only module that constructs a `redis.asyncio` connection. Redis backs
exactly one thing in this system: the rate-limit token bucket in
`middleware/rate_limit.py` (docs/21-event-flow.md §2 — "nothing else is
ever coordinated through Redis"). This stays a thin connection/primitive
facade; the token-bucket math itself lives in `middleware/rate_limit.py`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TypeVar

import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Bounds the WATCH/MULTI/EXEC retry loop below — high contention on a single
# key (many concurrent requests for the same identity) causes WatchError
# retries, not an infinite loop; this is a sane ceiling, not a tuned constant.
_MAX_TRANSACTION_ATTEMPTS = 10


class RedisClient:
    """Thin wrapper around a single `redis.asyncio.Redis` connection pool.

    `client` may be injected directly (e.g. a `fakeredis` instance in tests)
    to avoid a real network connection — mirrors `BedrockClient`'s pattern of
    swapping the underlying SDK client for tests (docs/IMPLEMENTATION_PLAN.md
    Phase 3).
    """

    def __init__(self, client: redis.Redis | None = None) -> None:
        self._client = client

    def _get_client(self) -> redis.Redis:
        if self._client is None:
            if not settings.REDIS_URL:
                raise RuntimeError("REDIS_URL is not configured; cannot create the Redis client.")
            self._client = redis.from_url(  # type: ignore[no-untyped-call]
                settings.REDIS_URL, decode_responses=True
            )
        return self._client

    async def transactional_update(
        self,
        key: str,
        *,
        update: Callable[[Mapping[str, str]], tuple[Mapping[str, str], _T]],
        ttl_seconds: int,
    ) -> _T:
        """Atomically read `key`'s hash, compute `update(current_fields)`, write the
        returned fields back with `ttl_seconds`, and return `update`'s second return value.

        Uses `WATCH`/`MULTI`/`EXEC` optimistic concurrency (retrying on conflicting
        concurrent writers) rather than a server-side Lua script — `EVAL` support in
        `fakeredis` needs the `lupa` C-extension, and this avoids that dependency
        entirely (docs/11-coding-standard.md's minimal-dependency-footprint rule)
        while still being safe under concurrent access from multiple app replicas.
        """
        client = self._get_client()
        for _ in range(_MAX_TRANSACTION_ATTEMPTS):
            async with client.pipeline(transaction=True) as pipe:
                await pipe.watch(key)
                current = await pipe.hgetall(key)  # type: ignore[misc]
                new_fields, result = update(current)
                pipe.multi()  # type: ignore[no-untyped-call]
                pipe.hset(key, mapping=dict(new_fields))
                pipe.expire(key, ttl_seconds)
                try:
                    await pipe.execute()
                except redis.WatchError:
                    continue
                return result
        raise RuntimeError(
            f"Could not update Redis key {key!r} after {_MAX_TRANSACTION_ATTEMPTS} attempts "
            "(sustained concurrent contention on the same key)."
        )

    async def ping(self) -> bool:
        """Reachability check for `GET /health/ready` (docs/IMPLEMENTATION_PLAN.md Phase 5)."""
        try:
            client = self._get_client()
            return bool(await client.ping())
        except Exception:
            logger.warning("Redis ping failed", exc_info=True)
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


redis_client = RedisClient()
