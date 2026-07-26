# 05 — AI Agent Design (LangGraph)

## 1. Overview

Three LangGraph graph instances implement the system's AI behavior: `user_chat_graph`, `operator_chat_graph`, and `ingestion_graph`. All are Python/LangChain/LangGraph-based and call AWS Bedrock exclusively for embeddings and text generation.

`user_chat_graph` and `operator_chat_graph` are **separate graph instances, not one graph branching on a `persona` flag** — this is a deliberate tool-isolation boundary, not just a routing convenience: the User graph is wired only with QA nodes/tools and must never have the ability to reach ingestion or knowledge-management tools, while the Operator graph additionally wires the summary sub-flow, the add-knowledge-intent short-circuit (§2.2), and (where applicable) knowledge-management tool calls. They share the same underlying node *functions* where behavior overlaps (e.g., `embed_question`, `similarity_search`) to avoid duplication, but each graph's own node/tool registry only includes what that persona is allowed to call. See `11-coding-standard.md` §8 for the enforced project-structure rule behind this.

**Language**: every generated answer — from both graphs, in every mode — is in **Bahasa Indonesia**, regardless of the language the question was asked in. This is enforced in the system prompts themselves (`docs/prompts/ai-agent.md`), not by translating output after the fact. The canned/short-circuit responses (§2.5) are likewise authored in Bahasa Indonesia directly, not translated at runtime.

## 2. Chat Graphs (`user_chat_graph`, `operator_chat_graph`)

### 2.1 State Schema (conceptual)

```python
class ChatState(TypedDict):
    session_id: str
    user_id: str
    persona: Literal["user", "operator"]   # informational only — the graph instance itself is what enforces tool access, not this flag
    question: str
    image_description: Optional[str]
    is_greeting: Optional[bool]
    is_add_knowledge_intent: Optional[bool]  # operator_chat_graph only; always None/unset in user_chat_graph
    is_out_of_topic: Optional[bool]
    question_embedding: Optional[list[float]]
    top_matches: Optional[list[dict]]   # chunk_id, content, score, source, source_url, valid_until, superseded_by_title
    best_score: Optional[float]
    history_summary: Optional[str]
    mode: Literal["qa", "summary"]
    answer: Optional[str]
    short_circuited: bool
    short_circuit_reason: Optional[str]
    latency_ms: dict[str, float]
```

### 2.2 Nodes & Routing

```
START
  │
  ▼
[preprocess_input] ── if image present → describe image, merge into question
  │
  ▼
[classify_greeting] ──yes──▶ [respond_default_greeting] ──▶ END
  │no
  ▼
[classify_add_knowledge_intent]  (operator_chat_graph ONLY — node does not exist in user_chat_graph)
  │yes──▶ [respond_add_knowledge_template] ──▶ END   (short_circuit_reason: "add_knowledge_intent")
  │no / n/a
  ▼
[classify_out_of_topic] ──yes──▶ [respond_out_of_topic] ──▶ END
  │no
  ▼
[embed_question]  (1 Bedrock embedding call)
  │
  ▼
[similarity_search]  (pgvector top-k)
  │
  ▼
[check_similarity_threshold] ──below threshold──▶ [respond_no_knowledge_found] ──▶ END
  │meets threshold
  ▼
[condense_history]  (only if session has prior turns > CONTEXT_CONDENSATION_MAX_TURNS)
  │
  ▼
 ── user_chat_graph ──────────────────    ── operator_chat_graph ─────────────────
 [generate_answer]                        [route_by_intent] ──summary──▶ [generate_summary]
  (Bedrock text model, streamed,           │default
   grounded in retrieved chunks)           ▼
                                           [generate_answer]  (same as User)
  │                                        │
  ▼                                        ▼
[append_sources]  (append "## Sources" section using [Link Text](URL) built from top_matches)
  │
  ▼
[persist_message]
  │
  ▼
[log_metrics]
  │
  ▼
 END
```
`generate_answer`/`generate_summary` stream tokens back to the API layer as they're produced (see `06-api-specification.md` §0); `append_sources` runs once generation completes, before the terminal `done` event is emitted. Only `operator_chat_graph` wires `route_by_intent`/`generate_summary`, `classify_add_knowledge_intent`, and any knowledge-management tool nodes — `user_chat_graph` never includes them, per the persona-isolation rule in `11-coding-standard.md` §8.

### 2.3 Node Details

