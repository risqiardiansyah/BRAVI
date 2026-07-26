"""Server-Sent Events helpers — docs/06-api-specification.md §0.

Generic streaming infrastructure shared by `/api/chat` (Phase 9) and, later, `/api/opr/chat`
(Phase 10) — not chat business logic itself, hence `utils/` rather than `services/`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TypeVar

from pydantic import BaseModel

_T = TypeVar("_T")

KEEPALIVE_COMMENT = ": keepalive\n\n"


def format_sse_event(event: BaseModel) -> str:
    """One `data: {...}\\n\\n` line — docs/06-api-specification.md §0's fixed JSON schema."""
    return f"data: {event.model_dump_json()}\n\n"


async def stream_with_keepalive(
    source: AsyncIterator[_T], *, interval_seconds: float
) -> AsyncIterator[_T | None]:
    """Relays `source` 1:1, yielding `None` (a keepalive signal) after `interval_seconds`
    of inactivity between items — without ever cancelling the in-flight upstream call.

    `asyncio.wait_for(anext(...), timeout=...)` would cancel the pending `__anext__()` call
    on timeout, risking silently dropping a chunk that arrives just as the timeout fires
    (LangGraph's custom-stream-mode queue included). Instead, the same pending task is
    re-awaited on every timeout with `asyncio.wait` (no cancellation) until it actually
    completes, so a slow generation only ever produces extra keepalive pings, never a lost
    token (docs/06-api-specification.md §0's keepalive requirement,
    docs/12-testing-strategy.md §3's "keepalive comment ping is emitted" test).
    """
    aiter = source.__aiter__()
    pending: asyncio.Task[_T] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(aiter.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=interval_seconds)
            if not done:
                yield None
                continue
            try:
                result = pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
            yield result
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
