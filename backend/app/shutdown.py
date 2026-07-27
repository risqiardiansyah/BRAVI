"""Graceful-shutdown coordination for in-flight SSE streams — docs/10-deployment.md
§4.1, docs/03-non-functional-requirements.md §3/§11.

`/api/chat`/`/api/opr/chat` hold open long-lived SSE connections for the duration of a
generation. A rolling deploy's `SIGTERM` must not cut those connections mid-stream.
Uvicorn already stops accepting new TCP connections as soon as its own shutdown sequence
begins ("stop accepting new connections" is native ASGI-server behavior, not something
this module reimplements) and keeps an already-open connection alive for as long as its
request-handler coroutine keeps running. The piece that *is* this app's responsibility is
not returning from the ASGI lifespan's shutdown phase (`app/main.py`) until every
in-flight SSE stream has actually finished — `shutdown_state` below tracks that count and
lets the lifespan handler block on it, bounded by a grace period, per §4.1's "let in-flight
SSE streams finish... bounded by a shutdown grace period."
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class ShutdownState:
    """One process-wide instance (`shutdown_state` below). Not thread-safe by design —
    this app runs a single asyncio event loop per process (one uvicorn worker), matching
    every other piece of shared in-process state in this codebase (e.g.
    `bedrock_client`'s circuit breaker)."""

    def __init__(self) -> None:
        self.is_shutting_down = False
        self._active_streams = 0
        self._drained = asyncio.Event()
        self._drained.set()

    @property
    def active_stream_count(self) -> int:
        return self._active_streams

    def begin_shutdown(self) -> None:
        self.is_shutting_down = True

    def _enter_stream(self) -> None:
        self._active_streams += 1
        self._drained.clear()

    def _exit_stream(self) -> None:
        self._active_streams -= 1
        if self._active_streams <= 0:
            self._active_streams = 0
            self._drained.set()

    async def wait_drained(self, *, timeout_seconds: float) -> bool:
        """Blocks until every tracked stream has finished, or `timeout_seconds` elapses
        first. Returns whether draining completed cleanly — the caller still proceeds
        with shutdown either way (the grace period is a bound, not a guarantee: docs/10-
        deployment.md §4.1 says "configure the orchestrator's termination grace period
        accordingly," i.e. the outer orchestrator, not this app, owns the hard cutoff)."""
        if self._active_streams == 0:
            return True
        logger.info(
            "graceful shutdown: waiting up to %.1fs for %d in-flight SSE stream(s) to drain",
            timeout_seconds,
            self._active_streams,
        )
        try:
            await asyncio.wait_for(self._drained.wait(), timeout=timeout_seconds)
            return True
        except TimeoutError:
            logger.warning(
                "graceful shutdown: grace period elapsed with %d SSE stream(s) still active",
                self._active_streams,
            )
            return False


shutdown_state = ShutdownState()


@asynccontextmanager
async def track_stream(state: ShutdownState = shutdown_state) -> AsyncIterator[None]:
    """Wraps one SSE stream's lifetime so `state` (the shared `shutdown_state` singleton
    by default; a fresh instance in tests) knows it is in flight — entered when the
    stream actually starts being consumed (not merely constructed), exited (successfully
    or on error) when it stops."""
    state._enter_stream()
    try:
        yield
    finally:
        state._exit_stream()
