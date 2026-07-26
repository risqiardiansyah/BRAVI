"""System / operational endpoints — docs/06-api-specification.md §9, docs/09-observability.md §8.

Not persona-specific; used by the container orchestrator, load balancer, and monitoring
stack rather than end users. No rate limiting, no session resolution.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.clients.bedrock_client import CircuitState, bedrock_client
from app.clients.redis_client import redis_client
from app.db import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — process is up, no dependency checks (docs/06-api-specification.md §9.1)."""
    return {"status": "ok"}


async def _check_database() -> bool:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("Database readiness check failed", exc_info=True)
        return False


async def _check_redis() -> bool:
    return await redis_client.ping()


def _check_bedrock() -> bool:
    """ "Lightweight, not a real inference call" (docs/09-observability.md §8): reflects
    `bedrock_client`'s own circuit-breaker signal (`CLOSED`/`HALF_OPEN` => reachable) rather
    than issuing a live AWS API call on every readiness poll — a real call would need IAM
    permissions beyond the documented least-privilege `bedrock:InvokeModel` scope
    (docs/08-security.md §5) and would cost real money if polled frequently.
    """
    return bedrock_client.circuit_breaker_state is not CircuitState.OPEN


@router.get("/health/ready")
async def health_ready(response: Response) -> dict[str, object]:
    """Readiness probe — DB, Redis, Bedrock reachability (docs/06-api-specification.md §9.2).

    `200` when every check passes, `503` (failing checks reported as `"error"`) otherwise —
    the orchestrator must not route traffic while this returns `503` (docs/10-deployment.md §4).
    """
    database_ok = await _check_database()
    redis_ok = await _check_redis()
    bedrock_ok = _check_bedrock()
    all_ok = database_ok and redis_ok and bedrock_ok

    response.status_code = 200 if all_ok else 503
    return {
        "status": "ready" if all_ok else "error",
        "checks": {
            "database": "ok" if database_ok else "error",
            "redis": "ok" if redis_ok else "error",
            "bedrock": "ok" if bedrock_ok else "error",
        },
    }


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus exposition format (docs/06-api-specification.md §9.3,
    docs/09-observability.md §5).

    Counters/histograms/gauges are registered progressively as later phases add each
    AI-involved code path (full set wired by Phase 13) — this endpoint only guarantees
    valid exposition format now, per this phase's Definition of Done.
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
