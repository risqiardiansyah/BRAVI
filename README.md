# bravi-ai-chatbot

AI Agent Orchestration backend for a document-grounded chatbot, with separate flows for **User** and **Operator** personas, built on Python, LangChain/LangGraph, PostgreSQL + pgvector, and AWS Bedrock (embedding + text generation). Includes usage/latency/cost observability.

> No authentication or role-based access control is implemented in this phase — User vs Operator is determined purely by which API route is called.

## Tech Stack

| Layer | Technology |
|---|---|
| Database | PostgreSQL + `pgvector` |
| Cache / shared state | Redis (distributed rate limiting only — not a source of truth) |
| Backend | Python (FastAPI-style REST API) |
| AI Agent Orchestration | Python, LangChain / LangGraph |
| LLM & Embeddings | AWS Bedrock only (Cohere Embed v4, Claude Sonnet 4.6) |

## Core Endpoints

| Method | Path | Persona |
|---|---|---|
| POST | `/api/chat` | User |
| GET | `/api/session` | User |
| POST | `/api/messages` | User |
| GET | `/api/trending` | User/Public |
| POST | `/api/opr/chat` | Operator |
| POST | `/api/opr/ingest` | Operator |
| GET | `/api/opr/knowledge` | Operator |
| DELETE | `/api/opr/knowledge/{id}` | Operator |
| GET | `/api/opr/analytics` | Operator |
| GET | `/health`, `/health/ready`, `/metrics` | System (orchestrator/monitoring) |

Full request/response contracts: [`docs/06-api-specification.md`](docs/06-api-specification.md)

## Key Behaviors

- **Cost-controlled chat pipeline**: greeting/small-talk → (Operator only: add-knowledge-intent) → out-of-topic → similarity-score threshold — all resolved *before* any Bedrock text-generation call. Only genuine, in-domain, sufficiently-similar questions reach full RAG generation.
- **Streaming chat responses**: `/api/chat` and `/api/opr/chat` always stream the answer as Server-Sent Events (SSE) as it's generated, rather than waiting for the full completion.
- **Answers always in Bahasa Indonesia**: every generated or canned response, regardless of the question's language — enforced in the system prompt, not a translation pass.
- **Markdown-formatted answers with inline sources**: the assistant's answer is Markdown, with a trailing `Sources` section listing each citation as `[Link Text](URL)`, mentioning a cited document's expiry/replacement only when that metadata actually exists (never speculated).
- **Multimodal image understanding**: when a user attaches an image, it's read directly by an AWS Bedrock multimodal (vision-capable) LLM — no separate captioning model/service.
- **Operator add-knowledge shortcut**: an Operator asking to add knowledge (e.g. "tambah knowledge ai") gets redirected to the add-knowledge form via a fixed `<BTN>Add Knowledge</BTN>` template — no LLM call, and never triggered on the User-facing `/api/chat`.
- **Knowledge deletion**: `DELETE /api/opr/knowledge/{id}` permanently removes a document and its chunks/vectors from retrieval.
- **Implicit session management**: there is no `POST /api/session` — calling `/api/chat`/`/api/opr/chat` with no `session_id` creates one automatically; passing an existing `session_id` continues that session; passing an unknown `session_id` is a `404`. `GET /api/session?user_id=...` lists a user's sessions.
- **Session-aware context** with contextual condensation of history to bound token growth.
- **Persona-isolated agent orchestration**: User and Operator chat are handled by separate LangGraph graph instances with separate tool registries, so a User-facing agent can never invoke Operator-only tools (e.g., ingestion).
- **One-time startup ingestion job** that reads document links from the database and downloads/embeds them from `DOCUMENT_BASE_URL`.
- **On-demand operator ingestion** from file or raw text, processed asynchronously, batched/concurrency-bounded (`EMBEDDING_BATCH_SIZE`, `INGESTION_CONCURRENCY`) and idempotent (content-hash / `Idempotency-Key`).
- **Redis-backed rate limiting** on chat/ingest routes — the one abuse-prevention control that exists without auth, and it has to be shared-store-backed since the API is stateless/horizontally scaled.
- **Resilient Bedrock client**: bounded timeout/retry-with-backoff and a circuit breaker in front of every Bedrock call, so a Bedrock outage degrades gracefully instead of cascading.
- **Full usage observability**: latency, model used, tokens, estimated cost, short-circuit tier — per request, plus `/health`, `/health/ready`, and `/metrics` for operational monitoring.

## Documentation Index

