"""ORM model for `usage_metrics` — docs/07-database-design.md §3.7."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    false,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class UsageMetric(Base):
    __tablename__ = "usage_metrics"
    __table_args__ = (
        CheckConstraint("persona IN ('user', 'operator')", name="ck_usage_metrics_persona"),
        Index("idx_usage_metrics_created_at", "created_at"),
        Index("idx_usage_metrics_persona", "persona"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", name="fk_usage_metrics_session_id"),
        nullable=True,
    )
    user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    persona: Mapped[str | None] = mapped_column(Text, nullable=True)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    short_circuited: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    short_circuit_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    similarity_best_score: Mapped[float | None] = mapped_column(REAL, nullable=True)
    model_embedding_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_text_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
