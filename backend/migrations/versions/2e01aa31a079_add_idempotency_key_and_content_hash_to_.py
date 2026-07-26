"""add idempotency key and content hash to knowledge documents

Revision ID: 2e01aa31a079
Revises: dbbf96997e42
Create Date: 2026-07-26 20:57:52.244253

Phase 7 (docs/IMPLEMENTATION_PLAN.md) gap-fill: `06-api-specification.md` §6 and
`22-error-handling.md` §4 require `POST /api/opr/ingest` to honor a client-supplied
`Idempotency-Key` header (same key + same content -> return the original result; same
key + different content -> `409 IDEMPOTENCY_KEY_CONFLICT`), but `07-database-design.md`
§3 defines no column anywhere to persist that key. `knowledge_sources.content_hash`
(§3.3) cannot be reused — it only applies to startup-managed sources, and
`/api/opr/ingest`-created `knowledge_documents` rows never have a `source_id`.
Confirmed directly with the project owner (see `IMPLEMENTATION_PLAN.md` Phase 7 note):
add `idempotency_key`/`content_hash` directly to `knowledge_documents`, mirroring the
existing `knowledge_sources.content_hash` pattern.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2e01aa31a079"
down_revision: str | Sequence[str] | None = "dbbf96997e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("knowledge_documents", sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.add_column("knowledge_documents", sa.Column("content_hash", sa.Text(), nullable=True))
    op.create_index(
        "idx_knowledge_documents_idempotency_key",
        "knowledge_documents",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_knowledge_documents_idempotency_key", table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "content_hash")
    op.drop_column("knowledge_documents", "idempotency_key")
