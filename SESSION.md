# Session Snapshot

## Current Phase

Phase 9 — User Chat Graph & `/api/chat` (SSE)

## Current Status

DONE

## Completed Tasks

- `app/graphs/chat_state.py` (new): `ChatState` TypedDict extending `05-ai-agent-design.md` §2.1's conceptual schema — adds `original_question` (untouched by image-description merging, for message/analytics persistence), `TopMatch`/`SourceItem` TypedDicts, and model-usage/timing bookkeeping fields for `log_metrics`.
- `app/graphs/prompts.py` (new): canonical QA system prompt (`docs/prompts/ai-agent.md` §1, verbatim) + history-condensation prompt (§7, verbatim) + `render_context` (attaches `valid_until`/`superseded_by_title` only when actually set) + an internal (non-canonical, not user-facing) image-description prompt for `preprocess_input`.
- `app/graphs/canned_responses.py` (new): greeting/out-of-topic/no-knowledge-found canned text (§3-§5, verbatim) as Python constants (not a DB-backed config table — none exists in `07-database-design.md` §3, and adding one wasn't this phase's task); `is_greeting`/`is_out_of_topic` classifiers (exact-match normalization / keyword-pattern denylist respectively).
- `app/graphs/nodes/`: `preprocess_input`, `classify_greeting`, `classify_out_of_topic`, `respond_short_circuit.py` (three canned-response nodes), `embed_question`, `similarity_search`, `check_similarity_threshold` (pure routing function, not a state-mutating node), `condense_history` (incremental fold per `17-memory-strategy.md` §4), `generate_answer` (streams via `langgraph.config.get_stream_writer`), `append_sources`, `persist_message` (thin wrapper reusing Phase 8's `chat_service.persist_message`), `log_chat_metrics` (named to avoid colliding with Phase 6's ingestion `log_metrics.py`; registered under graph node key `"log_metrics"`).
- `app/graphs/user_chat_graph.py` (new): wires all of the above per `05-ai-agent-design.md` §2.2's diagram — all three short-circuit tiers converge on `persist_message`/`log_metrics` (not literally `END` as the diagram's abbreviated arrows show) so canned replies are saved to history and every tier is logged for analytics; only `append_sources` is skipped on short-circuit paths. Imports only `graphs/nodes/` (no `tools/operator_tools.py` anywhere in its transitive closure — verified by `tests/integration/test_persona_isolation.py`).
- `app/tools/user_tools.py` (new): placeholder — no extra tool needed beyond the node pipeline (no LLM-driven tool-calling anywhere, per `16-tool-calling.md`).
- `app/repositories/knowledge_chunk_repository.py`: added `similarity_search` (+ `SimilarityMatch` dataclass) — the exact retrieval query from `18-rag-design.md` §4, extended with a second join to resolve `superseded_by_title`.
- `app/errors.py`: added `FileTooLargeError` (413/`FILE_TOO_LARGE`) and `UnsupportedMediaTypeError` (415/`UNSUPPORTED_MEDIA_TYPE`) for the chat image-upload validation path.
- `app/schemas/chat.py` (new): `ChatRequestFields` (validates either wire format into one shape), `ChatSourceItem`, `ChatStreamEvent` (the one fixed SSE JSON schema, `06-api-specification.md` §0).
- `app/utils/sse.py` (new): `format_sse_event`, `stream_with_keepalive` (emits `: keepalive\n\n` on inactivity without ever cancelling/losing an in-flight chunk — deliberately avoids `asyncio.wait_for`'s cancel-on-timeout behavior).
- `app/services/chat_service.py`: added `stream_user_chat_response` (runs `user_chat_graph.astream(..., stream_mode=["custom","values"])`, converts to SSE, maps exceptions to `error` events per `22-error-handling.md` §2), `_build_done_event`, `_map_exception_to_error_event`. The `user_chat_graph` import is deferred (function-local) to break a circular import (`persist_message` node -> `chat_service` -> `user_chat_graph` -> `persist_message` node).
- `app/api/user_router.py`: added `POST /api/chat` — manually parses `Request` to support both `multipart/form-data` (image present) and plain JSON (per `06-api-specification.md` §2's dual wire format, which isn't expressible via one set of FastAPI `Body`/`Form` params), validates via `ChatRequestFields`, validates the optional image (MIME allowlist + `MAX_IMAGE_UPLOAD_MB`; real malware scanning is Phase 12), resolves the session and persists the user's message *before* opening the SSE stream (so `404`/`400`/`413`/`415` are plain JSON responses, per spec), then returns a `StreamingResponse` wrapping `chat_service.stream_user_chat_response`. Rate-limited via Phase 4's `rate_limit_dependency("/api/chat")`.
- Tests: `tests/integration/test_user_chat_graph.py` (4 tests — all three short-circuit tiers + full RAG, asserting exact Bedrock call counts and DB persistence), `tests/integration/test_persona_isolation.py` (static AST-based transitive-import walk — no dynamic import, so it never executes `tools/operator_tools.py` even after Phase 10 adds it), `tests/integration/test_language.py` (4 tests — greeting canned text + full-RAG system-prompt language instruction, both Indonesian- and English-phrased), `tests/integration/test_freshness.py` (5 tests — `render_context` unit tests + end-to-end system-prompt assertions for `valid_until` present/absent), `tests/unit/test_classifiers.py` (23 table-driven cases), `tests/unit/test_similarity_threshold.py` (8 boundary cases), `tests/unit/test_sse.py` (2 tests — keepalive-without-loss, no-keepalive-when-fast). `KnowledgeChunkRepository.similarity_search` is stubbed at the class level in these tests rather than seeding real `knowledge_chunks` rows, since the shared test DB carries real leftover rows from Phase 6's manual verification that would make pgvector-distance assertions flaky.
- **Manual verification against the real running app**: temporary `redis:7-alpine` container (this environment's Redis wasn't already running) + existing `bravi-db-1` + `poetry run uvicorn`, against real Bedrock. Ingested one small real text document via `/api/opr/ingest`, then verified via real `curl -N` SSE calls: greeting/out-of-topic canned tiers (exact text, zero Bedrock calls implied by instant response); a genuinely-relevant question scored *below* the default `SIMILARITY_SCORE_THRESHOLD=0.75` against real Cohere Embed v4 embeddings (~0.51-0.65 observed — see Known Issues); re-verified full RAG end-to-end with `SIMILARITY_SCORE_THRESHOLD=0.5` passed as a one-off process env var (committed `.env` untouched, same technique as Phase 6) — real streamed, grounded, Bahasa Indonesia Markdown answer with a correct `## Sources` section citing the ingested document. Also confirmed unknown `session_id` -> `404` before the stream opens, and `POST /api/messages` round-trips the real persisted turn. All test sessions/`usage_metrics`/the one ingested document were cleaned up afterward; the temporary Redis container was removed.

## Remaining Tasks

- None for Phase 9. Next session should begin Phase 10 (Operator Chat Graph & `/api/opr/chat` (SSE)) — reuses every shared node built this phase, adds `classify_add_knowledge_intent`, `route_by_intent`, `generate_summary`, `tools/operator_tools.py`.

## Files Added

- `backend/app/graphs/chat_state.py`
- `backend/app/graphs/prompts.py`
- `backend/app/graphs/canned_responses.py`
- `backend/app/graphs/nodes/preprocess_input.py`
- `backend/app/graphs/nodes/classify_greeting.py`
- `backend/app/graphs/nodes/classify_out_of_topic.py`
- `backend/app/graphs/nodes/respond_short_circuit.py`
- `backend/app/graphs/nodes/embed_question.py`
- `backend/app/graphs/nodes/similarity_search.py`
- `backend/app/graphs/nodes/check_similarity_threshold.py`
- `backend/app/graphs/nodes/condense_history.py`
- `backend/app/graphs/nodes/generate_answer.py`
- `backend/app/graphs/nodes/append_sources.py`
- `backend/app/graphs/nodes/persist_message.py`
- `backend/app/graphs/nodes/log_chat_metrics.py`
- `backend/app/graphs/user_chat_graph.py`
- `backend/app/tools/user_tools.py`
- `backend/app/schemas/chat.py`
- `backend/app/utils/sse.py`
- `backend/tests/integration/test_user_chat_graph.py`
- `backend/tests/integration/test_persona_isolation.py`
- `backend/tests/integration/test_language.py`
- `backend/tests/integration/test_freshness.py`
- `backend/tests/unit/test_classifiers.py`
- `backend/tests/unit/test_similarity_threshold.py`
- `backend/tests/unit/test_sse.py`

## Files Modified

- `backend/app/repositories/knowledge_chunk_repository.py` — `similarity_search`/`SimilarityMatch`.
- `backend/app/errors.py` — `FileTooLargeError`, `UnsupportedMediaTypeError`.
- `backend/app/services/chat_service.py` — `stream_user_chat_response`, `_build_done_event`, `_map_exception_to_error_event`.
- `backend/app/api/user_router.py` — `POST /api/chat`.
- `docs/IMPLEMENTATION_PLAN.md` — Phase 9 checkboxes/status/progress table updated to `DONE`; dated note recording implementation decisions.

## Tests Executed

- `poetry run pytest tests/integration/test_user_chat_graph.py tests/integration/test_persona_isolation.py tests/integration/test_language.py tests/integration/test_freshness.py tests/unit/test_classifiers.py tests/unit/test_similarity_threshold.py tests/unit/test_sse.py -v` → 46/46 passed.
- `poetry run pytest` (full suite) → 202/202 passed, run 3× with no flakiness.
- `poetry run black --check .` / `poetry run ruff check .` / `poetry run mypy app` → all clean.
- Live verification against the real running app (real Bedrock, real Postgres, temporary Redis) — see Completed Tasks' manual-verification entry.

## Verification Results

All Phase 9 Verification checklist items pass.

## Known Issues

- Carried over from Phase 0: no commit/remote yet, CI still unexercised on GitHub's infrastructure.
- Carried over from Phase 3: live-Bedrock smoke-test credentials are root-account, not a scoped IAM role/user.
- Carried over from Phase 5: this environment's standalone `bravi-db-1` Postgres container still occupies the `db` service's name/port; a from-scratch `docker-compose up` was not re-run this phase either.
- Carried over from Phase 7: file-upload validation (MIME allowlist, size limit, malware scanning) is intentionally deferred to Phase 12 for the ingest path; this phase adds the same MIME/size checks (but not malware scanning) for `/api/chat`'s image upload too.
- **New this phase — `SIMILARITY_SCORE_THRESHOLD`'s default (`0.75`) is empirically strict against real Cohere Embed v4 scores.** Live verification found genuinely on-topic, well-matched question/document pairs scoring ~0.51-0.65 — below the default threshold, meaning realistic questions may hit the "no knowledge found" short-circuit more often than expected at the documented default. This confirms the pre-existing, already-tracked risk (`01-prd.md` §11 item 3, `18-rag-design.md` §5) rather than introducing a new one; no default was changed (a product/tuning decision, out of this phase's authority) — flag for the user before real traffic relies on this default.
- Redis is not running by default in this environment (only `bravi-db-1` persists between sessions) — any future manual verification needing `/api/chat`/`/api/opr/chat`/`/api/opr/ingest` live against a running app will need a temporary Redis container again (`docker run -d --name bravi-redis-temp -p 6379:6379 redis:7-alpine`), matching this phase's and Phase 5's approach.
- Two real `knowledge_sources`/`knowledge_documents` rows remain in the shared database from Phase 6's manual verification (`sample.pdf`/`sample-3pp.pdf`) — intentionally left in place, unaffected by this phase's work.

