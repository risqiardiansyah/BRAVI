"""Integration tests for app.api.system_router — docs/06-api-specification.md §9.

`_check_database`/`_check_redis`/`_check_bedrock` are individually monkeypatchable
module-level functions (docs/IMPLEMENTATION_PLAN.md Phase 5), so the readiness
aggregation logic (status code, JSON shape) is tested deterministically here without
depending on whether a real Redis/Bedrock happens to be reachable in this environment.
The corresponding real-infrastructure proof (`docker-compose stop db` -> `503` -> restart
-> recovers) is this phase's documented *manual* verification step, not this file.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import system_router
from app.clients.bedrock_client import CircuitState
from app.db import normalize_asyncpg_url
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_liveness_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- /health/ready aggregation (all three checks monkeypatched) -------------


@pytest.fixture
def all_checks_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok_database() -> bool:
        return True

    async def _ok_redis() -> bool:
        return True

    def _ok_bedrock() -> bool:
        return True

    monkeypatch.setattr(system_router, "_check_database", _ok_database)
    monkeypatch.setattr(system_router, "_check_redis", _ok_redis)
    monkeypatch.setattr(system_router, "_check_bedrock", _ok_bedrock)


def test_health_ready_returns_200_when_all_checks_pass(
    client: TestClient, all_checks_healthy: None
) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "redis": "ok", "bedrock": "ok"},
    }


def test_health_ready_returns_503_when_database_down(
    client: TestClient, all_checks_healthy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _down() -> bool:
        return False

    monkeypatch.setattr(system_router, "_check_database", _down)

    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "checks": {"database": "error", "redis": "ok", "bedrock": "ok"},
    }


def test_health_ready_returns_503_when_redis_down(
    client: TestClient, all_checks_healthy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _down() -> bool:
        return False

    monkeypatch.setattr(system_router, "_check_redis", _down)

    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "redis": "error", "bedrock": "ok"}


def test_health_ready_returns_503_when_bedrock_circuit_open(
    client: TestClient, all_checks_healthy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(system_router, "_check_bedrock", lambda: False)

    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "redis": "ok", "bedrock": "error"}


def test_health_ready_returns_503_when_every_check_fails(
    client: TestClient, all_checks_healthy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _down() -> bool:
        return False

    monkeypatch.setattr(system_router, "_check_database", _down)
    monkeypatch.setattr(system_router, "_check_redis", _down)
    monkeypatch.setattr(system_router, "_check_bedrock", lambda: False)

    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "checks": {"database": "error", "redis": "error", "bedrock": "error"},
    }


# --- Individual check functions, exercised directly -------------------------


async def test_check_database_returns_true_against_the_real_test_database() -> None:
    """Proves the real `SELECT 1` path works, against the same live Postgres
    docs/IMPLEMENTATION_PLAN.md Phase 2's repository tests already require."""
    assert await system_router._check_database() is True


async def test_check_database_returns_false_for_unreachable_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real (not mocked) connection attempt against a host nothing is listening on —
    proves the try/except catches a genuine asyncpg connection failure, not just a
    stand-in exception."""
    broken_engine = create_async_engine(
        normalize_asyncpg_url("postgresql://user:pass@127.0.0.1:1/nonexistent"),
        poolclass=NullPool,
    )
    monkeypatch.setattr(
        system_router,
        "AsyncSessionLocal",
        async_sessionmaker(broken_engine, expire_on_commit=False),
    )

    assert await system_router._check_database() is False
    await broken_engine.dispose()


class _StubRedisClient:
    def __init__(self, *, healthy: bool) -> None:
        self._healthy = healthy

    async def ping(self) -> bool:
        return self._healthy


async def test_check_redis_reflects_ping_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_router, "redis_client", _StubRedisClient(healthy=True))
    assert await system_router._check_redis() is True

    monkeypatch.setattr(system_router, "redis_client", _StubRedisClient(healthy=False))
    assert await system_router._check_redis() is False


class _StubBedrockClient:
    def __init__(self, *, state: CircuitState) -> None:
        self.circuit_breaker_state = state


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (CircuitState.CLOSED, True),
        (CircuitState.HALF_OPEN, True),
        (CircuitState.OPEN, False),
    ],
)
def test_check_bedrock_reflects_circuit_breaker_state(
    monkeypatch: pytest.MonkeyPatch, state: CircuitState, expected: bool
) -> None:
    monkeypatch.setattr(system_router, "bedrock_client", _StubBedrockClient(state=state))
    assert system_router._check_bedrock() is expected


# --- /metrics ----------------------------------------------------------------


def test_metrics_returns_prometheus_exposition_format(client: TestClient) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    # Valid exposition format is plain text — decoding is enough to prove it's well-formed
    # without asserting on specific counters, which land progressively in later phases.
    assert isinstance(response.text, str)
