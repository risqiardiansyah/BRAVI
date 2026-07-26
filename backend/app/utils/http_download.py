"""Shared HTTP download helper.

Used both by the `load_source` ingestion-graph node (fetching a URL source directly)
and by `app/jobs/run_initial_ingestion.py` (which downloads a source's bytes once
upfront to compute a content-hash before deciding whether to (re-)ingest at all,
docs/07-database-design.md §5) — factored out so the two don't duplicate the same
`httpx` call/timeout/error-handling logic.
"""

from __future__ import annotations

import httpx

DOWNLOAD_TIMEOUT_SECONDS = 30.0


async def download_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content
