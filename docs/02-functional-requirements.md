# 02 — Functional Requirements

## 1. Purpose
Defines detailed, testable functional behavior of `bravi-ai-chatbot`, expanding on `01-prd.md`.

## 2. Actors

| Actor | Description |
|---|---|
| User/Visitor | Sends chat text/image, no knowledge management access |
| Operator | Sends chat, manages knowledge, views analytics |
| System (scheduler) | Runs the one-time startup ingestion job |

## 3. Functional Requirement List

### FR-1: List Sessions
- **Endpoint**: `GET /api/session`
- **Input**: `?user_id=string` (required)
- **Behavior**: Returns all sessions belonging to `user_id`. There is no `POST /api/session` — session creation is implicit, handled by FR-2/FR-5 (see below).
- **Output**: `{ "user_id": string, "total": number, "sessions": [ { "session_id", "persona", "title": string | null, "created_at" } ] }` — `title` is set once from the session's first user message (truncated), `null` until then; see `07-database-design.md` §3.1.

### FR-2: Chat (User)
- **Endpoint**: `POST /api/chat`
- **Input**: `{ "session_id"?: string, "question": string, "user_id": string, "file"?: binary/image }`
- **Behavior**:
  1. Resolve session: if `session_id` is omitted/empty, create a new session (`persona="user"`) linked to `user_id`; if provided, look it up — if it exists, continue using it (load its history/context, persist new messages to it); if it does not exist, fail with `404`.
  2. If `file` is an image, pass it as multimodal input directly to the Bedrock text model alongside the question (see FR-11).
  3. Run pre-processing pipeline (see FR-6 short-circuit rules).
  4. If not short-circuited: retrieve relevant knowledge chunks (pgvector similarity search), optionally condense session history, stream the answer via Bedrock text model grounded in retrieved chunks (Markdown, **in Bahasa Indonesia** — see FR-14, with a `## Sources` section appended using `[Link Text](URL)`, mentioning cited-document freshness/versioning where applicable — see FR-12).
  5. Persist user message + assistant response to `messages` table.
  6. Log usage metrics (latency, model, tokens, short-circuit reason if any).
- **Output**: streamed SSE — see `06-api-specification.md` §0/§2. Terminal event: `{ "session_id", "answer", "sources"?: [...], "short_circuited": boolean }`

### FR-3: Get Messages
- **Endpoint**: `POST /api/messages`
- **Input**: `{ "session_id": string }`
- **Behavior**: Returns ordered message history for the session.
- **Output**: `{ "session_id", "messages": [ { "role", "content", "created_at" } ] }`

### FR-4: Trending Questions
- **Endpoint**: `GET /api/trending`
- **Behavior**: Returns top-N most frequently asked questions/topics over a rolling window (e.g., last 7 days), aggregated/normalized (e.g., clustered by semantic similarity or exact-normalized text).
- **Output**: `{ "trending": [ { "question": string, "count": number } ] }`

### FR-5: Operator Chat
- **Endpoint**: `POST /api/opr/chat`
- **Input**: `{ "session_id"?: string, "question": string, "user_id": string }`
- **Behavior**: Same session resolution rule as FR-2 (auto-create if `session_id` empty, `404` if provided-but-unknown, `persona="operator"`). Same pipeline as `/api/chat`, plus:
  - **Knowledge-summary intent** — if the operator's question is a summarization request (e.g., "summarize everything about X"), the agent routes to a summary sub-flow that retrieves a broader set of chunks and produces a structured summary instead of a direct short answer.
  - **Add-knowledge intent** — if the operator's question matches an add-knowledge trigger phrase (bilingual, e.g. "tambah knowledge ai", "add ai knowledge"), skip retrieval/generation entirely and return the fixed template `Silahkan klik tombol berikut untuk mengisi form: <BTN>Add Knowledge</BTN>` (`short_circuited: true`, `short_circuit_reason: "add_knowledge_intent"`). This is Operator-only — `classify_add_knowledge_intent` does not exist in the User chat path (FR-2), so `/api/chat` can never return this template. See `05-ai-agent-design.md` §2.2/§2.3.
- **Output**: streamed SSE — see `06-api-specification.md` §0/§5. Terminal event: `{ "session_id", "answer", "sources"?, "mode": "qa" | "summary" | null }` (`mode` is `null` on any short-circuited response, including add-knowledge intent).

### FR-6: Cost-Control Short-Circuit Pipeline
Applies to both `/api/chat` and `/api/opr/chat`, executed **in this order**, each step short-circuits (skips remaining steps) on match:

1. **Greeting/small-talk classifier** (regex/keyword or lightweight local classifier — no LLM call) → return canned response.
2. **Out-of-topic detection** → run cheaply (e.g., embedding-similarity against a set of "in-domain topic" anchors, or a small classifier) → return canned "out of scope" response **before** the expensive generation step.
3. **Similarity-score threshold** → one embedding call for the question, pgvector top-k search; if best score < `SIMILARITY_SCORE_THRESHOLD` → return canned "no relevant knowledge found" response. **No LLM generation call is made.**
4. If none matched → proceed to full RAG (condense context if history exists → call Bedrock text model with retrieved chunks + condensed history).

