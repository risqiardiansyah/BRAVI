# 06 — API Specification

Base URL (example): `https://{host}/api`
Content-Type: `application/json` unless noted (multipart for file uploads, streaming for chat responses — see §0).
Auth: **None in Phase 1** (no login/RBAC — see `01-prd.md`).

---

## 0. Streaming Chat Responses (`/api/chat`, `/api/opr/chat`)

Both chat endpoints **always** stream the answer as it's generated, as **Server-Sent Events** (`Content-Type: text/event-stream`) — this is the only supported wire format (no NDJSON, no buffered JSON alternative).

Every SSE `data:` line is a JSON object using **one single, fixed structure** — same fields in every event, regardless of `type`. Fields not applicable to a given `type` are present but `null`. Clients can therefore parse every line with one schema instead of branching on `type` first.

```json
{
  "type": "token | done | error",
  "session_id": "uuid",
  "content": "string | null",
  "answer": "string | null",
  "sources": "array | null",
  "short_circuited": "boolean | null",
  "short_circuit_reason": "string | null",
  "mode": "string | null",
  "code": "string | null",
  "message": "string | null"
}
```

| Field | Type | Populated on | Description |
|---|---|---|---|
| `type` | string | always | `"token"` \| `"done"` \| `"error"` |
| `session_id` | uuid | always | resolved session id (see §2/§5 for auto-create/reuse rules) |
| `content` | string \| `null` | `token` only | partial Markdown text chunk; concatenate in order to reconstruct the answer |
| `answer` | string \| `null` | `done` only | full Markdown answer, including the trailing `## Sources` section |
| `sources` | array \| `null` | `done` only | `[ { "document_id", "title", "url", "page", "valid_until", "superseded_by_title" } ]` — structured form of the same citations appended to `answer`. `valid_until`/`superseded_by_title` are `null` unless set on that document (see `07-database-design.md` §5b) — useful for a frontend badge even when the model's prose doesn't mention it. |
| `short_circuited` | boolean \| `null` | `done` only | whether a short-circuit tier (greeting/out-of-topic/low-similarity) answered instead of full RAG |
| `short_circuit_reason` | string \| `null` | `done` only | short-circuit tier name, or `null` if not short-circuited |
| `mode` | string \| `null` | `done` only, `/api/opr/chat` only | `"qa"` \| `"summary"` |
| `code` | string \| `null` | `error` only | machine-readable error code — full registry in `22-error-handling.md` §2 |
| `message` | string \| `null` | `error` only | human-readable error message |

Example stream for `/api/chat`:
```
data: { "type": "token", "session_id": "uuid", "content": "Here", "answer": null, "sources": null, "short_circuited": null, "short_circuit_reason": null, "mode": null, "code": null, "message": null }

data: { "type": "token", "session_id": "uuid", "content": " is what I found.", "answer": null, "sources": null, "short_circuited": null, "short_circuit_reason": null, "mode": null, "code": null, "message": null }

data: { "type": "done", "session_id": "uuid", "content": null, "answer": "Here is what I found.\n\n## Sources\n- [Doc Title](https://example.com/doc)", "sources": [ { "document_id": "uuid", "title": "Doc Title", "url": "https://example.com/doc", "page": 3, "valid_until": null, "superseded_by_title": null } ], "short_circuited": false, "short_circuit_reason": null, "mode": null, "code": null, "message": null }
```

