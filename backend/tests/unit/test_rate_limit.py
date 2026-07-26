"""Unit tests for app.middleware.rate_limit — docs/08-security.md §6.

Token-bucket math and identity resolution run against `fakeredis` (no network,
no real Redis needed — docs/IMPLEMENTATION_PLAN.md Phase 4 Verification).
"""

from __future__ import annotations

from collections.abc import Callable

import fakeredis
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.clients.redis_client import RedisClient
from app.config import settings
from app.middleware import rate_limit as rate_limit_module
from app.middleware.rate_limit import (
    RateLimitExceededError,
    TokenBucketRateLimiter,
    rate_limit_dependency,
    rate_limit_exception_handler,
    resolve_identity,
)


class _FakeClock:
    """Deterministic stand-in for `time.time()` so refill math never depends on
    wall-clock timing/sleeps in tests."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = start

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    fake = _FakeClock()
    monkeypatch.setattr(rate_limit_module, "time", fake)
    return fake


@pytest.fixture
def make_limiter(monkeypatch: pytest.MonkeyPatch) -> Callable[..., TokenBucketRateLimiter]:
    def _make(
        *, burst: int = 3, per_minute: int = 60, client: RedisClient | None = None
    ) -> TokenBucketRateLimiter:
        monkeypatch.setattr(settings, "RATE_LIMIT_BURST", burst)
        monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS_PER_MINUTE", per_minute)
        redis_client = client or RedisClient(fakeredis.FakeAsyncRedis(decode_responses=True))
        return TokenBucketRateLimiter(redis_client)

    return _make


# --- Token-bucket math -------------------------------------------------------


async def test_allows_up_to_burst_capacity_then_denies(
    make_limiter: Callable[..., TokenBucketRateLimiter], clock: _FakeClock
) -> None:
    limiter = make_limiter(burst=3, per_minute=60)

    results = [await limiter.allow(endpoint="/api/chat", identity="user:alice") for _ in range(4)]

    assert results == [True, True, True, False]


async def test_refills_over_time_at_configured_rate(
    make_limiter: Callable[..., TokenBucketRateLimiter], clock: _FakeClock
) -> None:
    # 60 requests/minute == 1 token/second.
    limiter = make_limiter(burst=1, per_minute=60)

    assert await limiter.allow(endpoint="/api/chat", identity="user:alice") is True
    assert await limiter.allow(endpoint="/api/chat", identity="user:alice") is False

    clock.advance(1.0)

    assert await limiter.allow(endpoint="/api/chat", identity="user:alice") is True


async def test_refill_never_exceeds_capacity(
    make_limiter: Callable[..., TokenBucketRateLimiter], clock: _FakeClock
) -> None:
    limiter = make_limiter(burst=2, per_minute=60)
    await limiter.allow(endpoint="/api/chat", identity="user:alice")

    clock.advance(3600.0)  # a long idle period must not accrue unbounded tokens

    results = [await limiter.allow(endpoint="/api/chat", identity="user:alice") for _ in range(3)]
    assert results == [True, True, False]


async def test_burst_independent_of_per_minute_rate(
    make_limiter: Callable[..., TokenBucketRateLimiter], clock: _FakeClock
) -> None:
    """docs/23-configuration.md §4: RATE_LIMIT_BURST need not be >= the per-minute rate."""
    limiter = make_limiter(burst=10, per_minute=1)

    results = [await limiter.allow(endpoint="/api/chat", identity="user:alice") for _ in range(10)]
    assert results == [True] * 10
    assert await limiter.allow(endpoint="/api/chat", identity="user:alice") is False


# --- Isolation ---------------------------------------------------------------


async def test_isolated_per_identity(
    make_limiter: Callable[..., TokenBucketRateLimiter], clock: _FakeClock
) -> None:
    limiter = make_limiter(burst=1, per_minute=60)

    assert await limiter.allow(endpoint="/api/chat", identity="user:alice") is True
    assert await limiter.allow(endpoint="/api/chat", identity="user:alice") is False
    # A different identity has its own untouched bucket.
    assert await limiter.allow(endpoint="/api/chat", identity="user:bob") is True
    assert await limiter.allow(endpoint="/api/chat", identity="ip:127.0.0.1") is True


async def test_isolated_per_endpoint(
    make_limiter: Callable[..., TokenBucketRateLimiter], clock: _FakeClock
) -> None:
    limiter = make_limiter(burst=1, per_minute=60)

    assert await limiter.allow(endpoint="/api/chat", identity="user:alice") is True
    assert await limiter.allow(endpoint="/api/chat", identity="user:alice") is False
    # Same identity, different endpoint budget.
    assert await limiter.allow(endpoint="/api/opr/ingest", identity="user:alice") is True


# --- enforce() / RateLimitExceededError -------------------------------------


async def test_enforce_raises_with_endpoint_and_identity(
    make_limiter: Callable[..., TokenBucketRateLimiter], clock: _FakeClock
) -> None:
    limiter = make_limiter(burst=1, per_minute=60)
    await limiter.enforce(endpoint="/api/chat", identity="user:alice")

    with pytest.raises(RateLimitExceededError) as exc_info:
        await limiter.enforce(endpoint="/api/chat", identity="user:alice")

    assert exc_info.value.endpoint == "/api/chat"
    assert exc_info.value.identity == "user:alice"


# --- resolve_identity() ------------------------------------------------------


def test_resolve_identity_prefers_user_id() -> None:
    scope = {"type": "http", "client": ("203.0.113.5", 12345), "headers": []}
    request = Request(scope)
    assert resolve_identity(request, user_id="alice") == "user:alice"


def test_resolve_identity_falls_back_to_ip_when_no_user_id() -> None:
    scope = {"type": "http", "client": ("203.0.113.5", 12345), "headers": []}
    request = Request(scope)
    assert resolve_identity(request, user_id=None) == "ip:203.0.113.5"


# --- _extract_json_user_id() -------------------------------------------------


async def test_extract_json_user_id_ignores_non_json_content_type() -> None:
    scope = {
        "type": "http",
        "client": ("127.0.0.1", 1234),
        "headers": [(b"content-type", b"multipart/form-data; boundary=x")],
    }
    request = Request(scope)
    assert await rate_limit_module._extract_json_user_id(request) is None


async def test_extract_json_user_id_returns_none_for_malformed_json() -> None:
    scope = {
        "type": "http",
        "client": ("127.0.0.1", 1234),
        "headers": [(b"content-type", b"application/json")],
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"not-json", "more_body": False}

    request = Request(scope, receive)
    assert await rate_limit_module._extract_json_user_id(request) is None


# --- FastAPI dependency wiring against stub routes ---------------------------


@pytest.fixture
def stub_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """A throwaway FastAPI app standing in for the not-yet-built `/api/chat` and
    `/api/opr/ingest` routes (docs/IMPLEMENTATION_PLAN.md Phase 4: "wired against
    stub routes if the real routes don't exist yet")."""
    monkeypatch.setattr(settings, "RATE_LIMIT_BURST", 2)
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS_PER_MINUTE", 60)

    shared_client = RedisClient(fakeredis.FakeAsyncRedis(decode_responses=True))
    monkeypatch.setattr(rate_limit_module, "rate_limiter", TokenBucketRateLimiter(shared_client))

    app = FastAPI()
    app.add_exception_handler(RateLimitExceededError, rate_limit_exception_handler)

    @app.post("/stub/chat", dependencies=[Depends(rate_limit_dependency("/stub/chat"))])
    async def stub_chat(request: Request) -> dict[str, object]:
        body = await request.json()
        return {"question_echo": body["question"]}

    @app.post("/stub/ingest", dependencies=[Depends(rate_limit_dependency("/stub/ingest"))])
    async def stub_ingest() -> dict[str, str]:
        return {"status": "queued"}

    return app


def test_dependency_allows_then_429s_on_exhaustion(stub_app: FastAPI) -> None:
    client = TestClient(stub_app)
    payload = {"question": "hello", "user_id": "alice"}

    assert client.post("/stub/chat", json=payload).status_code == 200
    assert client.post("/stub/chat", json=payload).status_code == 200

    response = client.post("/stub/chat", json=payload)
    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": "RATE_LIMITED",
            "message": "Rate limit exceeded for endpoint='/stub/chat' identity='user:alice'.",
        }
    }


