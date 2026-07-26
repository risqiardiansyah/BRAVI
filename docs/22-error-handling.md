# 22 — Error Handling & Retry Policy

## 1. Scope

Default error handling and retry policy across the system. The JSON error shape (`{ "error": { "code", "message" } }`) and the HTTP status code summary already exist (`11-coding-standard.md` §6, `06-api-specification.md` §10) — not repeated here. This document adds four things that were referenced but never made concrete: the actual `code` value registry, the retry policy for non-Bedrock failures, `Idempotency-Key` conflict handling, and client-facing retry guidance.

## 2. Error Code Registry (gap fill — new)

`06-api-specification.md` §0 defines `code`/`message` as fields on the SSE `error` event (and the same shape is used for plain-JSON pre-stream error bodies) but never enumerated actual `code` values. Authoritative registry:

| `code` | Status (pre-stream) / channel (mid-stream) | Meaning |
|---|---|---|
| `INVALID_REQUEST` | 400 | Missing/invalid `question` or `user_id`, or malformed body |
| `SESSION_NOT_FOUND` | 404 | Provided `session_id` does not exist |
| `KNOWLEDGE_NOT_FOUND` | 404 | `DELETE /api/opr/knowledge/{id}` — unknown or already-deleted id |
| `FILE_TOO_LARGE` | 413 | Exceeds `MAX_IMAGE_UPLOAD_MB`/`MAX_FILE_UPLOAD_MB` |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | MIME type not in the allowlist (`08-security.md` §3) |
| `MALWARE_DETECTED` | 415 | File failed content scanning (`08-security.md` §8a) |
| `IDEMPOTENCY_KEY_CONFLICT` | 409 | Same `Idempotency-Key` reused with different content (§4 below) |
| `RATE_LIMITED` | 429 | Redis-backed limiter tripped (`08-security.md` §6) |
| `BEDROCK_TIMEOUT` | 502 (pre-stream) / SSE `error` (mid-stream) | Bedrock call exceeded `BEDROCK_TIMEOUT_SECONDS` after exhausting `BEDROCK_MAX_RETRIES` |
| `BEDROCK_UNAVAILABLE` | 503 (pre-stream) / SSE `error` (mid-stream) | Circuit breaker `open` (`14-bedrock-integration.md` §6) — fails fast, no Bedrock call attempted |
| `INTERNAL_ERROR` | 500 | Unhandled exception — stack trace/internal message never leaked when `APP_ENV=production` (`11-coding-standard.md` §6) |

(`06-api-specification.md` §0 is patched with a one-line cross-reference to this table rather than duplicating it there.)

## 3. Retry Policy by Failure Class

- **Bedrock calls**: fully specified already — bounded retry-with-backoff + circuit breaker (`11-coding-standard.md` §12, `14-bedrock-integration.md` §5–§6). No change here.
- **Database calls (new — not previously specified)**: per-request DB errors (e.g., a query failing mid-transaction) are **not** retried automatically — fail fast, return `INTERNAL_ERROR`/500. Rationale: unlike a stateless Bedrock read/generate call, blindly retrying a DB write (e.g., `persist_message`) risks a duplicate write if the original attempt actually succeeded before the error surfaced (e.g., a network blip immediately after commit) — the safer default is to surface the error rather than silently double-write. The one exception is the **initial DB connection at startup**, which retries with backoff before the app reports itself unready (`GET /health/ready`, `06-api-specification.md` §9.2) — connecting is naturally idempotent; an in-flight query isn't.
- **Ingestion node failures**: already specified — isolated per-document, no automatic retry; re-running the startup job or re-submitting via `/api/opr/ingest` is the retry mechanism (`05-ai-agent-design.md` §3.2). No change here.

## 4. Idempotency-Key Conflict (gap fill — new)

`06-api-specification.md` §6 only covers a retry with the *same* content under the same `Idempotency-Key`. A previously-undefined case: the same key reused with **different** file/text/title. This is a client bug — key reuse implies "this is a retry of the same logical request" — and the server must neither (a) silently return the old result for genuinely different content, nor (b) silently ingest it as a new document under a key that claims to be a retry. Resolution: compare the new request's content-hash against the hash stored for that key; on mismatch, respond `409 Conflict`, `code: IDEMPOTENCY_KEY_CONFLICT`, and perform no ingestion.

## 5. Client Retry Guidance (gap fill — new)

No document previously stated what a client should safely auto-retry:

| Situation | Safe to auto-retry? |
|---|---|
| `GET`/read endpoints (`/api/session`, `/api/messages`, `/api/opr/knowledge`, `/api/opr/analytics`) on 5xx | Yes — read-only, no side effect |
| `POST /api/chat`/`/api/opr/chat` failing **before** the stream opens (plain JSON 4xx/5xx) | Yes — no side effect occurred yet |
| `POST /api/chat`/`/api/opr/chat` failing **after** at least one `token` event was received | **No** — a partial assistant message may already reflect real generation. Client should surface what was streamed and let the user re-ask rather than auto-retrying, to avoid a duplicate turn |
| `POST /api/opr/ingest` on timeout/dropped connection | Yes — but must reuse the same `Idempotency-Key` (`06-api-specification.md` §6) |
| `DELETE /api/opr/knowledge/{id}` on timeout | Yes — deletion is naturally idempotent; a `404` on the retried call is an expected outcome, not an error to alarm on |

## 6. Degraded-Mode Behavior (gap fill — new)

When the Bedrock circuit breaker is `open`, every request that would otherwise call Bedrock text-generation fails fast with `BEDROCK_UNAVAILABLE` instead of individually queuing/timing out (`14-bedrock-integration.md` §6) — a deliberate fail-fast posture, not a bug to be "fixed" into a longer timeout. Short-circuited responses (greeting, out-of-topic, add-knowledge-intent) are unaffected, since they never call Bedrock at all — a circuit-breaker trip degrades the system to "short-circuits still work, generation is temporarily unavailable," not a full outage.