| Node | Bedrock call? | Notes |
|---|---|---|
| `preprocess_input` | Yes, if image present | If `file` (image) is provided, it is passed as multimodal input directly to the Bedrock text model (`BEDROCK_TEXT_MODEL`, Claude Sonnet 4.6 — vision-capable) alongside the question. No separate captioning/OCR model or service is used — this is a firm decision, not an open option. |
| `classify_greeting` | No | Rule-based (regex/keyword list) or tiny local classifier — must be near-zero latency, zero LLM cost. |
| `classify_add_knowledge_intent` | No | `operator_chat_graph` only. Rule-based bilingual keyword/phrase match (e.g. "tambah knowledge ai", "tambah pengetahuan ai", "add ai knowledge", "add knowledge ai") — near-zero latency, zero LLM cost, same pattern as `classify_greeting`. On match, returns the fixed template from §2.5 §6 and skips straight to `persist_message`/`log_metrics`/END — never reaches embedding, retrieval, or generation. |
| `classify_out_of_topic` | No (preferred) | Prefer a cheap heuristic (keyword/topic-anchor embedding compare reusing the same embedding call as similarity search) rather than a separate LLM call — see §2.4. |
| `embed_question` | Yes (embedding model) | Single call to `BEDROCK_EMBEDDING_MODEL`. |
| `similarity_search` | No | pgvector cosine/inner-product query, `LIMIT RETRIEVAL_TOP_K` (default `5`; `operator_chat_graph`'s summary path re-queries or extends to `SUMMARY_TOP_K`, default `15`, once `route_by_intent` selects the summary sub-flow). Deleted knowledge (`DELETE /api/opr/knowledge/{id}`, `07-database-design.md` §5a) never appears here — its `knowledge_chunks` rows are hard-deleted at delete time, so no separate "exclude deleted" filter is needed at query time. |
| `check_similarity_threshold` | No | Compares `best_score` to `SIMILARITY_SCORE_THRESHOLD`. |
| `condense_history` | Yes (text model, small) | Only invoked when history exceeds configured turn window; produces/updates a rolling summary persisted to `sessions.history_summary` (see `07-database-design.md` §3.1, `17-memory-strategy.md` §3/§4) incrementally, to avoid re-summarizing from scratch each time. |
| `route_by_intent` | No | `operator_chat_graph` only. Classifies whether the operator's question is a summarization request; routes to `generate_summary` vs `generate_answer` accordingly. Rule-based/lightweight — not a separate Bedrock call. |
| `generate_answer` / `generate_summary` | Yes (text model) | Only reached after all short-circuits pass. Prompt includes: system instructions, retrieved chunks with `valid_until`/`superseded_by` metadata attached (as untrusted reference data), condensed history, question. Invoked in streaming mode; output is Markdown **in Bahasa Indonesia** (regardless of question language), capped at `BEDROCK_MAX_OUTPUT_TOKENS`. Mentions a chunk's freshness/versioning metadata only when it's actually present on that chunk's source document — never speculates otherwise. Retrieves `RETRIEVAL_TOP_K` chunks (`SUMMARY_TOP_K` for `generate_summary`, a broader value — see §3.3-adjacent config in §5). |
| `append_sources` | No | Appends a `## Sources` section to the streamed answer using `[Link Text](URL)` per citation, built from `top_matches` (title as link text, `knowledge_documents.source_url` as URL — see `07-database-design.md` §3.4). Matches with no `source_url` are cited by title only, without a link. |
| `persist_message` | No | Writes to `messages` table. |
| `log_metrics` | No | Writes to `usage_metrics` table (latency per node, model used, tokens, short-circuit tier). |

### 2.4 Merging Out-of-Topic Check with Embedding Call (cost optimization note)

To avoid two separate embedding/LLM calls, `classify_out_of_topic` can be implemented as part of the same similarity search: pre-compute embeddings for a small set of "in-domain topic" anchor phrases once at startup; compare the question embedding (from `embed_question`) against both the anchors and the knowledge base in the same pass. If out-of-topic detection instead needs to run *before* any embedding call (per PRD ordering), keep it as a cheap keyword/classifier step and reserve the embedding-based check purely for the similarity threshold gate. **Implementation detail to finalize during development; both orderings satisfy "respond before the most expensive step."**

### 2.5 Default / Canned Responses

Stored as configurable templates (not hard-coded strings), e.g. in a `responses` config table or `.env`/YAML, so operators can tune wording without a redeploy. All are authored directly in Bahasa Indonesia (not translated at runtime) — canonical text in `docs/prompts/ai-agent.md` §3–§6:

- Greeting/small-talk response
- Out-of-topic response
- No-relevant-knowledge-found response
- **Add-knowledge-intent response** (`operator_chat_graph` only) — fixed template `Silahkan klik tombol berikut untuk mengisi form: <BTN>Add Knowledge</BTN>`. The `<BTN>Label</BTN>` inline tag is a deliberate, documented exception to "answers are plain Markdown" — a custom directive the frontend renders as an actionable button, not real HTML/CommonMark. See `docs/prompts/ai-agent.md` §6 and `06-api-specification.md` §0.

## 3. Ingestion Graph

### 3.1 State Schema (conceptual)

```python
class IngestionState(TypedDict):
    source_type: Literal["file", "text", "url"]
    source_ref: str            # file path, raw text, or URL
    title: Optional[str]
    raw_text: Optional[str]
    chunks: Optional[list[str]]
    embeddings: Optional[list[list[float]]]
    knowledge_id: Optional[str]
    status: Literal["queued", "processing", "completed", "failed"]
    error: Optional[str]
```

### 3.2 Nodes

```
START
  │
  ▼
[load_source]        # download PDF from URL, read uploaded file, or use raw text
  │
  ▼
[extract_text]        # PDF text extraction (e.g., pypdf/pdfplumber)
  │
  ▼
[chunk_text]           # recursive/character-based chunking with overlap
  │
  ▼
[embed_chunks]          # batched Bedrock embedding calls, batch size EMBEDDING_BATCH_SIZE
  │
  ▼
[store_vectors]          # upsert into pgvector knowledge_chunks table
  │
  ▼
[update_ingestion_status]
  │
  ▼
[log_metrics]
  │
  ▼
END (on any node failure → mark status="failed", persist error, continue batch for other sources)
```

For the startup batch job (many `knowledge_sources` rows), up to `INGESTION_CONCURRENCY` documents are processed through this graph concurrently rather than strictly sequentially, bounded to avoid saturating Bedrock's account-level embedding throughput (the same `clients/bedrock_client.py` retry/backoff/circuit-breaker behavior from `11-coding-standard.md` §12 applies here too). A document that ends in `status="failed"` is not automatically retried — re-running the startup job (idempotent, `07-database-design.md` §5) or re-submitting via `/api/opr/ingest` is the retry mechanism for Phase 1; a dedicated retry endpoint is a candidate for a later phase.

### 3.3 Chunking Strategy

- Chunk size and overlap are **configurable**, not hard-coded: `CHUNK_SIZE_TOKENS` (default `700`) and `CHUNK_OVERLAP_TOKENS` (default `100`), read via `app/config.py` like every other setting (see `11-coding-standard.md` §5). `chunk_text` uses these directly.
- The overlap exists specifically so a concept split across a chunk boundary still has enough surrounding text embedded coherently in at least one chunk — i.e., the embedding stays "in context" rather than being computed on a fragment that starts or ends mid-thought.
- `CHUNK_SIZE_TOKENS` must stay comfortably under the embedding model's max input tokens (`BEDROCK_EMBEDDING_MODEL`) — confirm the Cohere `embed-v4` limit before finalizing the default — and `CHUNK_OVERLAP_TOKENS` must always be `< CHUNK_SIZE_TOKENS` (validated at startup in `config.py`, fail fast on misconfiguration).
- Preserve section/page metadata for citation (`source_document_id`, `page_number`).

## 4. Prompt Design Principles

- **System prompt** clearly instructs the model to answer only using provided context; if the answer isn't in the context, say so (do not hallucinate).
- **Retrieved content is treated as data, not instructions** — explicitly delimited (e.g., inside `<context>` tags) with an instruction that content inside must never override system instructions (prompt-injection mitigation — see `08-security.md`).
- **Output format**: the model is instructed to respond in Markdown. Source citations are appended by the `append_sources` node (not left to the model to fabricate URLs), using `[Link Text](URL)` — see §2.2/§2.3 and `06-api-specification.md` §0.
- **Language**: every system prompt hard-codes "always answer in Bahasa Indonesia" — this is not conditional on the question's language, and it is not implemented as a post-generation translation step.
- **Document freshness/versioning**: `valid_until`/`superseded_by` metadata is attached per-document inside `<context>` (only when the document actually has it set) with an explicit instruction to mention it naturally when present and never speculate when absent — same "don't let the model fabricate what isn't grounded in real data" principle as source citations.
- **Operator summary mode** uses a distinct system prompt oriented toward structured, comprehensive summarization rather than a short direct answer.
- Full prompt templates maintained in `docs/prompts/ai-agent.md` and in-code prompt modules — kept in sync.

## 5. Model Configuration

| Purpose | Model | Env var |
|---|---|---|
| Embeddings | Cohere Embed v4 (via inference profile) | `BEDROCK_EMBEDDING_MODEL` |
| Text generation / summarization / condensation | Claude Sonnet 4.6 | `BEDROCK_TEXT_MODEL` |

Generation/retrieval tuning knobs (all read via `app/config.py`, never hardcoded — see `11-coding-standard.md` §5):

| Var | Default | Purpose |
|---|---|---|
| `RETRIEVAL_TOP_K` | `5` | Chunks retrieved for `generate_answer` (QA mode). |
| `SUMMARY_TOP_K` | `15` | Broader chunk set retrieved for `generate_summary` (Operator summary mode). |
| `BEDROCK_MAX_OUTPUT_TOKENS` | `1024` | Hard cap on `generate_answer`/`generate_summary`/`condense_history` output length — bounds cost/latency of every generation call. |

`clients/bedrock_client.py` resilience settings (`BEDROCK_TIMEOUT_SECONDS`, `BEDROCK_MAX_RETRIES`, `BEDROCK_RETRY_BACKOFF_BASE_MS`) are defined in `11-coding-standard.md` §12 and apply to every node above that calls Bedrock — graph nodes never implement their own retry/timeout logic.

## 6. Testing Considerations

- Each graph node should be unit-testable independently (pure functions where possible, Bedrock calls mocked).
- Golden-path and short-circuit-path integration tests for both `user_chat_graph` and `operator_chat_graph`, including a persona tool-isolation check (see `11-coding-standard.md` §8.1).
- Ingestion graph tested against sample PDFs including malformed/corrupt files (must fail gracefully).
- See `12-testing-strategy.md`.