def test_dependency_reads_user_id_without_disturbing_route_body_parsing(
    stub_app: FastAPI,
) -> None:
    """The dependency's own `request.json()` read must not prevent the route handler
    from independently reading the same body afterward."""
    client = TestClient(stub_app)

    response = client.post("/stub/chat", json={"question": "apa itu BRAVI?", "user_id": "alice"})

    assert response.status_code == 200
    assert response.json() == {"question_echo": "apa itu BRAVI?"}


def test_dependency_isolates_different_user_ids(stub_app: FastAPI) -> None:
    client = TestClient(stub_app)

    for _ in range(2):
        assert (
            client.post("/stub/chat", json={"question": "q", "user_id": "alice"}).status_code == 200
        )
    assert client.post("/stub/chat", json={"question": "q", "user_id": "alice"}).status_code == 429

    # A different user_id is unaffected by alice's exhausted bucket.
    assert client.post("/stub/chat", json={"question": "q", "user_id": "bob"}).status_code == 200


def test_dependency_falls_back_to_ip_for_no_json_body(stub_app: FastAPI) -> None:
    """Stands in for `/api/opr/ingest`, whose multipart request has no `user_id` field
    at all (docs/06-api-specification.md §6)."""
    client = TestClient(stub_app)

    assert client.post("/stub/ingest").status_code == 200
    assert client.post("/stub/ingest").status_code == 200
    assert client.post("/stub/ingest").status_code == 429