- `token` events are emitted incrementally as the Bedrock text model streams output.
- Exactly one terminal event (`done` or `error`) closes the stream.
- The **answer body is Markdown** (headings/lists/bold/links as needed for readability).
- When the answer cites knowledge base content, a `## Sources` section is appended at the **end of `answer` itself** (not just the `sources` field), with one Markdown link per citation: `[Link Text](URL)`.
- Short-circuited responses (greeting/out-of-topic/no-knowledge-found/add-knowledge-intent) are still streamed — typically as a single `token` event followed by `done` — for a consistent client integration path.
- `mode` is only ever non-null on `/api/opr/chat` `done` events reached via `generate_answer`/`generate_summary`; it is `null` on **every** short-circuited response (both endpoints) and always `null` on `/api/chat`.
- **Language**: `answer`/`content` are always in **Bahasa Indonesia**, regardless of the language `question` was asked in — this applies to every generated and canned response on both endpoints.
- **`<BTN>Label</BTN>` exception**: the one non-Markdown element that can appear in `answer` is the Operator-only add-knowledge-intent template (`06-api-specification.md` §5), which contains a literal `<BTN>Add Knowledge</BTN>` tag — a custom directive for the frontend to render an actionable button, not real HTML/CommonMark and not something the client should attempt to render as-is. Every other response is plain Markdown.
- When the answer includes a citation to a document with `valid_until`/`superseded_by` metadata set, the model may mention this naturally in the answer text — it never does so for a document without that metadata (see `05-ai-agent-design.md` §4).
- **Keepalive**: during a long `generate_answer`/`generate_summary` call, the server sends a bare SSE comment ping (`: keepalive\n\n` — not a `data:` line, ignored by clients) every `SSE_KEEPALIVE_INTERVAL_SECONDS`, so idle load balancers/proxies don't kill the connection mid-generation. Response is sent with `X-Accel-Buffering: no` so intermediary reverse proxies (e.g., nginx) don't buffer the stream. See `10-deployment.md` §4.2 for the corresponding LB idle-timeout requirement.

---

## 1. `GET /api/session`

List all sessions belonging to a given `user_id`. There is no `POST /api/session` — sessions are created implicitly by `/api/chat` / `/api/opr/chat` (see §2/§5).

**Query params**: `?user_id=string` (required), `?limit=50&offset=0` (optional, default `limit=50`)

**Response `200`**
```json
{
  "user_id": "string",
  "total": 3,
  "sessions": [
    { "session_id": "uuid", "persona": "user", "title": "string | null", "created_at": "2026-07-26T10:00:00Z" }
  ]
}
```
`title` is set once, from the session's first user message (truncated ~60 chars) — see `07-database-design.md` §3.1. It is `null` for a brand-new session with no messages yet, and not user-editable in Phase 1.

**Error responses**
| Code | Reason |
|---|---|
| 400 | Missing `user_id` |

---

## 2. `POST /api/chat`

Send a chat message as a User. Response is **always streamed as SSE** — see §0 for the event format.

**Request** (multipart/form-data if `file` present, else JSON)
```json
{
  "session_id": "uuid (optional)",
  "question": "string",
  "user_id": "string",
  "file": "binary (optional, image)"
}
```

**Session resolution** (applies identically to `/api/opr/chat`, §5):
- If `session_id` is omitted/empty, the system creates a new session (`persona="user"`, linked to `user_id`) and uses it for this request and the response.
- If `session_id` is provided, the system looks it up first: if it exists, the request continues using that session (message history/context loaded from it, new messages persisted to it); if it does **not** exist, the request fails with `404` (no silent auto-create for a client-supplied, non-existent `session_id` — see error table below).

If `file` is an image, it is passed directly to the AWS Bedrock multimodal (vision-capable) text model alongside the question — no separate captioning/OCR service is used. See `05-ai-agent-design.md` §2.3.

**Response** — streamed SSE using the unified event structure defined in §0. Terminal `done` event:
```json
{
  "type": "done",
  "session_id": "uuid",
  "content": null,
  "answer": "string (markdown, in Bahasa Indonesia, with trailing ## Sources section using [Link Text](URL))",
  "sources": [
    { "document_id": "uuid", "title": "string", "url": "string", "page": 3, "valid_until": "2026-12-31 | null", "superseded_by_title": "string | null" }
  ],
  "short_circuited": false,
  "short_circuit_reason": null,
  "mode": null,
  "code": null,
  "message": null
}
```

**Error responses**
| Code | Reason |
|---|---|
| 400 | Missing/invalid `question` or `user_id` |
| 404 | `session_id` provided but does not exist |
| 413 | File too large |
| 415 | Unsupported file type |
| 500 | Internal/Bedrock error |

`404` on a bad `session_id` is returned before the stream opens (plain JSON error body, not an SSE event). Errors after the stream has started are delivered as an `error` event (see §0) rather than an HTTP error status, since headers are already committed.

---

## 3. `POST /api/messages`

Fetch message history for a session.

**Request**
```json
{ "session_id": "uuid" }
```

