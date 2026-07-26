# 04 — System Architecture

## 1. Architecture Style

Modular, service-oriented monolith (Phase 1): a single Python/FastAPI application exposing REST endpoints, delegating AI work to distinct **LangGraph graph instances**, backed by PostgreSQL/pgvector, with AWS Bedrock as the only LLM/embedding provider. Designed so components (ingestion, chat orchestration, analytics) can later be split into separate services if needed.

## 2. High-Level Component Diagram

```
┌────────────────────────────────────────────────────────────────────┐
│                         FastAPI Application                        │
│                                                                      │
│  ┌───────────────┐   ┌──────────────────┐   ┌──────────────────────┐ │
│  │ User Router    │   │ Operator Router  │   │ Ingestion Router     │ │
│  │ /api/chat      │   │ /api/opr/chat    │   │ /api/opr/ingest      │ │
│  │ /api/session   │   │ /api/opr/analytics│  │ /api/opr/knowledge   │ │
│  │ /api/messages  │   └────────┬─────────┘   │ DELETE .../{id}      │ │
│  │ /api/trending  │            │             └──────────┬───────────┘ │
│  └───────┬────────┘            │                        │            │
│          │                     │                        │            │
│          ▼                     ▼                        ▼            │
│  ┌────────────────────────────────────┐   ┌─────────────────────┐   │
│  │       Chat Orchestration Service    │   │  Ingestion Service   │   │
│  │  (invokes "user_chat_graph" or      │   │ (invokes LangGraph   │   │
│  │   "operator_chat_graph"; streams    │   │  "ingestion graph")  │   │
│  │   tokens back as SSE)               │   │                      │   │
│  └───────────────────┬──────────────────┘   └──────────┬──────────┘   │
│                       │                                  │             │
└───────────────────────┼──────────────────────────────────┼────────────┘
                         ▼                                  ▼
              ┌───────────────────┐              ┌────────────────────┐
              │   AWS Bedrock      │              │   AWS Bedrock       │
              │ (Text: Claude 4.6) │              │ (Embed: Cohere v4)  │
              └───────────────────┘              └────────────────────┘
                         │                                  │
                         ▼                                  ▼
              ┌──────────────────────────────────────────────────────┐
              │           PostgreSQL + pgvector                       │
              │  sessions | messages | knowledge_documents |          │
              │  knowledge_chunks (vector) | usage_metrics |          │
              │  ingestion_jobs                                       │
              └──────────────────────────────────────────────────────┘

              ┌────────────────────┐
              │       Redis         │  ◀── rate-limit middleware (all routers)
              │ (distributed rate   │      cache-only, not a source of truth —
              │  limit counters)    │      see 08-security.md §6
              └────────────────────┘
```

## 3. Three Graph Instances (per PRD requirement)

The system explicitly uses **separate LangGraph graph instances** rather than one monolithic orchestrator graph, to keep responsibilities isolated, independently testable, independently scalable, and — for the two chat graphs — isolated in what tools each persona's agent can reach:

1. **User Chat Graph** (`user_chat_graph`) — used by `/api/chat` only.
   - Nodes: `preprocess_input` (multimodal image handling) → `classify_greeting` → `classify_out_of_topic` → `embed_question` → `similarity_threshold_check` → `condense_history` (conditional) → `retrieve_chunks` → `generate_answer` (streamed, Bahasa Indonesia) → `append_sources` → `persist_message` → `log_metrics`.
   - Tool registry is QA-only — it has no access to ingestion or knowledge-management tools.
2. **Operator Chat Graph** (`operator_chat_graph`) — used by `/api/opr/chat` only.
   - Same node backbone as the User graph, plus `classify_add_knowledge_intent` (right after `classify_greeting` — bilingual keyword match, e.g. "tambah knowledge ai"/"add ai knowledge", returns the fixed `<BTN>Add Knowledge</BTN>` template with no Bedrock call at all), `route_by_intent` → `generate_summary` for the knowledge-summary sub-flow, and access to Operator-only tools (e.g., triggering ingestion, knowledge-management queries) that the User graph never wires in.
