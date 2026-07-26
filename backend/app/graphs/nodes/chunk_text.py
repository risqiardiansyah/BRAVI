"""`chunk_text` node — docs/05-ai-agent-design.md §3.2/§3.3, docs/18-rag-design.md §3.

Token-based (not character-based) chunking via `app/utils/chunking.py`. Each page's
text is chunked independently so every resulting chunk keeps one accurate
`page_number` for citation (docs/07-database-design.md §3.3) rather than concatenating
all pages first and losing per-chunk page attribution.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.graphs.ingestion_state import ChunkRecord, IngestionState
from app.utils.chunking import split_into_token_chunks

logger = logging.getLogger(__name__)


async def chunk_text(state: IngestionState) -> dict[str, Any]:
    try:
        chunks: list[ChunkRecord] = []
        chunk_index = 0
        for page in state["pages"]:
            for piece in split_into_token_chunks(
                page["text"],
                chunk_size_tokens=settings.CHUNK_SIZE_TOKENS,
                chunk_overlap_tokens=settings.CHUNK_OVERLAP_TOKENS,
            ):
                chunks.append(
                    {
                        "content": piece,
                        "page_number": page["page_number"],
                        "chunk_index": chunk_index,
                    }
                )
                chunk_index += 1

        if not chunks:
            return {"status": "failed", "error": "chunk_text: produced zero chunks"}
        return {"chunks": chunks}
    except Exception as exc:
        logger.warning(
            "chunk_text failed for document_id=%s", state.get("document_id"), exc_info=True
        )
        return {"status": "failed", "error": f"chunk_text: {exc}"}