| Doc | Description |
|---|---|
| [`docs/00-project-overview.md`](docs/00-project-overview.md) | What this project is and why it exists |
| [`docs/01-prd.md`](docs/01-prd.md) | Product Requirements Document |
| [`docs/02-functional-requirements.md`](docs/02-functional-requirements.md) | Detailed functional requirements (FR-1..FR-14) |
| [`docs/03-non-functional-requirements.md`](docs/03-non-functional-requirements.md) | Performance, security, cost, reliability targets |
| [`docs/04-system-architecture.md`](docs/04-system-architecture.md) | Component diagram, request flows, design decisions |
| [`docs/05-ai-agent-design.md`](docs/05-ai-agent-design.md) | LangGraph chat & ingestion graph design |
| [`docs/06-api-specification.md`](docs/06-api-specification.md) | Full REST API contract |
| [`docs/07-database-design.md`](docs/07-database-design.md) | PostgreSQL/pgvector schema |
| [`docs/08-security.md`](docs/08-security.md) | Threat model & mitigations |
| [`docs/09-observability.md`](docs/09-observability.md) | Logging, metrics, dashboards |
| [`docs/10-deployment.md`](docs/10-deployment.md) | Environments, containerization, CI/CD |
| [`docs/11-coding-standard.md`](docs/11-coding-standard.md) | Project structure & conventions |
| [`docs/12-testing-strategy.md`](docs/12-testing-strategy.md) | Test levels & coverage targets |
| [`docs/13-roadmap.md`](docs/13-roadmap.md) | Delivery phases M1–M6 |
| [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) | **Start here to build.** Phase-by-phase build sequence with dependencies, Definition of Done, and verification checklists for an AI coding agent |
| [`docs/14-bedrock-integration.md`](docs/14-bedrock-integration.md) | Bedrock SDK mechanics: streaming, credentials, error taxonomy, circuit breaker |
| [`docs/15-model-management.md`](docs/15-model-management.md) | Model registry pattern, inference parameters, versioning/upgrade procedure |
| [`docs/16-tool-calling.md`](docs/16-tool-calling.md) | Why orchestration is a fixed graph, not LLM-driven tool-calling |
| [`docs/17-memory-strategy.md`](docs/17-memory-strategy.md) | Conversation memory taxonomy, condensation mechanics, context-window budget |
| [`docs/18-rag-design.md`](docs/18-rag-design.md) | Retrieval query shape, chunking precision, reranking/caching stance |
| [`docs/19-cost-management.md`](docs/19-cost-management.md) | Cost calculation mechanism, quota management, budget alerting |
| [`docs/20-performance-target.md`](docs/20-performance-target.md) | p99/TTFT/throughput targets, per-node latency budget |
| [`docs/21-event-flow.md`](docs/21-event-flow.md) | Cross-component sequence diagrams; no message bus clarification |
| [`docs/22-error-handling.md`](docs/22-error-handling.md) | Error code registry, retry policy, client retry guidance |
| [`docs/23-configuration.md`](docs/23-configuration.md) | Configuration reference: categorized settings, startup validation checklist |
| [`docs/prompts/architect.md`](docs/prompts/architect.md) | System prompt persona: Architect |
| [`docs/prompts/backend.md`](docs/prompts/backend.md) | System prompt persona: Backend Engineer |
| [`docs/prompts/ai-agent.md`](docs/prompts/ai-agent.md) | System prompt persona: AI Agent Engineer + canonical runtime prompts |
| [`docs/prompts/reviewer.md`](docs/prompts/reviewer.md) | System prompt persona: Reviewer |

## Environment Setup

Copy `.env.example` to `.env` and fill in values (see [`docs/10-deployment.md`](docs/10-deployment.md) for the full variable list), including:

```
DATABASE_URL=
AWS_REGION=ap-southeast-3
BEDROCK_EMBEDDING_MODEL=arn:aws:bedrock:ap-southeast-3:586794442374:inference-profile/global.cohere.embed-v4:0
BEDROCK_TEXT_MODEL=global.anthropic.claude-sonnet-4-6
DOCUMENT_BASE_URL=
SIMILARITY_SCORE_THRESHOLD=0.75
CHUNK_SIZE_TOKENS=700
CHUNK_OVERLAP_TOKENS=100
REDIS_URL=redis://localhost:6379/0
RATE_LIMIT_REQUESTS_PER_MINUTE=30
```

This is a trimmed quick-start subset — the full variable list (Bedrock resilience, retrieval top-k, DB pool sizing, retention, CORS, SSE keepalive, etc.) is in [`docs/10-deployment.md`](docs/10-deployment.md) §3.

## Open Questions / Known Gaps

See [`docs/01-prd.md`](docs/01-prd.md) §11 — notably: precise User vs Visitor definition for analytics, confirming `embed-v4`'s exact max input tokens/output vector dimension, and populating `CORS_ALLOWED_ORIGINS` once a frontend origin exists.
