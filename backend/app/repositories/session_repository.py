"""Repository for `sessions` — docs/07-database-design.md §3.1."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.session import Session
from app.repositories.base import BaseRepository


class SessionRepository(BaseRepository[Session]):
    model = Session

    async def list_by_user_id(
        self, user_id: str, *, limit: int, offset: int
    ) -> tuple[list[Session], int]:
        """Newest-first page of a user's sessions, plus the total matching count
        (docs/06-api-specification.md §1)."""
        count_result = await self._session.execute(
            select(func.count()).select_from(Session).where(Session.user_id == user_id)
        )
        total = count_result.scalar_one()

        rows_result = await self._session.execute(
            select(Session)
            .where(Session.user_id == user_id)
            .order_by(Session.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows_result.scalars().all()), total
