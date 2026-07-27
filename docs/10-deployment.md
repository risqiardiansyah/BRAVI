# 10 — Deployment

## 1. Environments

| Environment | Purpose |
|---|---|
| `local` | Developer machine, docker-compose (app + postgres/pgvector) |
| `development` | Shared dev/testing environment |
| `staging` | Pre-production validation |
| `production` | Live environment |

## 2. Containerization

- Application packaged as a Docker image (Python slim base, non-root user).
- `docker-compose.yml` (local/dev) provisioning:
  - `app` service (FastAPI + LangGraph)
  - `db` service (`pgvector/pgvector` official image, or `postgres` + manual extension install)
- Example `docker-compose.yml` services:
```yaml
services:
  app:
    build: .
    env_file: .env
    ports: ["8000:8000"]
    depends_on: [db, redis]
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: bravi_ai_chatbot
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    volumes: ["pgdata:/var/lib/postgresql/data"]
    ports: ["5432:5432"]
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
volumes:
  pgdata:
```
`redis` backs the distributed rate limiter (`08-security.md` §6) — required because the app is stateless/horizontally scaled, so rate-limit counters can't live in-process. It's a cache, not a source of truth: no volume/persistence needed (see §9).

## 3. Environment Variables (full list)

All variables loaded via `.env` locally; via the platform's secret manager in staging/production. See `.env.example` in project root. This is the authoritative default-value list — `23-configuration.md` provides a categorized/validation view of the same variables and must not be treated as a second source of truth if the two ever disagree.

```
# --- Database ---
DATABASE_URL=postgresql://user:pass@host:5432/bravi_ai_chatbot

# --- AWS Bedrock ---
AWS_REGION=ap-southeast-3
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
BEDROCK_EMBEDDING_MODEL=arn:aws:bedrock:ap-southeast-3:586794442374:inference-profile/global.cohere.embed-v4:0
BEDROCK_TEXT_MODEL=global.anthropic.claude-sonnet-4-6

# --- Ingestion ---
DOCUMENT_BASE_URL=https://example.com/documents
INGESTION_RUN_ONCE=true
CHUNK_SIZE_TOKENS=700
CHUNK_OVERLAP_TOKENS=100
EMBEDDING_BATCH_SIZE=16
INGESTION_CONCURRENCY=4
INGESTION_CRON_SCHEDULE=0 2 * * *

# --- Retrieval / cost control ---
SIMILARITY_SCORE_THRESHOLD=0.75
CONTEXT_CONDENSATION_MAX_TURNS=10
RETRIEVAL_TOP_K=5
SUMMARY_TOP_K=15
BEDROCK_MAX_OUTPUT_TOKENS=1024
BEDROCK_TEMPERATURE=0.2

# --- Bedrock resilience ---
BEDROCK_TIMEOUT_SECONDS=30
BEDROCK_MAX_RETRIES=3
BEDROCK_RETRY_BACKOFF_BASE_MS=500
BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD=5
BEDROCK_CIRCUIT_BREAKER_COOLDOWN_SECONDS=30

# --- Uploads ---
MAX_IMAGE_UPLOAD_MB=5
MAX_FILE_UPLOAD_MB=25

# --- Redis (rate limiting) ---
REDIS_URL=redis://localhost:6379/0
RATE_LIMIT_REQUESTS_PER_MINUTE=30
RATE_LIMIT_BURST=10

# --- Database pool ---
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=10
DB_STATEMENT_TIMEOUT_MS=30000

# --- pgvector tuning ---
PGVECTOR_HNSW_EF_SEARCH=40

# --- Streaming (SSE) ---
SSE_KEEPALIVE_INTERVAL_SECONDS=15

# --- Retention ---
MESSAGE_RETENTION_DAYS=90
USAGE_METRICS_RETENTION_DAYS=180
RETENTION_CRON_SCHEDULE=0 3 * * *

# --- Cost management ---
DAILY_COST_BUDGET_USD=
COST_BUDGET_CRON_SCHEDULE=0 * * * *

# --- CORS ---
CORS_ALLOWED_ORIGINS=

# --- App ---
APP_ENV=development
LOG_LEVEL=INFO
PORT=8000
```

## 4. Startup Sequence