### FR-7: Ingest Knowledge (Operator, on-demand)
- **Endpoint**: `POST /api/opr/ingest`
- **Input**: multipart file (PDF/doc) **or** `{ "text": string, "title"?: string }`, plus optional `valid_until` (date) and `supersedes_document_id` (uuid) — see FR-12.
- **Behavior**: Extract text (if file) → chunk → embed via Bedrock embedding model → store vectors + metadata in pgvector → record ingestion job status. If `supersedes_document_id` is provided, set that existing document's `superseded_by_document_id` to the newly created document's id.
- **Output**: `{ "knowledge_id", "status": "queued" | "processing" | "completed" | "failed", "chunks_ingested"?: number }`
- Recommended to run as a background task/job if file is large (see `04-system-architecture.md`).

### FR-8: List Ingested Knowledge (Operator)
- **Endpoint**: `GET /api/opr/knowledge`
- **Behavior**: Returns list of ingested documents with metadata: id, title/source, type (file/text), ingested_at, status, chunk_count, freshness/versioning metadata.
- **Output**: `{ "knowledge": [ { "id", "title", "source_type", "ingested_at", "status", "chunk_count", "valid_until", "superseded_by_document_id" } ] }`

### FR-9: Operator Analytics
- **Endpoint**: `GET /api/opr/analytics`
- **Behavior**: Aggregates usage metrics:
  - Most-asked questions (`top_questions.user` — non-role calculation, counts questions from both User and Operator sessions together, not split by persona)
  - Volume of chats over time
  - Average latency (overall and per short-circuit tier)
  - Model usage breakdown (embedding-only vs full generation)
  - Estimated token cost
- **Output**: structured JSON payload (defined fully in `06-api-specification.md`)

### FR-10: Startup Ingestion Job
- **Trigger**: Runs once at application startup (or via one-off CLI/management command), controlled by `INGESTION_RUN_ONCE` flag and a persisted "has this run" marker to avoid re-running on every restart.
- **Behavior**:
  1. Query DB table of known documents (with relative link/path).
  2. For each: build full URL as `f"{DOCUMENT_BASE_URL}/{relative_path}"`.
  3. Download PDF.
  4. Extract text, chunk, embed, store — same pipeline as FR-7.
  5. Mark each source as ingested (idempotency: skip if already marked ingested, or content-hash unchanged).
  6. Log failures per-document without aborting the whole batch.

### FR-11: Multimodal (Image) Input
- Applies to `/api/chat` and `/api/opr/chat`.
- Behavior: If `file` is an image, it is passed as multimodal input directly to the AWS Bedrock text model (vision-capable) alongside `question` — no separate captioning/OCR model or service. See `05-ai-agent-design.md` §2.3.

### FR-12: Answer Freshness / Versioning Awareness
- Applies to `/api/chat` and `/api/opr/chat` (full-RAG path only — not short-circuited responses).
- **Input**: no new client-facing input; sourced from `knowledge_documents.valid_until`/`superseded_by_document_id`, set at ingest time (FR-7).
- **Behavior**: when a retrieved chunk's source document has `valid_until` and/or `superseded_by_document_id` set, the generated answer may mention this naturally (e.g., that the cited information might be outdated or has been replaced by a newer document). When neither field is set on any cited document, the answer says nothing about freshness/versioning — the model must never speculate about expiry or replacement for a document that carries no such metadata. See `05-ai-agent-design.md` §4.
- **Output**: reflected both in the answer's prose (conditionally) and structurally in each `sources[]` entry (`valid_until`, `superseded_by_title`, both nullable) — see `06-api-specification.md` §0.

### FR-13: Delete Knowledge (Operator)
- **Endpoint**: `DELETE /api/opr/knowledge/{knowledge_id}`
- **Behavior**: Hard-deletes the document and its chunks/vectors (immediately excluded from retrieval for every session). If the document was startup-managed, resets `knowledge_sources.is_ingested = false` (may be re-ingested on the next startup run unless the source entry is also removed — documented trade-off, see `07-database-design.md` §5a). Logged as a destructive action.
- **Output**: `{ "knowledge_id", "status": "deleted", "chunks_removed": number }`; `404` if `knowledge_id` doesn't exist (including already-deleted).

### FR-14: Response Language (Bahasa Indonesia)
- Applies to `/api/chat` and `/api/opr/chat` — every generated answer (QA, summary) and every canned/short-circuit response (greeting, out-of-topic, no-knowledge-found, add-knowledge-intent).
- **Behavior**: all responses are in Bahasa Indonesia, regardless of the language `question` was written in. Enforced via the system prompts themselves (see `docs/prompts/ai-agent.md`) and via canned-response templates authored directly in Bahasa Indonesia — not a runtime translation step.

## 4. Functional Requirements Traceability Table

| FR | Endpoint(s) | Priority |
|---|---|---|
| FR-1 | `/api/session` | Must |
| FR-2 | `/api/chat` | Must |
| FR-3 | `/api/messages` | Must |
| FR-4 | `/api/trending` | Must |
| FR-5 | `/api/opr/chat` | Must |
| FR-6 | `/api/chat`, `/api/opr/chat` | Must |
| FR-7 | `/api/opr/ingest` | Must |
| FR-8 | `/api/opr/knowledge` | Must |
| FR-9 | `/api/opr/analytics` | Must |
| FR-10 | (startup job) | Must |
| FR-11 | `/api/chat`, `/api/opr/chat` | Should |
| FR-12 | `/api/chat`, `/api/opr/chat` | Should |
| FR-13 | `/api/opr/knowledge/{id}` (DELETE) | Must |
| FR-14 | `/api/chat`, `/api/opr/chat` | Must |
