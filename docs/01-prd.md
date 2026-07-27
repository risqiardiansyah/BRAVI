# 01 — Product Requirements Document (PRD)
### Project: `bravi-ai-chatbot`

| | |
|---|---|
| **Doc status** | Draft v0.1 |
| **Owner** | TBD (Product/Tech Lead) |
| **Related docs** | `00-project-overview.md`, `02-functional-requirements.md`, `03-non-functional-requirements.md`, `04-system-architecture.md`, `05-ai-agent-design.md`, `06-api-specification.md`, `07-database-design.md`, `08-security.md`, `09-observability.md`, `10-deployment.md`, `11-coding-standard.md`, `12-testing-strategy.md`, `13-roadmap.md`, `14-bedrock-integration.md`..`23-configuration.md` |

---

## 1. Background & Problem Statement

Bravi needs an internal AI chatbot capability that can answer questions strictly grounded in a curated set of ingested documents (knowledge base), rather than open-domain LLM knowledge. Today there is no system that:

- Orchestrates retrieval-augmented AI agents against ingested documents.
- Separates end-user chat from operator/knowledge-management workflows.
- Tracks AI usage cost/latency/model metrics for observability and cost governance.
- Avoids unnecessary LLM calls for trivial or out-of-scope input (cost & latency waste).

`bravi-ai-chatbot` is a backend + AI agent orchestration system that solves this by combining PostgreSQL/pgvector for storage & embeddings, a LangGraph-based agent pipeline, and AWS Bedrock as the LLM/embedding provider.

## 2. Goals

1. Provide a chatbot that answers **only** based on knowledge ingested into the system (file-grounded Q&A).
2. Support two distinct usage surfaces — **User** (public/end-user chat) and **Operator** (internal knowledge & analytics operations) — without implementing authentication/authorization (out of scope for this phase).
3. Maintain **session-based conversational context** (contextual condensation of history).
4. Minimize LLM token spend through pre-LLM short-circuiting (greeting/small-talk detection, similarity-threshold rejection, out-of-topic detection).
5. Provide **observability** on AI usage: latency, model used, token/cost, and question trends.
6. Support a **one-time startup ingestion job** that pulls existing documents from a database-provided link list and embeds them, plus on-demand operator ingestion afterward.
7. Use **AWS Bedrock** exclusively as the LLM/embedding provider.

## 3. Non-Goals (Out of Scope — Phase 1)

- User authentication, login, session tokens, or role-based access control (RBAC). `user_id` is passed by the client as a plain identifier.
- Multi-tenant support.
- Front-end/UI implementation (this PRD covers backend + AI agent orchestration only).
- Fine-tuning or training custom models.
- Real-time streaming voice/audio chat.

## 4. Target Users / Personas

| Persona | Description | Access |
|---|---|---|
| **User (Visitor/End-user)** | External or internal person chatting with the bot to get answers from the knowledge base. Can send text and images. | `/api/chat`, `/api/session`, `/api/messages`, `/api/trending` |
| **Operator** | Internal staff managing the knowledge base and monitoring bot usage. Can chat (with knowledge-summary capability), list knowledge, ingest new knowledge, and view analytics. | `/api/opr/chat`, `/api/opr/ingest`, `/api/opr/analytics`, plus all User endpoints |

> Note: No login/role enforcement is implemented in this phase — separation is purely by endpoint/route, and the client is trusted to call the correct endpoint for the correct persona.

## 5. Core Use Cases

1. **User asks a question** → system checks if it's greeting/small talk or out-of-topic → if genuine knowledge question, retrieve relevant chunks via pgvector similarity search → condense with session history if needed → generate grounded answer via Bedrock LLM, in Bahasa Indonesia → log usage metrics.
2. **User sends an image** with a question → image is processed/described and combined with text query for retrieval/answering.
3. **Operator asks for a knowledge summary** on a specific topic/question → system retrieves and summarizes relevant knowledge chunks.
4. **Operator asks to add knowledge** (e.g. "tambah knowledge ai") → system responds with a fixed template directing them to the add-knowledge form, instead of attempting the action itself — see §6.2.
5. **Operator lists ingested knowledge** → returns catalog of ingested documents/chunks with metadata (source, ingest date, status, freshness/versioning).
6. **Operator ingests new knowledge** from an uploaded file or raw text → chunk → embed (Bedrock Cohere embed model) → store in pgvector; optionally tags it with an expiry date and/or marks it as superseding an older document.
7. **Operator deletes outdated/incorrect knowledge** → document and its chunks/vectors are removed from the knowledge base immediately.
8. **Operator views analytics** → most-asked questions (combined across User and Operator personas, not split by role), usage volume, latency, token cost, model distribution.
9. **System auto-ingests existing documents** once at startup by reading document link records from the database and downloading/embedding each PDF from `DOCUMENT_BASE_URL`.
10. **Trending questions** are surfaced publicly via `/api/trending`.
11. **Answer mentions document freshness/versioning** when relevant → if a cited document has an expiry date or has been superseded by a newer one, the answer may note this; otherwise it says nothing about it.

