# 11 — Coding Standard

## 1. Language & Tooling

- **Python** ≥ 3.11.
- Formatter: `black`.
- Linter: `ruff` (replaces flake8/isort in one tool).
- Type checking: `mypy` (strict mode encouraged for core modules).
- Dependency management: `poetry` or `pip-tools` (pick one; keep lockfile committed).

## 2. Project Structure (backend)

```
backend/
├── app/
│   ├── main.py                 # FastAPI app entrypoint
│   ├── config.py               # Pydantic BaseSettings (.env loader)
│   ├── api/
│   │   ├── user_router.py      # /api/chat, /api/session, /api/messages, /api/trending
│   │   ├── operator_router.py  # /api/opr/*
│   │   └── system_router.py    # /health, /health/ready, /metrics
│   ├── graphs/
│   │   ├── nodes/               # shared pure node functions (embed_question, similarity_search, ...)
│   │   ├── user_chat_graph.py   # builds the graph for /api/chat — QA-only node/tool wiring
│   │   ├── operator_chat_graph.py  # builds the graph for /api/opr/chat — QA + summary + operator tools
│   │   └── ingestion_graph.py
│   ├── tools/
│   │   ├── user_tools.py        # tools reachable ONLY from user_chat_graph
│   │   └── operator_tools.py    # tools reachable ONLY from operator_chat_graph (e.g., ingestion trigger)
│   ├── middleware/
│   │   └── rate_limit.py        # Redis-backed token-bucket, applied to /api/chat, /api/opr/chat, /api/opr/ingest
│   ├── services/
│   │   ├── chat_service.py
│   │   ├── ingestion_service.py
│   │   ├── analytics_service.py
│   │   └── retention_service.py  # scheduled cleanup per MESSAGE_RETENTION_DAYS / USAGE_METRICS_RETENTION_DAYS
│   ├── models/                 # SQLAlchemy ORM models
│   ├── schemas/                # Pydantic request/response schemas
│   ├── repositories/           # DB access layer (per table/aggregate)
│   ├── clients/
│   │   ├── bedrock_client.py    # timeout + bounded retry + circuit breaker — see §12
│   │   └── redis_client.py
│   ├── jobs/
│   │   └── run_initial_ingestion.py
│   └── utils/
├── migrations/                 # Alembic
├── tests/
├── .env.example
├── pyproject.toml
└── Dockerfile
```

## 3. Naming Conventions

- Modules/files: `snake_case`.
- Classes: `PascalCase`.
- Functions/variables: `snake_case`.
- Constants/env keys: `UPPER_SNAKE_CASE`.
- Pydantic schema classes suffixed `Request`/`Response` (e.g., `ChatRequest`, `ChatResponse`).

## 4. Layering Rules

- **Routers** (`api/`) contain only request parsing/validation and calling a service — no business logic.
- **Services** (`services/`) contain orchestration/business logic, call graphs and repositories.
- **Graphs** (`graphs/`) contain LangGraph node/edge definitions only — no direct DB access; use repositories injected or passed via state/context where needed.
- **Repositories** (`repositories/`) are the only layer executing SQL/ORM queries.
- **Clients** (`clients/`) wrap external calls (Bedrock) — all Bedrock SDK usage isolated here for testability/mocking.

## 5. Configuration

- All configuration via `app/config.py` using `pydantic-settings.BaseSettings`, reading from `.env`.
- No `os.environ` access scattered across the codebase — always via the typed settings object.

## 6. Error Handling

- Use custom exception classes per domain (e.g., `IngestionError`, `BedrockInvocationError`) caught centrally by FastAPI exception handlers, mapped to consistent JSON error responses:
```json
{ "error": { "code": "string", "message": "string" } }
```
- Never leak stack traces or internal exception messages to the client in production (`APP_ENV=production`).

## 7. Async & Concurrency

- Use `async def` route handlers and async DB drivers (e.g., `asyncpg`/SQLAlchemy async engine) for I/O-bound operations.
- Long-running ingestion tasks must not block the event loop — use `BackgroundTasks`, a worker process, or `asyncio.create_task` with proper supervision.
- `/api/chat` and `/api/opr/chat` handlers return an SSE (`text/event-stream`) response, backed by the graph's async streaming invocation (`graph.astream(...)`) — never buffer the full answer server-side before responding, and never offer a non-streaming/NDJSON alternative. See `06-api-specification.md` §0 for the wire format.

## 8. LangGraph Conventions

