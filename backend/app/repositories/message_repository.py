"""Repository for `messages` — docs/07-database-design.md §3.2."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select

from app.models.message import Message
from app.repositories.base import BaseRepository


class MessageRepository(BaseRepository[Message]):
    model = Message

    async def list_by_session_id(self, session_id: uuid.UUID) -> list[Message]:
        """Ordered message history for a session (docs/06-api-specification.md §3)."""
        result = await self._session.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
        )
        return list(result.scalars().all())

    async def delete_older_than(self, cutoff: datetime) -> int:
        """Purges `messages` rows older than `cutoff` — `services/retention_service.py`
        (docs/07-database-design.md §7). `sessions` rows are left untouched; only their
        messages are pruned."""
        result = cast(
            CursorResult[Any],
            await self._session.execute(delete(Message).where(Message.created_at < cutoff)),
        )
        return result.rowcount or 0