3. **Ingestion Graph** (`ingestion_graph`) — used by the startup job and `/api/opr/ingest`.
   - Nodes: `load_source` (file/text/url) → `extract_text` → `chunk_text` → `embed_chunks` → `store_vectors` → `update_ingestion_status` → `log_metrics`.

Rationale for **not** using a single monolithic graph:
- Different failure modes and retry semantics (chat needs low-latency short-circuits; ingestion is a long-running batch job).
- Different scaling needs (ingestion may run as background/async worker; chat must be low-latency request/response).
- Simpler testing/versioning of each graph independently.
- Avoids a bloated conditional-routing graph that mixes unrelated concerns.

Rationale for splitting `user_chat_graph` from `operator_chat_graph` specifically (rather than one graph branching on a `persona` flag):
- **Tool-access isolation is a security boundary, not just UX branching** — a single shared agent that can call "any tool inside" (e.g., an ingestion-trigger tool meant only for Operators) risks a User-facing request reaching an Operator-only capability through prompt injection or a routing bug. Two separate graph instances, each wired with only its own persona's tools, make that class of bug structurally impossible rather than reliant on a runtime check. See `11-coding-standard.md` §8.

## 4. Request Flow — `/api/chat` (happy path)

1. FastAPI receives request → validates payload (`question`, `user_id`, optional `session_id`, optional `file`) → opens an SSE streaming response.
2. **Session resolution** (no separate `POST /api/session` call required): if `session_id` is empty/omitted, create a new `sessions` row (`persona="user"`) and use it; if provided, look it up — found → continue using it; not found → respond `404` before opening the stream. Same rule applies verbatim to `/api/opr/chat` with `persona="operator"`.
3. If `file` is an image, it is passed as multimodal input directly to the Bedrock text model together with the question (no separate captioning step/service).
4. Invoke `user_chat_graph.astream(...)`.
5. Graph runs short-circuit checks (greeting → out-of-topic → similarity threshold). If short-circuited, streams the canned response as a `token` event followed by `done`.
6. If not short-circuited: condense history (if session has prior turns beyond a configured window) → retrieve top-k chunks from pgvector (each carrying `valid_until`/`superseded_by` metadata where set) → stream tokens from the Bedrock text model (system prompt + retrieved context + condensed history + question) as `token` events — answer is Markdown, always in Bahasa Indonesia, mentioning a cited document's freshness/versioning only when that metadata is actually present → append a `## Sources` Markdown section (`[Link Text](URL)` per citation) once generation completes.
7. Persist user + assistant messages (full assembled Markdown answer, including the Sources section).
8. Log usage metrics (latency, model, tokens, short-circuit tier).
9. Emit the terminal `done` event and close the stream.

`/api/opr/chat` follows the same flow through `operator_chat_graph`, with one extra early branch: if `classify_add_knowledge_intent` matches, the graph returns the fixed `<BTN>Add Knowledge</BTN>` template immediately after step 5 (short-circuited, no Bedrock call at all) — see `06-api-specification.md` §5.

## 4a. Request Flow — `DELETE /api/opr/knowledge/{id}`

1. FastAPI receives request → looks up `knowledge_documents` by id → `404` if not found.
2. Delete the row (cascades to `knowledge_chunks`); if `source_id` is set, reset the linked `knowledge_sources.is_ingested = false` (see `07-database-design.md` §5a for the resurrection-on-next-startup trade-off this implies).
3. Log the deletion as a destructive action (`user_id`, `knowledge_id`, `title`, chunks removed).
4. Return `{ "knowledge_id", "status": "deleted", "chunks_removed" }`.

No graph/LangGraph involvement — this is a plain repository-backed REST operation, not an agent tool call (consistent with add-knowledge also being a client-side form redirect rather than an agent-executed action — see `01-prd.md` §5).

## 5. Request Flow — Ingestion (startup job)

1. On app startup (or via CLI command `python -m app.jobs.run_initial_ingestion`), check `ingestion_jobs` table for a "startup" job marker.
2. If not yet run: query `knowledge_sources` table for document link records.
3. For each record: download PDF from `DOCUMENT_BASE_URL` + relative path → run `ingestion_graph`.
4. Mark startup job complete; log summary (success/failure counts).

