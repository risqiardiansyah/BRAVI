"""Request/response schemas for `POST /api/messages` — docs/06-api-specification.md §3."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class MessagesRequest(BaseModel):
    session_id: uuid.UUID


class MessageItem(BaseModel):
    role: str
    content: str
    created_at: datetime


class MessagesResponse(BaseModel):
    session_id: uuid.UUID
    messages: list[MessageItem]
