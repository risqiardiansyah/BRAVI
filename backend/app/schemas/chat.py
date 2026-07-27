"""Request/response schemas for `POST /api/chat` — docs/06-api-specification.md §0/§2.

`ChatStreamEvent` is the single fixed SSE event structure (§0): every field is always
present; fields not applicable to a given `type` are `null` rather than omitted, so a
client can parse every line with one schema.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, field_validator

# docs/08-security.md §3 — Input Validation Rules.
_QUESTION_MAX_LENGTH = 2_000
_USER_ID_MAX_LENGTH = 128
# TEXT (control characters) — everything below 0x20 except tab/newline/CR, plus DEL.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")


class ChatRequestFields(BaseModel):
    """Validated shape of the request, regardless of whether it arrived as JSON or
    `multipart/form-data` (docs/06-api-specification.md §2) — the router parses either
    wire format into this same model before any business logic runs."""

    session_id: uuid.UUID | None = None
    question: str
    user_id: str

    @field_validator("question", mode="after")
    @classmethod
    def _validate_question(cls, value: str) -> str:
        value = _CONTROL_CHAR_RE.sub("", value)
        if len(value) > _QUESTION_MAX_LENGTH:
            raise ValueError(
                f"`question` must not exceed {_QUESTION_MAX_LENGTH} characters "
                f"(docs/08-security.md §3), got {len(value)}."
            )
        return value

    @field_validator("user_id", mode="after")
    @classmethod
    def _validate_user_id(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) > _USER_ID_MAX_LENGTH:
            raise ValueError(
                f"`user_id` must not exceed {_USER_ID_MAX_LENGTH} characters "
                f"(docs/08-security.md §3), got {len(stripped)}."
            )
        if stripped and not _USER_ID_RE.match(stripped):
            raise ValueError(
                "`user_id` may only contain letters, digits, and '.', '_', '-', '@' "
                "(docs/08-security.md §3)."
            )
        return stripped


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