## 6. Functional Requirements Summary

> Full detail in `02-functional-requirements.md`. Summary below.

### 6.1 Endpoints (minimum required)

| # | Method | Path | Purpose | Persona |
|---|---|---|---|---|
| 1 | POST | `/api/chat` | Send chat message (`session_id` optional — auto-created if empty, `question`, `user_id`, optional `file`); response streamed as SSE | User |
| 2 | GET | `/api/session` | List a user's sessions (`user_id`); there is no `POST /api/session` — sessions are created implicitly by `/api/chat` | User |
| 3 | POST | `/api/messages` | Fetch message history for a session (`session_id`) | User |
| 4 | GET | `/api/trending` | Get trending questions | User/Public |
| 5 | POST | `/api/opr/chat` | Operator chat incl. knowledge-summary questions (`session_id` optional, `question`, `user_id`); response streamed as SSE | Operator |
| 6 | POST | `/api/opr/ingest` | Ingest knowledge from file or text | Operator |
| 7 | GET | `/api/opr/analytics` | Usage analytics (top questions, latency, model, cost) | Operator |

Additional implied operator endpoints (to satisfy "list down Knowledge ingested" and knowledge lifecycle management):
| 8 | GET | `/api/opr/knowledge` | List ingested knowledge documents/chunks | Operator |
| 9 | DELETE | `/api/opr/knowledge/{id}` | Delete an ingested document and its chunks/vectors | Operator |

### 6.2 Conversational Behavior

- **Response language**: every chat response — generated or canned/short-circuited — is in **Bahasa Indonesia**, regardless of the language the question was asked in. Hard requirement, enforced in the system prompts, not a runtime translation pass.
- **Session & context awareness**: each `session_id` maintains chat history; when history exists, apply **contextual condensation** (summarize/compress prior turns) before passing to the retrieval/generation pipeline to control token growth.
- **Cost-saving short circuits (in order, before any expensive LLM call):**
  1. **Greeting/small-talk detection** → return a canned/default response, no LLM call.
  2. **Add-knowledge-intent detection** (`/api/opr/chat` only) → if the operator's question matches a bilingual trigger phrase (e.g. "tambah knowledge ai", "add ai knowledge"), return the fixed template `Silahkan klik tombol berikut untuk mengisi form: <BTN>Add Knowledge</BTN>` — no LLM call, never triggered on `/api/chat`.
  3. **Out-of-topic detection** → return a canned "I can only help with X" response before the most expensive step (LLM generation).
  4. **Similarity-score threshold check** → one embedding call against the knowledge base; if best match score < threshold, reject with a default "no relevant knowledge found" response — **no LLM call**.
  5. Only if the question passes all above does the system proceed to full RAG (retrieve → condense context → call Bedrock text model).
- **Multimodal input**: text and image supported on chat endpoints; image is processed (e.g., described/embedded) and factored into retrieval and/or answer generation.
- **Answer freshness/versioning**: when a full-RAG answer cites a document with an expiry date or that's been superseded by a newer one, the answer may mention this; when no such metadata exists on the cited document(s), the answer says nothing about it — never speculated.

### 6.3 Ingestion & Knowledge Lifecycle

- **Startup/one-time ingestion job**: runs once on first deploy/boot; reads a list of document references (with links) from the database; downloads each PDF from `{DOCUMENT_BASE_URL}/{relative_path}`; chunks, embeds (Bedrock embedding model), and stores vectors + metadata.
- **Operator on-demand ingestion**: via `/api/opr/ingest`, accepts an uploaded file (e.g., PDF) or raw text; same chunk → embed → store pipeline; must not block the request thread indefinitely (async/background job pattern recommended — see `04-system-architecture.md`); optionally tagged with `valid_until` and/or `supersedes_document_id`.
- Ingestion must be **idempotent/trackable** (avoid duplicate ingestion of the same source on repeated startup).
- **Deletion**: via `DELETE /api/opr/knowledge/{id}`, an operator can permanently remove an ingested document and its chunks/vectors. See `07-database-design.md` §5a for what happens to startup-managed source rows on deletion.

