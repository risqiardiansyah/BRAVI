"""`stream_with_keepalive` — docs/06-api-specification.md §0's keepalive requirement,
docs/12-testing-strategy.md §3 ("for an artificially slow mocked generation call... a
keepalive comment ping is emitted at SSE_KEEPALIVE_INTERVAL_SECONDS").
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.utils.sse import stream_with_keepalive


async def _slow_source(delays: list[float]) -> AsyncIterator[int]:
    for index, delay in enumerate(delays):
        await asyncio.sleep(delay)
        yield index


async def test_keepalive_emitted_during_a_slow_gap_without_losing_items() -> None:
    # One item arrives quickly, then a gap longer than the interval before the next.
    results = [
        item
        async for item in stream_with_keepalive(
            _slow_source([0.0, 0.25, 0.0]), interval_seconds=0.05
        )
    ]

    # Every real item must still show up, in order, none lost to the keepalive timer.
    assert [r for r in results if r is not None] == [0, 1, 2]
    # At least one keepalive (`None`) fired during the 0.25s gap at a 0.05s interval.
    assert results.count(None) >= 2


async def test_no_keepalive_when_items_arrive_faster_than_the_interval() -> None:
    results = [
        item
        async for item in stream_with_keepalive(_slow_source([0.0, 0.0, 0.0]), interval_seconds=1.0)
    ]
    assert results == [0, 1, 2]