## Architectural Decisions

- **`check_similarity_threshold` is a pure routing function, not a state-mutating graph node** — `best_score` is already computed by `similarity_search`; making it a real node would add a no-op state transition. Mirrors `graphs/ingestion_graph.py`'s own `_route_after` pattern (Phase 6).
- **Short-circuit `respond_*` nodes converge on `persist_message`/`log_metrics`, not literally `END`** — see the dated note in `IMPLEMENTATION_PLAN.md` Phase 9 for the full reasoning (the diagram's literal arrows are read as abbreviated, not literal, since `log_metrics`'s own documented fields require every short-circuit tier to be logged).
- **`classify_out_of_topic` is a cheap keyword-denylist heuristic, not embedding-based** — forced by `IMPLEMENTATION_PLAN.md` §3's non-negotiable short-circuit ordering (out-of-topic must run before `embed_question`), which rules out `05-ai-agent-design.md` §2.4's embedding-based alternative.
- **Token streaming uses LangGraph's `get_stream_writer()`/`stream_mode="custom"`**, combined with `stream_mode="values"` for the final state — confirmed against the installed `langgraph==1.2.9` source directly (not just docs) before relying on it, since it's the only mechanism that fits `11-coding-standard.md` §7's "graph's async streaming invocation" requirement without buffering.
- **`user_chat_graph` import in `chat_service.py` is function-local (deferred), not module-level** — avoids a circular import against `graphs/nodes/persist_message.py`, which imports `chat_service` to reuse Phase 8's `persist_message` function rather than duplicating the `sessions.title` set-once logic.
- **`KnowledgeChunkRepository.similarity_search` is stubbed at the class level in graph tests**, not exercised against seeded real rows — this environment's shared test database carries real rows left in place from Phase 6's manual verification (by design, see that phase's notes), so real pgvector-distance-based assertions would be flaky; the stub isolates the graph's routing logic (what Phase 9's tests need to verify) from real embedding content, while `sessions`/`messages`/`usage_metrics` persistence still goes through the real test database.

## Next Recommended Action

Begin Phase 10 — Operator Chat Graph & `/api/opr/chat` (SSE). Read `docs/05-ai-agent-design.md` §2.2-§2.5, `docs/06-api-specification.md` §5, `docs/prompts/ai-agent.md` §2/§6, `docs/11-coding-standard.md` §8.1, `docs/12-testing-strategy.md` §3 before starting. Reuses this phase's shared nodes (`preprocess_input`, `classify_greeting`, `embed_question`, `similarity_search`, `check_similarity_threshold`, `condense_history`, `generate_answer`, `append_sources`, `persist_message`, `log_chat_metrics`) — only `classify_add_knowledge_intent`, `route_by_intent`, `generate_summary`, and `tools/operator_tools.py` are new. Consider revisiting `SIMILARITY_SCORE_THRESHOLD`'s default with the user given this phase's empirical finding (Known Issues above) before Phase 10's own manual verification hits the same behavior.
