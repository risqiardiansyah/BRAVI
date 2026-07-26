"""Two simulated app replicas sharing one Redis correctly share limiter state.

docs/03-non-functional-requirements.md §2: the API is stateless/horizontally scaled,
so an in-process limiter would only bound requests hitting one replica. This proves
the token bucket is enforced against the *combined* traffic from multiple replicas,
not per-process (docs/IMPLEMENTATION_PLAN.md Phase 4 Verification).
"""

from __future__ import annotations

import fakeredis
import pytest

from app.clients.redis_client import RedisClient
from app.config import settings
from app.middleware import rate_limit as rate_limit_module
from app.middleware.rate_limit import TokenBucketRateLimiter


class _FakeClock:
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
def two_replica_limiters(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TokenBucketRateLimiter, TokenBucketRateLimiter]:
    """Two `TokenBucketRateLimiter`s, each backed by its *own* Redis connection object,
    both pointed at one shared in-memory `fakeredis` server — standing in for two
    stateless API replicas talking to one real Redis instance."""
    monkeypatch.setattr(settings, "RATE_LIMIT_BURST", 5)
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS_PER_MINUTE", 60)

    shared_server = fakeredis.FakeServer()
    replica_a = TokenBucketRateLimiter(
        RedisClient(fakeredis.FakeAsyncRedis(server=shared_server, decode_responses=True))
    )
    replica_b = TokenBucketRateLimiter(
        RedisClient(fakeredis.FakeAsyncRedis(server=shared_server, decode_responses=True))
    )
    return replica_a, replica_b


async def test_bucket_shared_across_replicas_not_doubled(
    two_replica_limiters: tuple[TokenBucketRateLimiter, TokenBucketRateLimiter],
    clock: _FakeClock,
) -> None:
    replica_a, replica_b = two_replica_limiters

    # Alternate requests between the two replicas for the same caller. If state were
    # per-process (the bug this test guards against), each replica would independently
    # allow up to RATE_LIMIT_BURST=5 requests, for 10 total. Shared Redis state must
    # cap the combined total at 5.
    outcomes = []
    for i in range(10):
        replica = replica_a if i % 2 == 0 else replica_b
        outcomes.append(await replica.allow(endpoint="/api/chat", identity="user:alice"))

    assert outcomes == [True] * 5 + [False] * 5
    assert sum(outcomes) == settings.RATE_LIMIT_BURST


async def test_two_replicas_do_not_see_each_others_other_identities(
    two_replica_limiters: tuple[TokenBucketRateLimiter, TokenBucketRateLimiter],
    clock: _FakeClock,
) -> None:
    replica_a, replica_b = two_replica_limiters

    for _ in range(5):
        assert await replica_a.allow(endpoint="/api/chat", identity="user:alice") is True
    assert await replica_a.allow(endpoint="/api/chat", identity="user:alice") is False

    # A different identity, checked from the *other* replica, is unaffected.
    assert await replica_b.allow(endpoint="/api/chat", identity="user:bob") is True


async def test_replica_sees_refill_performed_by_the_other_replica(
    two_replica_limiters: tuple[TokenBucketRateLimiter, TokenBucketRateLimiter],
    clock: _FakeClock,
) -> None:
    replica_a, replica_b = two_replica_limiters  # burst=5, 60/min == 1 token/sec

    for _ in range(5):
        assert await replica_a.allow(endpoint="/api/chat", identity="user:alice") is True
    assert await replica_b.allow(endpoint="/api/chat", identity="user:alice") is False

    clock.advance(1.0)

    # Refill accrued while idle is visible to replica_b even though replica_a made
    # every prior request — proves the token state truly lives in shared Redis, not
    # in either process's memory.
    assert await replica_b.allow(endpoint="/api/chat", identity="user:alice") is True