1. Container starts → run DB migrations (Alembic) automatically or via init container/job.
2. Verify `pgvector` extension enabled.
3. FastAPI app starts, `GET /health`/`GET /health/ready` pass (DB + Redis reachable), traffic routed in. The `app` service does **not** wait on ingestion in any form — see §4.3.
4. The ingestion job (idempotent — see `07-database-design.md` §5) runs on its own schedule, independently of `app`'s startup — see §4.3.

### 4.1 Graceful Shutdown (rolling deploys)

`/api/chat`/`/api/opr/chat` hold open SSE connections for the duration of a generation — a rolling deploy that hard-kills the previous replica mid-stream cuts users off. On `SIGTERM`: stop accepting new connections, let in-flight SSE streams finish (bounded by a shutdown grace period comfortably longer than `BEDROCK_TIMEOUT_SECONDS` plus generation time), then exit. Configure the orchestrator's termination grace period accordingly (e.g., ECS `stopTimeout` / Kubernetes `terminationGracePeriodSeconds`).

### 4.2 Load Balancer / Reverse Proxy Timeout Alignment (SSE)

SSE responses are long-lived compared to typical REST calls. The load balancer/reverse-proxy **idle timeout must exceed** the worst-case time between bytes on the stream — otherwise it silently kills the connection mid-answer. `SSE_KEEPALIVE_INTERVAL_SECONDS` (server sends a `: keepalive\n\n` comment ping at this interval) keeps bytes flowing during long generations; set the LB/proxy idle timeout to at least `2 × SSE_KEEPALIVE_INTERVAL_SECONDS`. If deploying behind nginx, also disable response buffering for these routes (`X-Accel-Buffering: no`) or SSE chunks will be held instead of streamed. See `06-api-specification.md` §0.

### 4.3 Ingestion Scheduling — Cron, Not Deploy-Time, Not Blocking

The startup ingestion job runs on a recurring schedule, not once inline at boot and not gating `app`'s readiness:

