"""Response schemas for `/api/opr/ingest`, `/api/opr/knowledge` —
docs/06-api-specification.md §6/§7/§7.1.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class IngestResponse(BaseModel):
    knowledge_id: uuid.UUID
    # Normally "queued" (a fresh ingest just accepted). On an `Idempotency-Key` retry
    # matching prior content, this reflects the *original* request's current status
    # instead (docs/06-api-specification.md §6: "returns the original knowledge_id/status").
    status: Literal["queued", "processing", "completed", "failed"]


class KnowledgeListItem(BaseModel):
    id: uuid.UUID
    title: str | None
    url: str | None
    source_type: str
    ingested_at: datetime | None
    status: str
    chunk_count: int
    valid_until: date | None
    superseded_by_document_id: uuid.UUID | None


class KnowledgeListResponse(BaseModel):
    total: int
    knowledge: list[KnowledgeListItem]


class KnowledgeDeleteResponse(BaseModel):
    knowledge_id: uuid.UUID
    status: Literal["deleted"]
    chunks_removed: int
