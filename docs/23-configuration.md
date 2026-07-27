# 23 — Configuration Reference

## 1. Scope & Authority

This is a **navigational/validation reference, not a second source of truth**. The authoritative default values and the full raw `.env` block already live in `10-deployment.md` §3 (kept in sync with `01-prd.md` §8) — this document does not reproduce that block. If this document and `10-deployment.md` ever disagree, `10-deployment.md` wins and this one is out of date and should be fixed. What this document adds that neither existing doc has: settings grouped with type/secret/required metadata, and one consolidated startup-validation checklist (previously scattered across three files as isolated mentions).

## 2. Loading & Precedence (gap fill — not previously stated end-to-end)

- **Local/dev**: `.env` file, loaded via `pydantic-settings.BaseSettings` (`11-coding-standard.md` §5) — never `os.environ` accessed directly anywhere else in the codebase.
- **Staging/production**: injected as real process environment variables by the platform's secret manager (AWS Secrets Manager / SSM Parameter Store, `10-deployment.md` §6) — `.env` files are not deployed to these environments; the settings module reads from the process environment identically either way, so no code branches on which source supplied a value.
- No config value is ever hardcoded as a fallback default for a **secret** (credentials, connection strings). Only non-sensitive tuning knobs (e.g., `RETRIEVAL_TOP_K`) have in-code defaults; secrets must be explicitly supplied or startup fails (§4 below).

## 3. Settings by Category (metadata not previously compiled anywhere)

Grouping mirrors `10-deployment.md` §3's section comments, with added Secret/Required-in-production columns:

| Category | Variables | Secret? | Required in production? |
|---|---|---|---|
| Database | `DATABASE_URL` | Yes | Yes |
| | `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_STATEMENT_TIMEOUT_MS` | No | No (has default) |
| AWS / Bedrock credentials & models | `AWS_REGION`, `BEDROCK_EMBEDDING_MODEL`, `BEDROCK_TEXT_MODEL` | No | Yes |
| | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Yes | Only if not using an IAM role (`14-bedrock-integration.md` §3) |
| Bedrock resilience & behavior | `BEDROCK_TIMEOUT_SECONDS`, `BEDROCK_MAX_RETRIES`, `BEDROCK_RETRY_BACKOFF_BASE_MS`, `BEDROCK_MAX_OUTPUT_TOKENS`, `BEDROCK_TEMPERATURE` (`15-model-management.md` §3), `BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD`, `BEDROCK_CIRCUIT_BREAKER_COOLDOWN_SECONDS` (`14-bedrock-integration.md` §6) | No | No (has default) |
| Ingestion | `DOCUMENT_BASE_URL`, `INGESTION_RUN_ONCE`, `CHUNK_SIZE_TOKENS`, `CHUNK_OVERLAP_TOKENS`, `EMBEDDING_BATCH_SIZE`, `INGESTION_CONCURRENCY`, `INGESTION_CRON_SCHEDULE` (`10-deployment.md` §4.3 — 5-field cron expression, UTC, controlling when `app/jobs/ingestion_scheduler.py` runs the job; never run automatically at app/container startup) | No | Yes (`DOCUMENT_BASE_URL`); others have defaults |
| Retrieval / cost control | `SIMILARITY_SCORE_THRESHOLD`, `CONTEXT_CONDENSATION_MAX_TURNS`, `RETRIEVAL_TOP_K`, `SUMMARY_TOP_K`, `PGVECTOR_HNSW_EF_SEARCH` | No | No (has default) |
| Uploads | `MAX_IMAGE_UPLOAD_MB`, `MAX_FILE_UPLOAD_MB` | No | No (has default) |
| Redis / rate limiting | `REDIS_URL` | Treat as sensitive (internal network address) | Yes |
| | `RATE_LIMIT_REQUESTS_PER_MINUTE`, `RATE_LIMIT_BURST` | No | No (has default) |
| Streaming | `SSE_KEEPALIVE_INTERVAL_SECONDS` | No | No (has default) |
| Retention | `MESSAGE_RETENTION_DAYS`, `USAGE_METRICS_RETENTION_DAYS`, `RETENTION_CRON_SCHEDULE` (`10-deployment.md` §4.4 — 5-field cron expression, UTC, controlling when `app/jobs/retention_scheduler.py` runs `services/retention_service.py`; never run automatically at app/container startup) | No | No (has default) |
| Cost | `DAILY_COST_BUDGET_USD` (`19-cost-management.md` §4), `COST_BUDGET_CRON_SCHEDULE` (`10-deployment.md` §4.5 — 5-field cron expression, UTC, default hourly, controlling when `app/jobs/cost_budget_scheduler.py` runs `services/cost_budget_service.py`; never run automatically at app/container startup) | No | No (optional — no alert if `DAILY_COST_BUDGET_USD` unset) |
| CORS | `CORS_ALLOWED_ORIGINS` | No | Should be set (ships empty/restrictive by default — `08-security.md` §6a) |
| App | `APP_ENV`, `LOG_LEVEL`, `PORT` | No | No (has default) |

## 4. Startup Validation Checklist (gap fill — consolidated)

Previously scattered as isolated mentions (`13-roadmap.md` Phase 1 bullet, `05-ai-agent-design.md` §3.3); consolidated here as the full set `app/config.py` should enforce at boot, failing fast on violation rather than surfacing a confusing runtime error later:

- `CHUNK_OVERLAP_TOKENS < CHUNK_SIZE_TOKENS`
- `INGESTION_CRON_SCHEDULE` must be a valid 5-field cron expression (minute hour day month weekday)
- `RETENTION_CRON_SCHEDULE` must be a valid 5-field cron expression (minute hour day month weekday)
- `COST_BUDGET_CRON_SCHEDULE` must be a valid 5-field cron expression (minute hour day month weekday)
- `0 < SIMILARITY_SCORE_THRESHOLD <= 1`
- `0 <= BEDROCK_TEMPERATURE <= 1`
- `BEDROCK_MAX_RETRIES >= 0`, `BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD >= 1`
- `RATE_LIMIT_BURST` need **not** be `>= RATE_LIMIT_REQUESTS_PER_MINUTE` — burst is a short-window allowance independent of the steady-state per-minute rate. Noted explicitly so this isn't "corrected" into an incorrect invariant later.
- `DB_POOL_SIZE + DB_MAX_OVERFLOW` is reviewed against `(replica count × this value) < Postgres max_connections` at deploy/scaling time (`10-deployment.md` §7) — this is an operational check at scale-out time, not a single-process startup assertion, since a single replica's config can't know the eventual replica count.
- All secret-class vars (`DATABASE_URL`; `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` unless an IAM role is in use; `REDIS_URL`) must be non-empty when `APP_ENV=production`.
- `CORS_ALLOWED_ORIGINS` empty in production triggers a startup **warning**, not a failure — an intentional but risky default (`08-security.md` §6a, `01-prd.md` §11 risk #5) that should be visible, not silently accepted.

## 5. Full Variable List

See `10-deployment.md` §3 for the complete raw `.env` block with defaults — intentionally not reproduced here to avoid two copies drifting apart.
