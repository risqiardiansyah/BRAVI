# 07 — Database Design (PostgreSQL + pgvector)

## 1. Extensions

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto; -- for gen_random_uuid()
```

## 2. Entity Overview

```
sessions ──< messages
knowledge_sources ──< knowledge_documents ──< knowledge_chunks (vector)
ingestion_jobs
usage_metrics
```

## 3. Table Definitions

### 3.1 `sessions`
```sql
CREATE TABLE sessions (
    session_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                    TEXT NOT NULL,
    persona                    TEXT NOT NULL CHECK (persona IN ('user', 'operator')),
    title                      TEXT,                     -- nullable; set from the first user message, see note below
    history_summary            TEXT,                     -- nullable; rolling condensed-history summary, see 17-memory-strategy.md §3
    history_summary_updated_at TIMESTAMPTZ,               -- nullable; last time history_summary was recomputed
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_sessions_user_id ON sessions(user_id);
```
> `title` is `NULL` at session creation. `persist_message` sets it once, the first time a `role='user'` message is persisted for that session, as the question text truncated to ~60 chars (no LLM call — plain truncation, not summarization). It is never overwritten afterward. Exposed by `GET /api/session` (see `06-api-specification.md` §1) for client-side session-list display; not editable in Phase 1.
>
> `history_summary`/`history_summary_updated_at` back the `condense_history` node's rolling summary (`05-ai-agent-design.md` §2.1/§2.3) — both `NULL` until the session first exceeds `CONTEXT_CONDENSATION_MAX_TURNS`, then updated incrementally on each subsequent condensation. Not purged by the retention job in §7 (only `messages` rows are pruned) — see `17-memory-strategy.md` §3/§6 for the full persistence/recompute semantics.

### 3.2 `messages`
```sql
CREATE TABLE messages (
    id           BIGSERIAL PRIMARY KEY,
    session_id   UUID NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content      TEXT NOT NULL,
    has_image    BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_session_id ON messages(session_id, created_at);
```

### 3.3 `knowledge_sources`
Source-of-truth list for the startup ingestion job (documents referenced by link).
```sql
CREATE TABLE knowledge_sources (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    relative_path  TEXT NOT NULL,          -- appended to DOCUMENT_BASE_URL
    title          TEXT,
    content_hash   TEXT,                    -- for idempotency / change detection
    is_ingested    BOOLEAN NOT NULL DEFAULT false,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_knowledge_sources_path ON knowledge_sources(relative_path);
```

### 3.4 `knowledge_documents`
Represents each ingested document (from startup job or `/api/opr/ingest`).
```sql
CREATE TABLE knowledge_documents (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id                UUID REFERENCES knowledge_sources(id),   -- nullable if ingested via opr/ingest text
    title                    TEXT,
    source_url               TEXT,                    -- citation link; DOCUMENT_BASE_URL + relative_path for source-linked docs, uploader-provided/storage URL for opr/ingest, NULL for raw-text ingests
    source_type              TEXT NOT NULL CHECK (source_type IN ('file', 'text', 'url')),
    status                   TEXT NOT NULL CHECK (status IN ('queued','processing','completed','failed')) DEFAULT 'queued',
    chunk_count              INT NOT NULL DEFAULT 0,
    error_message            TEXT,
    valid_until              DATE,                    -- nullable; optional expiry set at ingest time, see §5b
    superseded_by_document_id UUID REFERENCES knowledge_documents(id) ON DELETE SET NULL,  -- nullable; set on the OLD doc when a newer one supersedes it
    ingested_at               TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
> `source_url` feeds the `[Link Text](URL)` citation format required in chat answers (see `06-api-specification.md` §0). Chunks retrieved from a document with `source_url IS NULL` (raw-text ingests with no addressable location) are still cited by title, without a link.
>
> `valid_until`/`superseded_by_document_id` feed the answer-freshness behavior in `05-ai-agent-design.md` §2.3/§4: `similarity_search` attaches these to each `top_matches` entry, and the system prompt is instructed to mention them naturally *only when set* — see §5b below for how they get populated.

### 3.5 `knowledge_chunks`
```sql
CREATE TABLE knowledge_chunks (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id    UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    content        TEXT NOT NULL,
    page_number    INT,
    chunk_index    INT NOT NULL,
    embedding      VECTOR(1024),   -- dimension must match BEDROCK_EMBEDDING_MODEL output; confirm exact dim before migration
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Approximate nearest neighbor index (tune lists/m/ef per data size)
CREATE INDEX idx_knowledge_chunks_embedding
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
```
> **Note:** Confirm the actual output dimension of the Cohere `embed-v4` Bedrock inference profile before finalizing the `VECTOR(n)` size — this must match exactly or inserts will fail.

### 3.6 `ingestion_jobs`
Tracks both the one-time startup job and on-demand ingestion jobs.
```sql
CREATE TABLE ingestion_jobs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type       TEXT NOT NULL CHECK (job_type IN ('startup_batch', 'on_demand')),
    document_id    UUID REFERENCES knowledge_documents(id) ON DELETE SET NULL,  -- SET NULL (not CASCADE): deleting a document keeps its ingestion job history, see §5a
    status         TEXT NOT NULL CHECK (status IN ('queued','processing','completed','failed')) DEFAULT 'queued',
    error_message  TEXT,
    started_at     TIMESTAMPTZ,
    completed_at   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.7 `usage_metrics`
```sql
CREATE TABLE usage_metrics (
    id                   BIGSERIAL PRIMARY KEY,
    session_id           UUID REFERENCES sessions(session_id),
    user_id              TEXT,
    persona              TEXT CHECK (persona IN ('user', 'operator')),
    endpoint             TEXT NOT NULL,
    question             TEXT,
    short_circuited      BOOLEAN NOT NULL DEFAULT false,
    short_circuit_reason TEXT,           -- 'greeting' | 'out_of_topic' | 'low_similarity' | 'add_knowledge_intent' (operator only) | null
    similarity_best_score REAL,
    model_embedding_used TEXT,
    model_text_used      TEXT,
    input_tokens         INT,
    output_tokens        INT,
    estimated_cost_usd    NUMERIC(10,4),
    latency_ms            INT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_usage_metrics_created_at ON usage_metrics(created_at);
CREATE INDEX idx_usage_metrics_persona ON usage_metrics(persona);
```

## 4. Analytics Query Notes

- **Trending / top questions**: aggregate `usage_metrics.question` (normalized: lowercased, whitespace-trimmed, optionally clustered by embedding similarity for semantic dedup) grouped by `persona`/window.
- **Latency percentiles**: computed via `percentile_cont` over `usage_metrics.latency_ms`.
- **Cost**: sum `estimated_cost_usd` over the requested period.

## 5. Idempotency Strategy for Startup Ingestion

- `knowledge_sources.content_hash` computed from the downloaded file bytes (e.g., SHA-256).
- On each startup run: for each source row, if `is_ingested = true` **and** hash unchanged → skip; if hash changed → re-ingest (create a new `knowledge_documents` row, optionally soft-delete/replace old chunks).

## 5a. Knowledge Deletion (`DELETE /api/opr/knowledge/{knowledge_id}`)

Operator-only, hard delete — see `06-api-specification.md` §7.1 for the endpoint contract:

1. Delete the `knowledge_documents` row for `knowledge_id`. `knowledge_chunks.document_id` has `ON DELETE CASCADE`, so its chunks (and their vectors) are removed in the same statement — the document stops appearing in `similarity_search` immediately, no separate filter needed anywhere in the chat graphs.
2. `ingestion_jobs.document_id` is `ON DELETE SET NULL` (§3.6) — job history for this document is kept (for audit/observability) but its `document_id` link goes `NULL` rather than the row being deleted or the delete failing on a FK violation.
3. If the deleted document has `source_id` set (i.e., it came from the startup-managed `knowledge_sources` list, not an `/api/opr/ingest` upload), reset `knowledge_sources.is_ingested = false` for that source row.
   - **Explicit trade-off, not an oversight**: this means the *next* startup ingestion run (`07-database-design.md` §5) will treat it as not-yet-ingested and re-ingest it from `DOCUMENT_BASE_URL`, effectively undoing the deletion on next deploy — because `knowledge_sources` is still the source of truth for what *should* exist. If an operator wants a startup-managed document gone permanently, the corresponding row must also be removed from `knowledge_sources` (no dedicated endpoint for that in Phase 1 — a direct DB/admin action). This is documented here so it isn't rediscovered as a surprise in production.
   - Documents ingested directly via `/api/opr/ingest` (no `source_id`) have no such resurrection risk — deleting them is final.
4. `superseded_by_document_id` is set on an **old** document, pointing at the **new** one that replaced it. Deleting the new document would otherwise orphan that pointer — `superseded_by_document_id` has `ON DELETE SET NULL` (§3.4) specifically so this degrades gracefully to "no known replacement" instead of a dangling reference or a blocked delete.
5. This is a destructive, unauthenticated-but-Operator-path action — log it (`user_id`, `knowledge_id`, `title`, chunk count removed) per `09-observability.md`; see `08-security.md` for the audit-logging note.

## 5b. Freshness / Versioning Metadata (`valid_until`, `superseded_by_document_id`)

- Both fields are set **at ingest time only** in Phase 1 — optional fields on `POST /api/opr/ingest` (`06-api-specification.md` §6): `valid_until` (ISO date) and `supersedes_document_id` (UUID of an existing document this upload replaces).
- When `supersedes_document_id` is provided, the ingestion service sets `knowledge_documents.superseded_by_document_id = <new document's id>` **on the old document being superseded** (not on the new one) — the new document itself carries no `superseded_by_document_id` (it isn't superseded by anything).
- There is no update/PATCH endpoint to change these on an already-ingested document in Phase 1 — correcting them means re-ingesting.
- Both are surfaced to the chat graphs via `similarity_search`/`top_matches` and to API clients via the `sources[]` array on the `done` SSE event (`06-api-specification.md` §0), so a frontend can render a badge even where the model doesn't weave it into prose.

## 6. Migrations

- Use Alembic (Python) for schema migrations.
- Migration files stored under `backend/migrations/`.

## 7. Retention / Cleanup

- `retention_service` (see `11-coding-standard.md` §2) runs on a schedule and purges `messages` older than `MESSAGE_RETENTION_DAYS` (default `90`) and `usage_metrics` older than `USAGE_METRICS_RETENTION_DAYS` (default `180`) — see `03-non-functional-requirements.md` §8. `sessions` rows are left in place (only their `messages` are pruned) so `GET /api/session` history isn't silently truncated to zero.
- At small-to-medium volume, a plain `DELETE ... WHERE created_at < now() - interval` on the indexed `created_at` column is sufficient. At large scale (§8) this becomes a partition-drop instead.

## 8. Scaling Considerations

- **Time-based partitioning**: once `messages`/`usage_metrics` reach a size where retention `DELETE`s cause noticeable bloat/vacuum pressure, convert both to native Postgres range-partitioned tables (e.g., monthly partitions on `created_at`). Retention cleanup then becomes `DROP TABLE`/detach-partition instead of a row-by-row `DELETE`, which is dramatically cheaper at scale and avoids autovacuum thrash. Not required at Phase 1 launch volume; a Phase 5+ migration once real traffic data justifies it.
- **HNSW index maintenance**: pgvector's HNSW index is built incrementally as rows are inserted — no periodic rebuild is required for correctness, but recall can drift as the knowledge base grows well past its original sizing assumptions. Re-evaluate `PGVECTOR_HNSW_EF_SEARCH` (runtime, no rebuild) first; only rebuild the index (`REINDEX`, or build-parameter changes like `m`/`ef_construction`) if tuning `ef_search` alone stops being sufficient.
- **Read replica**: `/api/opr/analytics` and `GET /api/opr/knowledge` are read-heavy and not latency-critical the way chat is — routing them to a read replica once write (chat) traffic grows is a straightforward scale-out path that doesn't require any schema change.