- One file per graph in `graphs/`.
- Node functions are pure where possible: `def node_name(state: StateType) -> StateType`.
- No node should directly call another node's function — routing/composition happens only via graph edges.
- All Bedrock/DB calls inside nodes go through `clients/`/`repositories/`, never inline SDK calls.

### 8.1 Persona-Isolated Agent Orchestration (User vs Operator)

User and Operator chat are built as **two separate graph instances** — `user_chat_graph.py` and `operator_chat_graph.py` — not one graph that branches on a `persona` field. This is a deliberate security/isolation rule, not a style preference: a single shared agent wired with every tool (QA retrieval, summarization, ingestion triggers, knowledge management, ...) means any request reaching that agent — including a User-facing one, via a routing bug or prompt injection — could invoke a tool that should be Operator-only (e.g., triggering ingestion). Splitting the graphs makes that class of bug structurally impossible instead of relying on a runtime persona check.

Rules:
- `graphs/user_chat_graph.py` may only import from `tools/user_tools.py` and `graphs/nodes/` (shared QA nodes). It must never import `tools/operator_tools.py`.
- `graphs/operator_chat_graph.py` may import both `tools/operator_tools.py` and `tools/user_tools.py`/`graphs/nodes/` as needed (Operator is a superset for QA purposes, plus its own summary/knowledge-management tools).
- Tools that trigger ingestion, mutate knowledge base state, or expose analytics/internal data must live only in `tools/operator_tools.py` and must never be imported by anything under the User request path (`api/user_router.py` → `chat_service.py` → `user_chat_graph.py`).
- Shared logic (e.g., `embed_question`, `similarity_search`, `condense_history`) is factored into `graphs/nodes/` as plain functions and wired into both graphs — this avoids duplicating QA behavior while keeping each graph's *tool registry* distinct.
- A lint/CI check (or code review checklist item) should verify `user_chat_graph.py` has no import path reaching `operator_tools.py`, so the isolation can't silently regress.
- See `05-ai-agent-design.md` §1–§2 and `04-system-architecture.md` §3 for the corresponding design rationale.

## 9. Testing Conventions

- Test files mirror source structure under `tests/`.
- Bedrock and DB calls mocked in unit tests; integration tests use a test database (and optionally recorded/mocked Bedrock responses) — see `12-testing-strategy.md`.

## 10. Git & PR Conventions

- Conventional Commits style: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`.
- PRs require: passing CI (lint, type-check, tests), at least one reviewer approval.
- No secrets committed; `.env` in `.gitignore`.

## 11. Documentation-in-Code

- All public service/repository functions have docstrings (purpose, params, returns, raises).
- Complex LangGraph routing logic documented inline with a comment referencing the relevant section of `05-ai-agent-design.md`.

## 12. Bedrock Client Resilience

`clients/bedrock_client.py` is the **only** place Bedrock is ever called from (see §4) — every graph node that needs Bedrock goes through it, so resilience behavior is defined once, not reimplemented per node:

- **Timeout**: every call bounded by `BEDROCK_TIMEOUT_SECONDS`.
- **Bounded retry with backoff**: up to `BEDROCK_MAX_RETRIES` retries, exponential backoff from `BEDROCK_RETRY_BACKOFF_BASE_MS` with jitter, retried only for transient/throttling errors (never for validation errors — those fail immediately).
- **Circuit breaker**: after `BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD` consecutive retry-exhausted failures, the client trips open and fails fast (without attempting the call) for `BEDROCK_CIRCUIT_BREAKER_COOLDOWN_SECONDS`, rather than letting every incoming request individually retry-and-timeout against a Bedrock outage — this is what prevents an upstream outage from cascading into request pile-up/thread exhaustion under load. Full state-machine detail: `14-bedrock-integration.md` §6.
- **Output cap**: `BEDROCK_MAX_OUTPUT_TOKENS` passed on every text-generation call (`generate_answer`, `generate_summary`, `condense_history`).
- A node/service must never construct its own `boto3` Bedrock client or implement ad hoc retry logic — that defeats the point of centralizing this behavior and is a code-review blocker (see `docs/prompts/reviewer.md`).

## 13. Redis Usage

`clients/redis_client.py` wraps the one sanctioned use of Redis in this system: the rate-limit token bucket in `middleware/rate_limit.py` (applied to `/api/chat`, `/api/opr/chat`, `/api/opr/ingest`). Redis is a cache, not a source of truth — never store session state, message content, or anything that must survive a Redis restart there; that all belongs in PostgreSQL.