### 6.4 Analytics & Observability

- Track per-request: latency, model used (embedding vs text, model ID), token usage/cost estimate, short-circuit reason (if rejected early), session/user/persona.
- `/api/opr/analytics` surfaces: most-asked questions under a single `top_questions.user` list — a non-role calculation that counts questions from both User and Operator sessions together, not split by persona — plus volume over time, average latency, model usage breakdown.
- `/api/trending` surfaces top questions publicly (subset of analytics, User-facing).

## 7. Technical Constraints (given)

| Layer | Technology |
|---|---|
| Database | PostgreSQL + `pgvector` extension (embedding storage) |
| Cache / shared state | Redis (distributed rate limiting only — not a source of truth) |
| Backend | Python |
| AI Agent Orchestration | Python, LangChain / LangGraph |
| LLM & Embedding Provider | AWS Bedrock only |
| Embedding model | `BEDROCK_EMBEDDING_MODEL=arn:aws:bedrock:ap-southeast-3:586794442374:inference-profile/global.cohere.embed-v4:0` |
| Text/generation model | `BEDROCK_TEXT_MODEL=global.anthropic.claude-sonnet-4-6` |
| Config management | `.env` file for all environment variables (incl. `DOCUMENT_BASE_URL`) |

## 8. Environment Variables

> Full authoritative list maintained in `10-deployment.md` §3 — kept in sync with the block below.

```
# Database
DATABASE_URL=postgresql://user:pass@host:5432/bravi_ai_chatbot
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=10
DB_STATEMENT_TIMEOUT_MS=30000

# AWS Bedrock
AWS_REGION=ap-southeast-3
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
BEDROCK_EMBEDDING_MODEL=arn:aws:bedrock:ap-southeast-3:586794442374:inference-profile/global.cohere.embed-v4:0
BEDROCK_TEXT_MODEL=global.anthropic.claude-sonnet-4-6
BEDROCK_TIMEOUT_SECONDS=30
BEDROCK_MAX_RETRIES=3
BEDROCK_RETRY_BACKOFF_BASE_MS=500
BEDROCK_MAX_OUTPUT_TOKENS=1024
BEDROCK_TEMPERATURE=0.2
BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD=5
BEDROCK_CIRCUIT_BREAKER_COOLDOWN_SECONDS=30

# Ingestion
DOCUMENT_BASE_URL=https://example.com/documents
INGESTION_RUN_ONCE=true
CHUNK_SIZE_TOKENS=700
CHUNK_OVERLAP_TOKENS=100
EMBEDDING_BATCH_SIZE=16
INGESTION_CONCURRENCY=4

# Retrieval / cost control
SIMILARITY_SCORE_THRESHOLD=0.75
CONTEXT_CONDENSATION_MAX_TURNS=10
RETRIEVAL_TOP_K=5
SUMMARY_TOP_K=15
PGVECTOR_HNSW_EF_SEARCH=40

# Uploads
MAX_IMAGE_UPLOAD_MB=5
MAX_FILE_UPLOAD_MB=25

# Redis / rate limiting
REDIS_URL=redis://localhost:6379/0
RATE_LIMIT_REQUESTS_PER_MINUTE=30
RATE_LIMIT_BURST=10

# Streaming
SSE_KEEPALIVE_INTERVAL_SECONDS=15

# Retention
MESSAGE_RETENTION_DAYS=90
USAGE_METRICS_RETENTION_DAYS=180

# Cost management
DAILY_COST_BUDGET_USD=

# CORS
CORS_ALLOWED_ORIGINS=

# App
APP_ENV=development
LOG_LEVEL=INFO
PORT=8000
```

## 9. Non-Functional Requirements (summary — full detail in `03-non-functional-requirements.md`)

