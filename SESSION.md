# Session Snapshot

## Current Phase

Phase 8 — Session & Message Persistence + Endpoints

## Current Status

DONE

## Completed Tasks

- Repository additions (business-specific methods, per each repo's own "added by the phase that first needs them" convention):
  - `SessionRepository.list_by_user_id` (newest-first, total count) — `GET /api/session`.
  - `MessageRepository.list_by_session_id` (ordered by `created_at`) — `POST /api/messages`.
- `app/errors.py`: added `SessionNotFoundError` (`SESSION_NOT_FOUND`/404, already in the `22-error-handling.md` §2 registry from Phase 7's work — just needed a concrete exception class).
- `app/services/chat_service.py` (new): `resolve_session` (session-resolution rule shared by `/api/chat`/`/api/opr/chat`, Phase 9/10 — auto-create on empty `session_id`, reuse on valid-existing, `SessionNotFoundError` on valid-but-unknown), `persist_message` (creates a message row; on the first `role='user'` message for a session, sets `sessions.title` once — plain truncation to 60 chars, never overwritten afterward), `list_sessions_for_user`/`get_session_messages` (thin service wrappers backing the two endpoints below).
- `app/schemas/session.py`, `app/schemas/message.py` (new): `SessionListItem`/`SessionListResponse`, `MessagesRequest`/`MessageItem`/`MessagesResponse` — field-for-field per `06-api-specification.md` §1/§3.
- `app/api/user_router.py` (new): `GET /api/session` (`user_id` required — blank/whitespace explicitly rejected, `limit`/`offset` optional), `POST /api/messages` (`{"session_id"}` body, `404 SESSION_NOT_FOUND` on unknown). No rate limiting on either (not in Phase 4's rate-limited-endpoint list — `/api/chat`, `/api/opr/chat`, `/api/opr/ingest` only).
- `app/main.py`: wires `user_router`.
- Tests: `tests/integration/test_session_resolution.py` (6 tests — all three resolution cases × both personas, calling `resolve_session` directly per this phase's own "chat endpoints don't exist until Phase 9" note); `tests/integration/test_session_title.py` (3 tests — truncation-to-60-chars, never-overwritten-by-later-messages, short-title-stripped-not-padded); `tests/integration/test_session_endpoints.py` (6 tests — direct HTTP-level coverage of the two new endpoints, added beyond this phase's named Verification files since they're this phase's other Task-list deliverable).
- **Manual verification against the real running app** (same pattern as Phases 5-7): `poetry run uvicorn` against the existing `bravi-db-1` container; a direct test-harness script exercised all three `resolve_session` cases plus `persist_message`'s title-setting (no mocking), then real `curl` calls against `GET /api/session`/`POST /api/messages` confirmed the same harness-created session/messages round-tripped correctly, including the 400/404 error paths. The manually-created session row was deleted afterward via direct repository access (no delete endpoint exists for sessions).

## Remaining Tasks

- None for Phase 8. Next session should begin Phase 9 (User Chat Graph & `/api/chat` (SSE)) — this is the M3/"Core Chat Pipeline" milestone's first half.

## Files Added

- `backend/app/services/chat_service.py`
- `backend/app/schemas/session.py`
- `backend/app/schemas/message.py`
- `backend/app/api/user_router.py`
- `backend/tests/integration/test_session_resolution.py`
- `backend/tests/integration/test_session_title.py`
- `backend/tests/integration/test_session_endpoints.py`

## Files Modified

- `backend/app/errors.py` — added `SessionNotFoundError`.
- `backend/app/repositories/session_repository.py` — `list_by_user_id`.
- `backend/app/repositories/message_repository.py` — `list_by_session_id`.
- `backend/app/main.py` — wires `user_router`.
- `docs/IMPLEMENTATION_PLAN.md` — Phase 8 checkboxes/status/progress table updated to `DONE`; dated note recording implementation decisions (no blocking ambiguities this phase).

## Tests Executed

- `poetry run pytest tests/integration/test_session_resolution.py tests/integration/test_session_title.py tests/integration/test_session_endpoints.py -v` → 15/15 passed, run 3× with no flakiness.
- `poetry run pytest` (full suite) → 155/155 passed, run 3× with no flakiness.
- `poetry run black --check .` / `poetry run ruff check .` / `poetry run mypy app` → all clean.
- Live verification against the real running app (not mocked) — see Completed Tasks' "Manual verification against the real running app" entry.

## Verification Results

All Phase 8 Verification checklist items pass.

## Known Issues

- Carried over from Phase 0: no commit/remote yet, CI still unexercised on GitHub's infrastructure.
- Carried over from Phase 3: live-Bedrock smoke-test credentials are root-account, not a scoped IAM role/user.
- Carried over from Phase 5: this environment's standalone `bravi-db-1` Postgres container still occupies the `db` service's name/port; a from-scratch `docker-compose up` was not re-run this phase either (no new Docker-relevant changes were made, so `docker build`/`docker compose config` were not re-verified this phase — nothing in `Dockerfile`/`docker-compose.yml`/`pyproject.toml` changed).
- Carried over from Phase 7: file-upload validation (MIME allowlist, size limit, malware scanning) is intentionally deferred to Phase 12; `DELETE /api/opr/knowledge/{id}`'s destructive-action log has no real `user_id`.
- **`sessions`/`messages` have no delete/cleanup mechanism yet** — Phase 13's retention job is the first thing that prunes `messages` (by `MESSAGE_RETENTION_DAYS`); `sessions` rows themselves are never pruned at all, per `07-database-design.md` §7 ("`sessions` rows are left in place... so `GET /api/session` history isn't silently truncated to zero"). Not a Phase 8 gap — this is the documented, intentional retention design; noted here only because this phase's own manual-verification session row had to be cleaned up by direct DB access for exactly this reason.
- Two real `knowledge_sources`/`knowledge_documents` rows remain in the shared database from Phase 6's manual verification (`sample.pdf`/`sample-3pp.pdf`) — intentionally left in place, unaffected by this phase's work.

## Architectural Decisions

- **`chat_service.py` centralizes session-resolution and message-persistence logic now, ahead of Phase 9/10 needing it** — `07-database-design.md` §3.1 names `persist_message` as the thing that sets `title`, and Phase 9's task list independently plans a `persist_message` *graph node*; building the real logic here as a plain service function (not a graph node, since `graphs/` may only contain LangGraph node/edge definitions per `11-coding-standard.md` §4) means Phase 9's node becomes a thin wrapper calling this same function instead of reimplementing the set-once rule a second time.
- **No persona-mismatch check in `resolve_session`** — if a `session_id` created under one persona is later passed to the other persona's endpoint, it is still reused as-is. Neither `06-api-specification.md` §2/§5 nor `08-security.md` define this as an error case, so nothing was invented; revisit only if a later phase's reference docs say otherwise.
- **`tests/integration/test_session_endpoints.py` added beyond the two Verification-listed files** — `GET /api/session`/`POST /api/messages` are this phase's own Task-list deliverable (not just `resolve_session`/`persist_message`), so they get direct HTTP-level test coverage; this doesn't weaken or replace either named Verification file, both of which still pass independently.
- **Router-level tests reuse Phase 7's `NullPool`-rebinding fixture pattern** (`app_session_factory`) for the same reason established there: a `TestClient` call drives the ASGI app through its own event loop, and the app's real pooled `AsyncSessionLocal` can otherwise hand back a connection checked out under a different call's loop, which asyncpg rejects.

## Next Recommended Action

Begin Phase 9 — User Chat Graph & `/api/chat` (SSE). Read `docs/05-ai-agent-design.md` §1-§2, `docs/06-api-specification.md` §0/§2, `docs/prompts/ai-agent.md` §1/§3/§4/§5/§7, `docs/08-security.md` §4, `docs/11-coding-standard.md` §7/§8.1, `docs/17-memory-strategy.md`, `docs/20-performance-target.md`, `docs/12-testing-strategy.md` §3 before starting. This is a large phase (full RAG pipeline, short-circuit tiers, streaming) — budget accordingly.
