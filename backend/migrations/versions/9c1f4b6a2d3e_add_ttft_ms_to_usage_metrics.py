"""add ttft_ms to usage_metrics

Revision ID: 9c1f4b6a2d3e
Revises: 2e01aa31a079
Create Date: 2026-07-27 09:00:00.000000

Phase 14 (docs/IMPLEMENTATION_PLAN.md) gap-fill: `03-non-functional-requirements.md` §1
requires "Time to First Token (TTFT), full RAG path < 2.5s p95", but `07-database-design.md`
§3.7's `usage_metrics` schema has only the aggregate `latency_ms` column — nothing captures
TTFT, so the release-gate load test (`12-testing-strategy.md` §6) has no persisted metric to
validate that target against. Confirmed directly with the project owner: add a nullable
`ttft_ms` column, mirroring the existing `latency_ms` column's type/nullability, rather than
inventing a separate table for one integer.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c1f4b6a2d3e"
down_revision: str | Sequence[str] | None = "2e01aa31a079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("usage_metrics", sa.Column("ttft_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("usage_metrics", "ttft_ms")
