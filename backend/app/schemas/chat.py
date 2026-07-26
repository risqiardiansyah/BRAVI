"""Request/response schemas for `POST /api/chat` — docs/06-api-specification.md §0/§2.

`ChatStreamEvent` is the single fixed SSE event structure (§0): every field is always
present; fields not applicable to a given `type` are `null` rather than omitted, so a
client can parse every line with one schema.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel


class ChatRequestFields(BaseModel):
    """Validated shape of the request, regardless of whether it arrived as JSON or
    `multipart/form-data` (docs/06-api-specification.md §2) — the router parses either
    wire format into this same model before any business logic runs."""

    session_id: uuid.UUID | None = None
    question: str
    user_id: str


class ChatSourceItem(BaseModel):
    document_id: uuid.UUID
    title: str
    url: str | None
    page: int | None
    valid_until: date | None
    superseded_by_title: str | None


class ChatStreamEvent(BaseModel):
    type: Literal["token", "done", "error"]
    session_id: uuid.UUID
    content: str | None = None
    answer: str | None = None
    sources: list[ChatSourceItem] | None = None
    short_circuited: bool | None = None
    short_circuit_reason: str | None = None
    mode: str | None = None
    code: str | None = None
    message: str | None = None
