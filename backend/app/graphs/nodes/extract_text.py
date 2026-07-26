"""`extract_text` node — docs/05-ai-agent-design.md §3.2.

PDF text extraction preserves page numbers for citation (docs/07-database-design.md
§3.3, "preserve section/page metadata"). Raw-text sources have no page structure —
every chunk from them carries `page_number: None`.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.graphs.ingestion_state import IngestionState, PageText

logger = logging.getLogger(__name__)


async def extract_text(state: IngestionState) -> dict[str, Any]:
    source_type = state["source_type"]
    try:
        if source_type == "text":
            pages: list[PageText] = [{"page_number": None, "text": state["source_ref"]}]
        else:
            raw_bytes = state.get("raw_bytes")
            if not raw_bytes:
                raise ValueError("no bytes available to extract text from")
            pages = _extract_pdf_pages(raw_bytes)

        if not any(page["text"].strip() for page in pages):
            return {"status": "failed", "error": "extract_text: no extractable text found"}
        return {"pages": pages}
    except Exception as exc:
        logger.warning(
            "extract_text failed for document_id=%s", state.get("document_id"), exc_info=True
        )
        return {"status": "failed", "error": f"extract_text: {exc}"}


def _extract_pdf_pages(raw_bytes: bytes) -> list[PageText]:
    try:
        reader = PdfReader(BytesIO(raw_bytes))
        pages: list[PageText] = [
            {"page_number": index + 1, "text": page.extract_text() or ""}
            for index, page in enumerate(reader.pages)
        ]
    except PdfReadError as exc:
        raise ValueError(f"corrupt or unreadable PDF: {exc}") from exc
    return pages