- **Schedule**: `INGESTION_CRON_SCHEDULE`, a standard 5-field cron expression (minute hour day month weekday), evaluated in UTC. Default `0 2 * * *` (daily at 02:00 UTC).
- **Runner**: `python -m app.jobs.ingestion_scheduler` — a long-running process (its own `docker-compose.yml` service) that registers `run_initial_ingestion` (docs/07-database-design.md §5's idempotent job) against `INGESTION_CRON_SCHEDULE` and otherwise sits idle. It does **not** run the job immediately when it starts — only at each scheduled occurrence.
- **`app` never waits on this** — `app`'s own `depends_on` only covers `db`/`redis`. At a large source-list size (thousands of documents), a full ingestion run can take a long time; blocking API readiness on it would mean the whole API is unreachable for that entire window rather than just answering against a partially-ingested knowledge base. `app` starts serving traffic as soon as `db`/`redis` are ready, and the knowledge base fills in (or refreshes) progressively as scheduled runs complete.
- **No overlapping runs**: if a run is still in progress when the next scheduled occurrence arrives, it is skipped rather than started concurrently (`max_instances=1`); if the process was down across more than one missed occurrence, only one catch-up run fires when it restarts, not one per missed occurrence (`coalesce=True`).
- **Ad hoc/manual run**: `python -m app.jobs.run_initial_ingestion` (or `docker compose run --rm ingestion python -m app.jobs.run_initial_ingestion`) still runs the job once, immediately, on demand — independent of the cron schedule.

### 4.4 Retention Cleanup Scheduling — Cron, Not Deploy-Time, Not Blocking

The `messages`/`usage_metrics` retention cleanup (`07-database-design.md` §7) follows the same cron-scheduled, fire-and-forget pattern as ingestion (§4.3), not a one-off run at deploy time:

- **Schedule**: `RETENTION_CRON_SCHEDULE`, a standard 5-field cron expression (minute hour day month weekday), evaluated in UTC. Default `0 3 * * *` (daily at 03:00 UTC — after the default `INGESTION_CRON_SCHEDULE` occurrence so the two never overlap).
- **Runner**: `python -m app.jobs.retention_scheduler` — a long-running process (its own `docker-compose.yml` service) that registers `services/retention_service.py::run_retention_cleanup` against `RETENTION_CRON_SCHEDULE` and otherwise sits idle. It does **not** run the job immediately when it starts — only at each scheduled occurrence.
- **`app` never waits on this** — same rationale as §4.3.
- **No overlapping runs**: `max_instances=1`/`coalesce=True`, identical semantics to §4.3.

### 4.5 Cost-Budget Check Scheduling — Cron, Not Deploy-Time, Not Blocking

The daily cost-budget check (`docs/19-cost-management.md` §4) follows the same
cron-scheduled, fire-and-forget pattern as ingestion/retention (§4.3/§4.4):

- **Schedule**: `COST_BUDGET_CRON_SCHEDULE`, a standard 5-field cron expression (minute
  hour day month weekday), evaluated in UTC. Default `0 * * * *` (hourly) — unlike
  retention's once-daily cadence, this check needs to run intra-day so a same-day budget
  breach is caught as it happens, not only after the day has already ended.
- **Runner**: `python -m app.jobs.cost_budget_scheduler` — a long-running process (its
  own `docker-compose.yml` service) that registers
  `services/cost_budget_service.py::run_cost_budget_check` against
  `COST_BUDGET_CRON_SCHEDULE` and otherwise sits idle. It does **not** run the check
  immediately when it starts — only at each scheduled occurrence.
- **`app` never waits on this** — same rationale as §4.3.
- **No overlapping runs**: `max_instances=1`/`coalesce=True`, identical semantics to §4.3.
- **No-op when `DAILY_COST_BUDGET_USD` is unset**: the check still runs on schedule but
  always resolves to "not exceeded" (docs/19-cost-management.md §4: unset means no budget
  alert).

## 5. CI/CD (recommended)

1. **CI**: lint (ruff/flake8), type-check (mypy), unit tests, dependency vulnerability scan, build Docker image.
2. **CD**: push image to registry → deploy to `staging` → run smoke tests → manual/auto promote to `production`.
3. Migrations run as a distinct pipeline step before new app version receives traffic.

## 6. Infrastructure (indicative — adjust to actual cloud target)

- Compute: container platform (e.g., AWS ECS/Fargate, EKS, or equivalent).
- Database: managed PostgreSQL with `pgvector` support (e.g., AWS RDS for PostgreSQL ≥ 15 with pgvector, or self-managed on EC2/EKS).
- Redis: managed (e.g., AWS ElastiCache for Redis) for the distributed rate limiter (`08-security.md` §6) — required infra, not optional, once horizontally scaled.
- Bedrock: same AWS account/region (`ap-southeast-3`) as configured model ARNs; ensure IAM role attached to compute has `bedrock:InvokeModel` permission scoped to the configured model ARNs.
- Secrets: AWS Secrets Manager / SSM Parameter Store injected as environment variables at deploy time.

## 7. Scaling Configuration

- API service: horizontal autoscaling on CPU/latency (stateless w.r.t. session/business data — safe to scale out; Redis is the one shared cross-replica dependency, and it's cache-only, not a scaling bottleneck for correctness).
- Ingestion background workers (if separated from web process): scaled independently based on queue depth, bounded by `INGESTION_CONCURRENCY` per worker to avoid overwhelming Bedrock embedding throughput.
- Database connection pool (`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`) must be sized so `(pool size × replica count) < Postgres max_connections` — a common horizontal-scaling failure mode is connection exhaustion as replicas scale out with an unchanged pool size.
- `PGVECTOR_HNSW_EF_SEARCH` trades recall for query latency at read time (no reindex needed to tune it) — raise it if retrieval quality degrades as the knowledge base grows; lower it if `similarity_search` latency dominates the short-circuit-pipeline budget.

## 8. Rollback Strategy

- Blue/green or rolling deployment with health-check gating.
- DB migrations must be backward-compatible with the previous app version for one release cycle to support safe rollback.

## 9. Backup & DR

- Automated daily PostgreSQL backups (including `pgvector` data) with defined retention (e.g., 7–30 days).
- Redis requires no backup — it holds only disposable rate-limit counters; a full data loss on restart degrades to "temporarily unlimited," never data loss.
- Document restore procedure and RTO/RPO targets (to be defined with infra team).
