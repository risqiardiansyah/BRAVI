"""initial schema

Revision ID: dbbf96997e42
Revises: 8a27f28d988b
Create Date: 2026-07-26 17:54:54.239858

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "dbbf96997e42"
down_revision: str | Sequence[str] | None = "8a27f28d988b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Confirmed with the project owner (see docs/IMPLEMENTATION_PLAN.md Phase 2
# note) — Cohere Embed v4 on Bedrock defaults to 1536 if `output_dimension`
# is unspecified; bedrock_client.py (Phase 3) must always pass 1024 explicitly.
EMBEDDING_DIMENSION = 1024


def upgrade() -> None:
    """Upgrade schema — docs/07-database-design.md §3."""
    op.create_table(
        "sessions",
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("persona", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("history_summary", sa.Text(), nullable=True),
        sa.Column("history_summary_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("persona IN ('user', 'operator')", name="ck_sessions_persona"),
    )
    op.create_index("idx_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("has_image", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.session_id"],
            name="fk_messages_session_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index("idx_messages_session_id", "messages", ["session_id", "created_at"])

    op.create_table(
        "knowledge_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("is_ingested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_knowledge_sources_path", "knowledge_sources", ["relative_path"], unique=True
    )

    op.create_table(
        "knowledge_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("superseded_by_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source_type IN ('file', 'text', 'url')", name="ck_knowledge_documents_source_type"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_knowledge_documents_status",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["knowledge_sources.id"],
            name="fk_knowledge_documents_source_id",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_document_id"],
            ["knowledge_documents.id"],
            name="fk_knowledge_documents_superseded_by_document_id",
            ondelete="SET NULL",
        ),
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSION), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            name="fk_knowledge_chunks_document_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_knowledge_chunks_embedding",
        "knowledge_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "job_type IN ('startup_batch', 'on_demand')", name="ck_ingestion_jobs_job_type"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_ingestion_jobs_status",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            name="fk_ingestion_jobs_document_id",
            ondelete="SET NULL",
        ),
    )

    op.create_table(
        "usage_metrics",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("persona", sa.Text(), nullable=True),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("short_circuited", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("short_circuit_reason", sa.Text(), nullable=True),
        sa.Column("similarity_best_score", sa.REAL(), nullable=True),
        sa.Column("model_embedding_used", sa.Text(), nullable=True),
        sa.Column("model_text_used", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("persona IN ('user', 'operator')", name="ck_usage_metrics_persona"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.session_id"],
            name="fk_usage_metrics_session_id",
        ),
    )
    op.create_index("idx_usage_metrics_created_at", "usage_metrics", ["created_at"])
    op.create_index("idx_usage_metrics_persona", "usage_metrics", ["persona"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_usage_metrics_persona", table_name="usage_metrics")
    op.drop_index("idx_usage_metrics_created_at", table_name="usage_metrics")
    op.drop_table("usage_metrics")

    op.drop_table("ingestion_jobs")

    op.drop_index("idx_knowledge_chunks_embedding", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")

    op.drop_table("knowledge_documents")

    op.drop_index("idx_knowledge_sources_path", table_name="knowledge_sources")
    op.drop_table("knowledge_sources")

    op.drop_index("idx_messages_session_id", table_name="messages")
    op.drop_table("messages")

    op.drop_index("idx_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