## 6. Background Processing

- `/api/opr/ingest` (on-demand) should enqueue work rather than process synchronously for large files — recommended via a lightweight task queue (e.g., FastAPI `BackgroundTasks` for Phase 1; consider Celery/RQ/Arq if volume grows).
- Client receives a `knowledge_id` with `status: "queued"` immediately; status is queryable via `/api/opr/knowledge`.

## 7. Session & Context Management

- `sessions` table: `session_id`, `user_id`, `persona`, `title` (nullable, set once from the first user message — see `07-database-design.md` §3.1), `history_summary`/`history_summary_updated_at` (nullable, see below), `created_at`.
- `messages` table: `id`, `session_id`, `role` (`user`/`assistant`), `content`, `created_at`.
- Contextual condensation: when message count in a session exceeds `CONTEXT_CONDENSATION_MAX_TURNS`, older turns are incrementally folded into a rolling summary persisted to `sessions.history_summary` (not just held in-memory for the current request), then appended to the prompt as a "history summary" block instead of sending full raw history — bounds token growth across the whole session, not just the current request. See `17-memory-strategy.md` for the persistence/recompute mechanics and the context-window budget this leaves room for.

## 8. Deployment Topology (Phase 1)

- Single containerized FastAPI app (can run multiple replicas behind a load balancer — stateless w.r.t. session/business data).
- Single managed PostgreSQL instance with `pgvector` extension enabled.
- Single managed Redis instance for distributed rate limiting (cache-only, shared across all API replicas — see §9 rationale below).
- AWS Bedrock accessed via AWS SDK (boto3) using IAM credentials/role — no other LLM provider.
- On `SIGTERM`, the app stops accepting new connections and drains in-flight SSE streams before exiting, so rolling deploys don't cut off an in-progress chat response — see `10-deployment.md` §4.1.
- See `10-deployment.md` for full details.

## 9. Key Design Decisions

| Decision | Rationale |
|---|---|
| Three separate graph instances (user chat vs operator chat vs ingestion) | Isolation of concerns, independent scaling/testing, and — for user vs operator — tool-access isolation so a persona can never reach the other's tools (explicit project requirement) |
| Streaming (SSE-only) chat responses | Lower perceived latency; single consistent client contract across short-circuit and full-generation paths |
| No auth/RBAC in Phase 1 | Explicit product decision; routes alone separate personas |
| pgvector over external vector DB | Keeps stack simple; one database to operate; sufficient for expected scale |
| Short-circuit pipeline before retrieval/generation | Cost & latency control (explicit requirement) |
| Background/async ingestion for on-demand uploads | Avoid blocking API threads on large file processing |
| Redis added for rate limiting only (not caching/sessions) | The API is explicitly stateless/horizontally scaled, so the one abuse-prevention control that exists without auth (rate limiting) needs a shared store to actually work across replicas; scope deliberately kept minimal (cache, not source of truth) to avoid reintroducing state into the request path |
| Bedrock calls centralized behind a resilient client (timeout + bounded retry + circuit breaker) | A single hosted-model dependency needs first-class failure handling; prevents a Bedrock outage/throttling event from cascading into request pile-up |
| Retrieval top-k, generation max-tokens configurable (not hardcoded) | Lets ops tune retrieval quality/cost/latency tradeoffs without a code change, as the knowledge base and traffic grow |
| All chatbot output in Bahasa Indonesia, enforced in the system prompt (not post-hoc translation) | Explicit product requirement; keeping it in the prompt (not a translation pass) avoids an extra Bedrock call and an extra failure mode |
| Add-knowledge intent handled as a deterministic canned template (`<BTN>Add Knowledge</BTN>`), not an agent-executed action | Adding knowledge is a form-driven Operator workflow outside this system's API surface — the agent's job is to redirect, not to attempt the action itself; keeps `operator_chat_graph`'s tool surface unchanged |
| Knowledge deletion is hard-delete + `knowledge_sources.is_ingested` reset, not soft-delete | Simpler mental model (deleted really means gone from retrieval immediately); the reset is an explicit, documented trade-off (re-ingestable on next startup run) rather than a hidden soft-delete flag scattered through every query |
