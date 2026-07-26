"""`ingestion_graph`'s state schema — docs/05-ai-agent-design.md §3.1 (conceptual).

The doc's own schema is explicitly marked "conceptual"; this extends it with the
fields the real pipeline needs — document/job linkage (created by the caller before
invoking the graph, so `POST /api/opr/ingest` can return a `knowledge_id` immediately
per docs/06-api-specification.md §6, a later phase's concern), per-page text, and
per-chunk metadata (docs/07-database-design.md §3.3's "preserve page metadata for
citation" requirement) — while keeping the same core field names/meaning.
"""

from __future__ import annotations

import uuid
from typing import Literal, TypedDict


class IngestionError(Exception):
    """Domain exception for ingestion pipeline failures — docs/11-coding-standard.md §6.

    Nodes catch this (and lower-level library exceptions such as a corrupt-PDF parse
    error or a failed download) internally and translate them into
    `state["status"] = "failed"` / `state["error"]` rather than letting them propagate,
    so one corrupt/unreachable source fails only that document
    (docs/05-ai-agent-design.md §3.2, Phase 6 Definition of Done).
    """


class PageText(TypedDict):
    page_number: int | None
    text: str


class ChunkRecord(TypedDict):
    content: str
    page_number: int | None
    chunk_index: int


class IngestionState(TypedDict, total=False):
    # file path, raw text, or URL — docs/05-ai-agent-design.md §3.1
    source_type: Literal["file", "text", "url"]
    source_ref: str

    # knowledge_documents.id / ingestion_jobs.id — both created by the caller
    # before invoking the graph (docs/06-api-specification.md §6's synchronous
    # `knowledge_id` response is a later phase's concern, but the row already
    # exists by the time the graph runs either way).
    document_id: uuid.UUID
    job_id: uuid.UUID

    # Set only for startup-batch (knowledge_sources-linked) runs.
    knowledge_source_id: uuid.UUID | None
    # sha256 of the downloaded bytes; persisted onto knowledge_sources on success.
    content_hash: str | None

    # Pre-fetched by the caller to avoid a second download; else fetched by load_source.
    raw_bytes: bytes | None
    pages: list[PageText]
    chunks: list[ChunkRecord]
    embeddings: list[list[float]]

    status: Literal["queued", "processing", "completed", "failed"]
    error: str | None

    # time.monotonic() at first node run — docs/09-observability.md §4 duration tracking.
    started_monotonic: float
