"""Response schemas for `GET /api/session` — docs/06-api-specification.md §1."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class SessionListItem(BaseModel):
    session_id: uuid.UUID
    persona: str
    title: str | None
    created_at: datetime


class SessionListResponse(BaseModel):
    user_id: str
    total: int
    sessions: list[SessionListItem]
