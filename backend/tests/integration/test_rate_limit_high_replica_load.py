"""Rate limiter re-verified at a higher simulated replica count under real concurrent
load — docs/IMPLEMENTATION_PLAN.md Phase 13 task 5 ("Rate limiter re-verified across
multiple simulated replicas under real load, not just the `fakeredis` unit test from
Phase 4").

`tests/integration/test_rate_limit_multi_instance.py` (Phase 4) proves correctness with
two replicas alternating *sequentially* — no two requests are ever actually in flight
against Redis at the same moment, so the `WATCH`/`MULTI`/`EXEC` optimistic-concurrency
retry path in `app/clients/redis_client.py::transactional_update` (the part that matters
under real load, where multiple replicas race to update the same bucket key) is never
exercised. This file fires genuinely concurrent requests (`asyncio.gather`) from a much
higher replica count against one shared `fakeredis` server, so the token bucket must
still land on exactly the right count despite real `WatchError`-triggered retries.
"""

from __future__ import annotations

import asyncio

import fakeredis
import pytest

from app.clients.redis_client import RedisClient
from app.config import settings
from app.middleware import rate_limit as rate_limit_module
from app.middleware.rate_limit import TokenBucketRateLimiter

REPLICA_COUNT = 20


class _FrozenClock:
    """A fixed wall-clock time for the whole test — no refill accrues no matter how
    long the concurrent batch actually takes to run, so the assertion is deterministic
    regardless of host speed (mirrors `test_rate_limit_multi_instance.py`'s `_FakeClock`,
    simplified since this file never needs to advance time)."""

    def __init__(self, at: float = 1_000_000.0) -> None:
        self._at = at

    def time(self) -> float:
        return self._at


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> _FrozenClock:
    clock = _FrozenClock()
    monkeypatch.setattr(rate_limit_module, "time", clock)
    return clock


@pytest.fixture
def many_replica_limiters(
    monkeypatch: pytest.MonkeyPatch,
) -> list[TokenBucketRateLimiter]:
    """`REPLICA_COUNT` independent `TokenBucketRateLimiter`s, each with its own Redis
    connection object, all pointed at one shared in-memory `fakeredis` server — standing
    in for `REPLICA_COUNT` stateless API replicas behind a load balancer, all talking to
    one real shared Redis instance (docs/03-non-functional-requirements.md §2)."""
    monkeypatch.setattr(settings, "RATE_LIMIT_BURST", 10)
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS_PER_MINUTE", 60)

    shared_server = fakeredis.FakeServer()
    return [
        TokenBucketRateLimiter(
            RedisClient(fakeredis.FakeAsyncRedis(server=shared_server, decode_responses=True))
        )
        for _ in range(REPLICA_COUNT)
    ]


async def test_burst_capacity_enforced_exactly_under_concurrent_load_across_many_replicas(
    many_replica_limiters: list[TokenBucketRateLimiter],
    frozen_clock: _FrozenClock,
) -> None:
    # 3x more concurrent requests than replicas, for the SAME identity, all fired via
    # asyncio.gather (genuinely concurrent, not sequential) — every replica may race
    # another to update the one shared bucket key at the same instant.
    async def _attempt(replica: TokenBucketRateLimiter) -> bool:
        return await replica.allow(endpoint="/api/chat", identity="user:alice")

    tasks = [_attempt(many_replica_limiters[i % REPLICA_COUNT]) for i in range(REPLICA_COUNT * 3)]
    outcomes = await asyncio.gather(*tasks)

    # Despite real concurrent contention (and the WATCH-retry path it forces), the
    # combined total allowed across every replica must land exactly on the configured
    # burst capacity — never doubled, never under-counted.
    assert sum(outcomes) == settings.RATE_LIMIT_BURST


async def test_distinct_identities_remain_isolated_under_concurrent_load(
    many_replica_limiters: list[TokenBucketRateLimiter],
    frozen_clock: _FrozenClock,
) -> None:
    # One identity per replica, each hammered concurrently past its own burst capacity —
    # proves per-identity buckets don't bleed into each other even when many distinct
    # keys are being written concurrently against the same shared Redis server.
    async def _drain_one_identity(replica: TokenBucketRateLimiter, identity: str) -> list[bool]:
        return await asyncio.gather(
            *[
                replica.allow(endpoint="/api/chat", identity=identity)
                for _ in range(settings.RATE_LIMIT_BURST + 5)
            ]
        )

    results = await asyncio.gather(
        *[
            _drain_one_identity(many_replica_limiters[i], f"user:replica-{i}")
            for i in range(REPLICA_COUNT)
        ]
    )

    for outcomes in results:
        assert sum(outcomes) == settings.RATE_LIMIT_BURST
