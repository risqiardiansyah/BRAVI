"""`load_source` node — docs/05-ai-agent-design.md §3.2.

Obtains the raw bytes to extract text from: downloads a URL, reads a local file
path, or (for raw-text ingests) does nothing — `extract_text` reads `source_ref`
directly. If the caller already fetched the bytes (the startup job's content-hash
idempotency check in `app/jobs/run_initial_ingestion.py`, docs/07-database-design.md
§5, downloads first to compute the hash before deciding whether to (re-)ingest at
all), `raw_bytes` is reused as-is rather than fetched a second time.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from app.graphs.ingestion_state import IngestionState
from app.utils.http_download import download_bytes

logger = logging.getLogger(__name__)


async def load_source(state: IngestionState) -> dict[str, Any]:
    started = time.monotonic()

    if state.get("status") == "failed":
        # The caller already determined this attempt failed before invoking the graph
        # (e.g. the startup job's pre-fetch-for-hash-check download itself failed for a
        # brand-new source — docs/07-database-design.md §5). Nothing to do here but let
        # the failure route through the same terminal nodes as any other failure.
        return {"started_monotonic": started}

    source_type = state["source_type"]
    try:
        if source_type == "text":
            return {"started_monotonic": started}
        if state.get("raw_bytes") is not None:
            return {"started_monotonic": started}
        if source_type == "file":
            raw_bytes = Path(state["source_ref"]).read_bytes()
        elif source_type == "url":
            raw_bytes = await download_bytes(state["source_ref"])
        else:
            raise ValueError(f"unsupported source_type: {source_type!r}")
        return {"raw_bytes": raw_bytes, "started_monotonic": started}
    except Exception as exc:
        logger.warning(
            "load_source failed for source_ref=%s", state.get("source_ref"), exc_info=True
        )
        return {"status": "failed", "error": f"load_source: {exc}", "started_monotonic": started}