- **Security**: input sanitization, prompt-injection mitigation, file-type/size validation on uploads, no secrets in code (all via `.env`), Redis-backed rate limiting (required, not optional — see `08-security.md` §6).
- **Performance**: short-circuit paths must resolve in low latency (no LLM call); full RAG path latency budget TBD.
- **Cost efficiency**: minimize Bedrock invocations per the short-circuit rules in §6.2.
- **Reliability**: ingestion job must be resumable/retry-safe; failed document downloads should be logged, not crash the app.
- **Observability**: structured logs + metrics for every AI-involved request (see `09-observability.md`).
- **Maintainability**: coding standards per `11-coding-standard.md`.

## 10. Success Metrics (KPIs)

| Metric | Target (initial) |
|---|---|
| % of requests short-circuited before LLM call (greeting/out-of-topic/low-similarity) | Track baseline, optimize over time |
| Average end-to-end latency (full RAG path) | TBD after baseline measurement |
| Answer groundedness (answers backed by ingested knowledge only) | Qualitative review / spot-check |
| Ingestion success rate (startup job) | 100% of listed documents ingested or explicitly logged as failed |
| Analytics availability | `/api/opr/analytics` reflects data with ≤ X min lag |

## 11. Risks & Open Questions

| # | Item | Notes |
|---|---|---|
| 1 | No auth/RBAC in this phase | Endpoints are trusted by path only; must be revisited before public exposure. |
| 2 | Similarity threshold tuning | Needs empirical tuning per embedding model (Cohere embed-v4) and domain content, using real traffic once available. |
| 3 | `embed-v4` max input tokens / output vector dimension not yet confirmed against AWS docs | Blocks finalizing `CHUNK_SIZE_TOKENS` default and `knowledge_chunks.embedding VECTOR(n)` dimension — see `05-ai-agent-design.md` §3.3 and `07-database-design.md` §3.5. |
| 4 | `CORS_ALLOWED_ORIGINS` not yet populated | Depends on the (not-yet-built) frontend's deployed origin(s); ships restrictive/empty by default — see `08-security.md` §6. |
| 5 | Redis is a new required infra dependency (distributed rate limiting) | Not present in earlier drafts of this PRD; must be provisioned alongside PostgreSQL — see `10-deployment.md` §6. |

Resolved since earlier drafts (kept here for traceability, no longer open): image handling (Bedrock multimodal, confirmed in `05-ai-agent-design.md` §2.3), ingestion source-of-truth schema and duplicate-ingestion strategy (`07-database-design.md` §3.3/§5), file upload size/type limits (`08-security.md` §3, `MAX_IMAGE_UPLOAD_MB`/`MAX_FILE_UPLOAD_MB`).

## 12. Milestones (high-level — detail in `13-roadmap.md`)

1. **M1** — Project scaffolding, `.env` setup, DB schema (pgvector), Bedrock connectivity smoke test.
2. **M2** — Ingestion pipeline (startup job + operator on-demand) working end-to-end.
3. **M3** — Core chat pipeline: short-circuit logic (greeting/out-of-topic/similarity threshold) + RAG + session context condensation.
4. **M4** — Operator endpoints: knowledge listing, opr-chat/summary, analytics.
5. **M5** — Observability/metrics wiring, security hardening, load testing.
6. **M6** — Docs finalization, handover.

## 13. Appendix — Full Documentation Structure

```
bravi-ai-chatbot/
│
├── docs/
│   ├── 00-project-overview.md
│   ├── 01-prd.md                     <- this document
│   ├── 02-functional-requirements.md
│   ├── 03-non-functional-requirements.md
│   ├── 04-system-architecture.md
│   ├── 05-ai-agent-design.md
│   ├── 06-api-specification.md
│   ├── 07-database-design.md
│   ├── 08-security.md
│   ├── 09-observability.md
│   ├── 10-deployment.md
│   ├── 11-coding-standard.md
│   ├── 12-testing-strategy.md
│   ├── 13-roadmap.md
│   ├── 14-bedrock-integration.md
│   ├── 15-model-management.md
│   ├── 16-tool-calling.md
│   ├── 17-memory-strategy.md
│   ├── 18-rag-design.md
│   ├── 19-cost-management.md
│   ├── 20-performance-target.md
│   ├── 21-event-flow.md
│   ├── 22-error-handling.md
│   ├── 23-configuration.md
│   ├── IMPLEMENTATION_PLAN.md
│   └── prompts/
│       ├── architect.md
│       ├── backend.md
│       ├── ai-agent.md
│       └── reviewer.md
│
├── README.md
```
