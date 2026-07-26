"""Redis-backed token-bucket rate limiter — docs/08-security.md §6, docs/11-coding-standard.md §13.

Applied to `/api/chat`, `/api/opr/chat`, `/api/opr/ingest` (docs/11-coding-standard.md §2).
Those routes don't exist yet (they land in Phases 7/9/10) — this module is complete and
tested against stub routes now (docs/IMPLEMENTATION_PLAN.md Phase 4); each later phase wires
`Depends(rate_limit_dependency(...))` into its own route once it exists.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping

from fastapi import Request
from fastapi.responses import JSONResponse

from app.clients.redis_client import RedisClient, redis_client
from app.config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "rate_limit"


class RateLimitExceededError(Exception):
    """Raised when a caller has exhausted its token bucket.

    A domain exception per docs/11-coding-standard.md §6, meant to be caught centrally by
    a FastAPI exception handler (`rate_limit_exception_handler` below) and mapped to
    `RATE_LIMITED`/429 (docs/22-error-handling.md §2) — not raised as an `HTTPException`
    directly, so every rate-limited endpoint gets the same response shape from one place.
    """

    def __init__(self, *, endpoint: str, identity: str) -> None:
        super().__init__(f"Rate limit exceeded for endpoint={endpoint!r} identity={identity!r}.")
        self.endpoint = endpoint
        self.identity = identity


async def rate_limit_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI exception handler for `RateLimitExceededError`.

    Register once `app/main.py` exists: `app.add_exception_handler(RateLimitExceededError,
    rate_limit_exception_handler)` (docs/IMPLEMENTATION_PLAN.md Phase 5+). Produces the
    standard error envelope (docs/11-coding-standard.md §6).
    """
    assert isinstance(exc, RateLimitExceededError)
    return JSONResponse(
        status_code=429,
        content={"error": {"code": "RATE_LIMITED", "message": str(exc)}},
    )


class TokenBucketRateLimiter:
    """Redis-backed token bucket, one independent bucket per `(endpoint, identity)` pair.

    Capacity is `RATE_LIMIT_BURST`; steady-state refill is `RATE_LIMIT_REQUESTS_PER_MINUTE`
    tokens/minute (docs/10-deployment.md §3) — the two are deliberately independent
    (docs/23-configuration.md §4: burst need not be >= the per-minute rate). Bucketing is
    per-endpoint as well as per-identity, matching `rate_limit_rejections_total` being
    labeled by `endpoint` (docs/09-observability.md §5) — a caller's budget on `/api/chat`
    is independent of its budget on `/api/opr/ingest`.
    """

    def __init__(self, client: RedisClient | None = None) -> None:
        self._client = client if client is not None else redis_client

    async def allow(self, *, endpoint: str, identity: str) -> bool:
        """Return `True` and consume one token if the caller may proceed, else `False`."""
        capacity = float(settings.RATE_LIMIT_BURST)
        refill_per_second = settings.RATE_LIMIT_REQUESTS_PER_MINUTE / 60.0
        now = time.time()
        key = f"{_KEY_PREFIX}:{endpoint}:{identity}"

        def _consume(current: Mapping[str, str]) -> tuple[Mapping[str, str], bool]:
            tokens = float(current["tokens"]) if "tokens" in current else capacity
            last_ts = float(current["ts"]) if "ts" in current else now
            elapsed = max(0.0, now - last_ts)
            tokens = min(capacity, tokens + elapsed * refill_per_second)
            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0
            return {"tokens": str(tokens), "ts": str(now)}, allowed

        # Long enough that a bucket outlives one full refill cycle (so a caller who
        # stops mid-window doesn't lose its accrued tokens early), short enough not
        # to accumulate idle keys forever — Redis is a cache here, not storage
        # (docs/08-security.md §6).
        ttl_seconds = int(capacity / refill_per_second) + 60 if refill_per_second > 0 else 86400

        return await self._client.transactional_update(
            key, update=_consume, ttl_seconds=ttl_seconds
        )

    async def enforce(self, *, endpoint: str, identity: str) -> None:
        """Raise `RateLimitExceededError` if `identity`'s bucket for `endpoint` is exhausted."""
        if not await self.allow(endpoint=endpoint, identity=identity):
            logger.info("Rate limit exceeded (endpoint=%s, identity=%s)", endpoint, identity)
            raise RateLimitExceededError(endpoint=endpoint, identity=identity)


rate_limiter = TokenBucketRateLimiter()


def resolve_identity(request: Request, *, user_id: str | None) -> str:
    """`user_id`-scoped identity when known, else the caller's IP.

    `/api/chat`/`/api/opr/chat` requests carry `user_id` in their JSON body
    (docs/06-api-specification.md §2/§5); `/api/opr/ingest`'s multipart body has no
    `user_id` field at all (docs/06-api-specification.md §6) — IP is the only identity
    available there, which is why the fallback exists rather than being optional.
    """
    if user_id:
        return f"user:{user_id}"
    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"


async def _extract_json_user_id(request: Request) -> str | None:
    """Best-effort `user_id` extraction from a JSON request body.

    Starlette caches the raw body bytes on the first `Request.body()`/`.json()` call and
    replays them for subsequent reads — including the route handler's own request-model
    parsing — so this read never races with or consumes the body out from under the
    endpoint itself.
    """
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("application/json"):
        return None
    try:
        body = await request.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        raw = body.get("user_id")
        if isinstance(raw, str) and raw:
            return raw
    return None


def rate_limit_dependency(endpoint: str) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency factory: `Depends(rate_limit_dependency("/api/chat"))`.

    Resolves the caller's identity (`user_id` if present in a JSON body, else IP) and
    enforces that endpoint's token bucket, raising `RateLimitExceededError` on exhaustion
    for the central handler above to catch.
    """

    async def _dependency(request: Request) -> None:
        user_id = await _extract_json_user_id(request)
        identity = resolve_identity(request, user_id=user_id)
        await rate_limiter.enforce(endpoint=endpoint, identity=identity)

    return _dependency