**Response `200`**
```json
{
  "session_id": "uuid",
  "messages": [
    { "role": "user", "content": "string", "created_at": "2026-07-26T10:00:00Z" },
    { "role": "assistant", "content": "string", "created_at": "2026-07-26T10:00:03Z" }
  ]
}
```

---

## 4. `GET /api/trending`

Get trending questions (public/User-facing).

**Query params (optional)**: `?limit=10&window_days=7`

**Response `200`**
```json
{
  "window_days": 7,
  "trending": [
    { "question": "string", "count": 42 }
  ]
}
```

---

## 5. `POST /api/opr/chat`

Operator chat, including knowledge-summary mode. Response is **always streamed as SSE** — see §0 for the event format. Same multimodal handling as `/api/chat` applies if an image is attached.

**Request**
```json
{
  "session_id": "uuid (optional)",
  "question": "string",
  "user_id": "string"
}
```

**Session resolution**: identical rule to `/api/chat` (§2) — empty/missing `session_id` auto-creates a new session (`persona="operator"`); a provided `session_id` must already exist or the request fails with `404`.

**Response** — streamed SSE using the unified event structure defined in §0. Terminal `done` event:
```json
{
  "type": "done",
  "session_id": "uuid",
  "content": null,
  "answer": "string (markdown, in Bahasa Indonesia, with trailing ## Sources section using [Link Text](URL))",
  "sources": [ { "document_id": "uuid", "title": "string", "url": "string", "page": 3, "valid_until": "2026-12-31 | null", "superseded_by_title": "string | null" } ],
  "short_circuited": false,
  "short_circuit_reason": null,
  "mode": "qa",
  "code": null,
  "message": null
}
```
`mode` is `"summary"` when the operator's question is routed to the summarization sub-flow; it is `null` whenever `short_circuited` is `true` (neither QA nor summary generation ran).

**Add-knowledge intent**: if `question` matches an add-knowledge trigger phrase (bilingual, e.g. "tambah knowledge ai", "add ai knowledge" — see `05-ai-agent-design.md` §2.3), the response is short-circuited (no Bedrock call, `sources: null`) with the fixed template:
```json
{
  "type": "done", "session_id": "uuid", "content": null,
  "answer": "Silahkan klik tombol berikut untuk mengisi form: <BTN>Add Knowledge</BTN>",
  "sources": null, "short_circuited": true, "short_circuit_reason": "add_knowledge_intent",
  "mode": null, "code": null, "message": null
}
```
This is Operator-only — `/api/chat` never returns this template regardless of what the User asks, since `classify_add_knowledge_intent` only exists in `operator_chat_graph` (`11-coding-standard.md` §8.1).

**Error responses**
| Code | Reason |
|---|---|
| 400 | Missing/invalid `question` or `user_id` |
| 404 | `session_id` provided but does not exist |
| 500 | Internal/Bedrock error |

---

## 6. `POST /api/opr/ingest`

Ingest knowledge from a file or raw text.

**Request** (multipart/form-data)
```
file: binary (optional, PDF)
text: string (optional, raw text — required if no file)
title: string (optional)
valid_until: date, ISO 8601 (optional) — see 07-database-design.md §5b
supersedes_document_id: uuid (optional) — marks the referenced existing document as superseded by this upload
```
Exactly one of `file` or `text` must be provided.

**Optional `Idempotency-Key` header**: if a client retries the same upload after a timeout/dropped response, pass the same `Idempotency-Key` to avoid double-ingesting — the server checks it (alongside the content hash stored in `knowledge_documents.content_hash`, see `07-database-design.md` §5c) and returns the original `knowledge_id`/`status` instead of starting a duplicate job.

**Response `202 Accepted`**
```json
{
  "knowledge_id": "uuid",
  "status": "queued"
}
```

---

## 7. `GET /api/opr/knowledge`

List ingested knowledge documents.

**Query params (optional)**: `?status=completed&limit=50&offset=0`

**Response `200`**
```json
{
  "total": 128,
  "knowledge": [
    {
      "id": "uuid",
      "title": "string",
      "url": "string",
      "source_type": "file",
      "ingested_at": "2026-07-20T08:00:00Z",
      "status": "completed",
      "chunk_count": 42,
      "valid_until": "2026-12-31 | null",
      "superseded_by_document_id": "uuid | null"
    }
  ]
}
```

