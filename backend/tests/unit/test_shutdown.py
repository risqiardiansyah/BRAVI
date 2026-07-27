"""`app.shutdown` — docs/10-deployment.md §4.1, docs/IMPLEMENTATION_PLAN.md Phase 13
task 2 ("Complete the SIGTERM in-flight-SSE-drain behavior").
"""

from __future__ import annotations

import asyncio

from app.shutdown import ShutdownState, track_stream


async def test_wait_drained_returns_immediately_when_no_streams_active() -> None:
    state = ShutdownState()
    assert await state.wait_drained(timeout_seconds=0.01) is True


async def test_track_stream_increments_and_decrements_active_count() -> None:
    state = ShutdownState()
    assert state.active_stream_count == 0

    async with track_stream(state):
        assert state.active_stream_count == 1

    assert state.active_stream_count == 0


async def test_track_stream_decrements_on_exception_too() -> None:
    state = ShutdownState()
    try:
        async with track_stream(state):
            raise ValueError("boom")
    except ValueError:
        pass

    assert state.active_stream_count == 0


async def test_wait_drained_blocks_until_stream_finishes() -> None:
    state = ShutdownState()
    order: list[str] = []

    async def _slow_stream() -> None:
        async with track_stream(state):
            await asyncio.sleep(0.05)
            order.append("stream_done")

    stream_task = asyncio.ensure_future(_slow_stream())
    await asyncio.sleep(0.01)  # let the stream register itself first
    assert state.active_stream_count == 1

    drained = await state.wait_drained(timeout_seconds=1.0)
    order.append("drained")

    assert drained is True
    assert order == ["stream_done", "drained"]
    await stream_task


async def test_wait_drained_times_out_with_streams_still_active() -> None:
    state = ShutdownState()

    async def _never_finishes() -> None:
        async with track_stream(state):
            await asyncio.sleep(10)

    stream_task = asyncio.ensure_future(_never_finishes())
    await asyncio.sleep(0.01)

    drained = await state.wait_drained(timeout_seconds=0.05)

    assert drained is False
    assert state.active_stream_count == 1
    stream_task.cancel()


async def test_exit_stream_never_goes_negative_on_repeated_exits() -> None:
    state = ShutdownState()
    async with track_stream(state):
        pass
    # A second, unmatched exit (defensive: shouldn't happen in practice, but the
    # counter must not drift negative and desynchronize `_drained`).
    state._exit_stream()
    assert state.active_stream_count == 0
    assert await state.wait_drained(timeout_seconds=0.01) is True


def test_begin_shutdown_sets_flag() -> None:
    state = ShutdownState()
    assert state.is_shutting_down is False
    state.begin_shutdown()
    assert state.is_shutting_down is True
