# 14 — Bedrock Integration

## 1. Scope

This document covers the *mechanics* of talking to AWS Bedrock: SDK usage, streaming protocol, credential resolution, error/retry classification, circuit-breaker internals, and model abstraction. Settings governing timeout/retry/output-cap/model IDs are already defined in `10-deployment.md` §3 and their qualitative behavior in `11-coding-standard.md` §12 — this document does not restate those defaults, only explains how `clients/bedrock_client.py` uses them and fills in details (credential order, error taxonomy, circuit-breaker thresholds) that were referenced but never made concrete.

## 2. Client Architecture

- `clients/bedrock_client.py` is the **only** place `boto3`'s `bedrock-runtime` client is constructed (`11-coding-standard.md` §4/§12) — no node, service, or router may instantiate its own.
- Exposes exactly two logical operations to the rest of the codebase: `embed(texts: list[str]) -> list[list[float]]` and `generate_stream(prompt: PromptPayload, **params) -> AsyncIterator[str]`. Both are consumed only from `graphs/nodes/*` — never called directly from `api/`, `services/`, or `tools/`.
- Neither operation accepts a raw model ID from the caller — the purpose (`embedding` vs `text`) is passed, and the client resolves it to the configured model ID internally (see `15-model-management.md` §2).

## 3. Credential Resolution

- Uses `boto3`'s default credential provider chain as-is — no custom resolution logic in application code.
- **Local/dev**: `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` from `.env`.
- **Staging/production**: an attached IAM role (ECS task role / EKS IRSA / equivalent) is preferred over static keys — least-privilege, no long-lived credentials to rotate or leak (`08-security.md` §5). Static keys should not be present in a deployed environment when a role is available; `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` remain in the config schema only for local development.
- `AWS_REGION` is fixed per deployment (`ap-southeast-3`) — no per-request region override exists.

## 4. Streaming Protocol

- `generate_answer`, `generate_summary`, and `condense_history` invoke Bedrock's streaming response API for `BEDROCK_TEXT_MODEL`, which yields incremental output chunks as they're generated.
- Each chunk is decoded inside `bedrock_client.generate_stream` and yielded into the LangGraph `astream(...)` call (`11-coding-standard.md` §7); the API layer relays each yielded chunk 1:1 as an SSE `token` event (`06-api-specification.md` §0) — no additional buffering or batching is introduced between Bedrock's stream and the client's stream.
- Embedding calls (`embed_question`, `embed_chunks`) are **not** streamed — a single synchronous request/response per call (or per batch, see `EMBEDDING_BATCH_SIZE`), since an embedding is a fixed-size vector output with nothing to stream incrementally.
- If the underlying Bedrock stream errors partway through (after some chunks were already yielded and relayed as `token` events), `bedrock_client` does not attempt to resume or replay — it surfaces the error to the graph, which emits a terminal SSE `error` event (`06-api-specification.md` §0, `22-error-handling.md` §2) instead of `done`. Whatever partial text was already streamed to the client stays displayed as-is; there is no retroactive retraction. Partial output is **not** persisted to `messages` — `persist_message` only runs on a successful `done`.

## 5. Error Taxonomy & Retry Classification

`11-coding-standard.md` §12 states retries apply "only for transient/throttling errors, never validation errors" — this is the concrete mapping `bedrock_client.py` implements:

| Bedrock/`boto3` exception | Retryable? | Handling |
|---|---|---|
| `ThrottlingException` | Yes | Retried with backoff up to `BEDROCK_MAX_RETRIES` |
| `ModelTimeoutException` / socket read timeout | Yes | Retried; each attempt still bounded by `BEDROCK_TIMEOUT_SECONDS` |
| `InternalServerException` | Yes | Retried — transient AWS-side fault |
| `ModelNotReadyException` | Yes | Retried — on-demand model still warming up |
| `ValidationException` | No | Fails immediately — malformed request/prompt is a bug, not a transient condition; retrying would just fail identically |
| `AccessDeniedException` | No | Fails immediately, logged at `ERROR` — an IAM/config problem requiring human intervention, not something retry-with-backoff can fix |
| `ModelStreamErrorException` (mid-stream) | No | Not retried mid-stream (see §4) — surfaces as an `error` event |

## 6. Circuit Breaker Mechanics

`11-coding-standard.md` §12 describes the circuit breaker qualitatively ("after a sustained run of failures, trips open and fails fast... cooldown window") without concrete thresholds. Two settings fill that gap (new — not previously defined; see `23-configuration.md` §3 and patched into `10-deployment.md` §3):

- `BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD` (default `5`) — number of consecutive retry-exhausted failures before the circuit trips from `closed` to `open`.
- `BEDROCK_CIRCUIT_BREAKER_COOLDOWN_SECONDS` (default `30`) — how long the circuit stays `open` (failing every call immediately, no `boto3` call attempted) before allowing a single `half-open` probe request through.

State machine: `closed` (normal) → `open` (trips after `BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD` consecutive failures; every call fails fast with `BEDROCK_UNAVAILABLE` for `BEDROCK_CIRCUIT_BREAKER_COOLDOWN_SECONDS`) → `half-open` (one probe call allowed) → back to `closed` on success or `open` again on failure. State is exposed via the `bedrock_circuit_breaker_state` gauge (`09-observability.md` §5) and drives the `BEDROCK_UNAVAILABLE` error code (`22-error-handling.md` §2/§6).

## 7. Model Abstraction

- `bedrock_client.py` takes a *purpose* (`embedding` / `text`), never a raw model ARN, from its callers — the ARN itself is resolved from config. See `15-model-management.md` §2 for how model IDs are organized.
- Both `BEDROCK_EMBEDDING_MODEL` and `BEDROCK_TEXT_MODEL` are configured as cross-region inference profile ARNs (the `global.` prefix) — AWS routes the request to whichever Bedrock region has capacity. The application does not implement its own regional failover or multi-region retry logic; that responsibility is delegated entirely to the inference profile.

## 8. Multimodal Input

Cross-reference only, no new behavior: `preprocess_input` attaches image bytes and the text question as two blocks of a single Bedrock multimodal message — one `generate` call, not a separate captioning step. See `05-ai-agent-design.md` §2.3.

## 9. Testing

Cross-reference only: `clients/bedrock_client.py` unit tests (timeout/retry/circuit-breaker state transitions against a mocked `boto3` client) are specified in `12-testing-strategy.md` §2 — nothing new to add here.