---

## 7.1 `DELETE /api/opr/knowledge/{knowledge_id}`

Permanently delete an ingested document and its chunks/vectors from the knowledge base. Operator-only; not reachable from any User-persona path (`11-coding-standard.md` §8.1).

**Response `200`**
```json
{
  "knowledge_id": "uuid",
  "status": "deleted",
  "chunks_removed": 42
}
```

**Behavior** (full detail in `07-database-design.md` §5a):
- Hard-deletes `knowledge_documents` (cascades to `knowledge_chunks`) — the document stops being retrievable immediately, mid-conversation, for any session.
- If the document was startup-managed (has a `source_id`), the corresponding `knowledge_sources.is_ingested` is reset to `false` — **the next startup ingestion run will re-ingest it** unless the source entry itself is also removed. This is a deliberate design trade-off, not a bug — see the linked section for the reasoning.
- Logged as a destructive action (`user_id`, `knowledge_id`, `title`, `chunks_removed`) per `09-observability.md`.

**Error responses**
| Code | Reason |
|---|---|
| 404 | `knowledge_id` does not exist (including: already deleted) |
| 500 | Internal/DB error |

---

## 8. `GET /api/opr/analytics`

**Query params (optional)**: `?from=2026-07-01&to=2026-07-26`

**Response `200`**
```json
{
  "period": { "from": "2026-07-01", "to": "2026-07-26" },
  "top_questions": {
    "user": [ { "question": "string", "count": 48 } ]
  },
  "volume": { "total_chats": 512, "by_day": [ { "date": "2026-07-25", "count": 40 } ] },
  "latency": { "p50_ms": 850, "p95_ms": 4200 },
  "model_usage": {
    "embedding_calls": 900,
    "text_generation_calls": 300,
    "short_circuited_pct": 41.2
  },
  "estimated_cost_usd": 12.34
}
```

---

## 9. System / Operational Endpoints

Not persona-specific; used by the container orchestrator, load balancer, and monitoring stack. See `09-observability.md` for full detail.

### 9.1 `GET /health`

Liveness probe — process is up. No dependency checks.

**Response `200`**: `{ "status": "ok" }`

### 9.2 `GET /health/ready`

Readiness probe — checks DB connection and Redis reachability (lightweight ping, not a full query), plus Bedrock reachability (lightweight, not a real inference call). Used to gate traffic during startup/rolling deploys (see `10-deployment.md` §4).

**Response `200`**: `{ "status": "ready", "checks": { "database": "ok", "redis": "ok", "bedrock": "ok" } }`
**Response `503`**: same shape, failing checks reported as `"error"` instead of `"ok"` — orchestrator should not route traffic.

### 9.3 `GET /metrics`

Prometheus-format metrics exposition (`chat_requests_total`, `chat_latency_ms`, `bedrock_*_calls_total`, `estimated_cost_usd_total`, `ingestion_jobs_total`, etc. — full list in `09-observability.md` §5). Not exposed publicly in production (internal network/scrape-target only).

**Response `200`**: `text/plain; version=0.0.4` (Prometheus exposition format).

---

## 10. Status Codes Summary

| Code | Meaning |
|---|---|
| 200 | Success |
| 202 | Accepted (async ingestion queued) |
| 400 | Bad request / validation error |
| 404 | Session or resource not found |
| 409 | `Idempotency-Key` reused with different content (`IDEMPOTENCY_KEY_CONFLICT`, see `22-error-handling.md` §4) |
| 413 | Payload/file too large |
| 415 | Unsupported media type |
| 429 | Rate limited — Redis-backed limiter tripped (`RATE_LIMIT_REQUESTS_PER_MINUTE`/`RATE_LIMIT_BURST`, see `08-security.md` §6) |
| 500 | Internal server error |
| 502/504 | Upstream (Bedrock) error/timeout |
| 503 | `/health/ready` reporting a failing dependency check, or the Bedrock circuit breaker is `open` (`BEDROCK_UNAVAILABLE`, see `14-bedrock-integration.md` §6) |

Full `code` value registry for every row above: `22-error-handling.md` §2.

## 11. Versioning

Phase 1 ships unversioned under `/api`. If breaking changes are needed later, introduce `/api/v2/...` and deprecate old paths with notice.
