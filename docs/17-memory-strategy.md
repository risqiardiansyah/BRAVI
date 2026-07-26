# 17 — Memory Strategy

## 1. Scope

How conversational memory works within a session: what's stored, when/how it's condensed, and how it fits inside the model's context window. Table/column definitions for `sessions`/`messages` already exist in `07-database-design.md` §3.1/§3.2, and the `condense_history` node's existence/trigger condition already exists in `05-ai-agent-design.md` §2.2/§2.3 and `04-system-architecture.md` §7. This document adds three things that were referenced but never fully specified: where the rolling summary is actually persisted, the exact turn-counting/recompute semantics, and a token-budget check against the model's context window.

## 2. Memory Taxonomy (clarifying framing, not new mechanics)

| Type | What it is | Where it lives |
|---|---|---|
| Short-term | Raw recent turns | `messages` table (`07-database-design.md` §3.2) |
| Long-term / condensed | Rolling summary of older turns | `sessions.history_summary` (new column, §3 below) |
| Semantic | Retrieved knowledge-base chunks | `top_matches` per-request, from pgvector — **not** conversational memory; covered in `18-rag-design.md`, listed here only to distinguish it from the two types above |

## 3. Persistence Gap — New Columns

`05-ai-agent-design.md` §2.1 defines `history_summary` as a field on `ChatState`, but no document ever defined where that summary is persisted *between* requests — without persistence, "rolling" summary (§2.3 of that document: "produces/updates a rolling summary... to avoid re-summarizing from scratch each time") has nothing to roll onto. `07-database-design.md`'s `sessions` table (§3.1) had no such column. Adding:

- `sessions.history_summary TEXT` (nullable — `NULL` until the session first exceeds `CONTEXT_CONDENSATION_MAX_TURNS`)
- `sessions.history_summary_updated_at TIMESTAMPTZ` (nullable — last time the summary was recomputed)

(Patched into `07-database-design.md` §3.1 alongside this document.)

## 4. Condensation Trigger — Precision (gap fill)

Two specifics that were previously left implicit:

- **What counts as a "turn"**: `CONTEXT_CONDENSATION_MAX_TURNS` counts raw `messages` rows for the session — both `role='user'` and `role='assistant'` rows, not user-turns only. A back-and-forth of 5 questions and 5 answers is 10 turns against this threshold, not 5.
- **Recompute strategy**: `condense_history` is **incremental**, not a full re-summarization from scratch on every triggering request. It re-summarizes only the messages added since `sessions.history_summary_updated_at`, folding them into the existing `history_summary` via a single small text-model call, then overwrites both columns. This bounds the condensation call's own cost/latency as a session grows arbitrarily long — a from-scratch re-summarization would otherwise make condensation itself get more expensive the longer a conversation runs, defeating its own purpose as a cost-control mechanism.

## 5. Context Window Budget (new — not previously computed anywhere)

Approximate token allocation for a single `generate_answer` call (QA mode, `RETRIEVAL_TOP_K` default) against Claude Sonnet 4.6:

| Component | Approx. budget |
|---|---|
| System prompt (QA, Bahasa Indonesia + freshness instructions, `docs/prompts/ai-agent.md` §1) | ~400 tokens |
| Retrieved context (`RETRIEVAL_TOP_K` × `CHUNK_SIZE_TOKENS` = 5 × 700) | ≈ 3,500 tokens |
| Condensed history (capped ≤150 words, `docs/prompts/ai-agent.md` §7) | ≈ 200 tokens |
| Question (max ~2,000 chars, `08-security.md` §3) | ≈ 500 tokens worst case |
| Output reserve (`BEDROCK_MAX_OUTPUT_TOKENS`) | 1,024 tokens |
| **Total, worst case** | **≈ 5,600 tokens** |

This fits Claude Sonnet 4.6's context window with wide margin — no truncation logic is currently needed anywhere in the pipeline. The largest single contributor by far is retrieved context; re-check this budget if `RETRIEVAL_TOP_K` or `SUMMARY_TOP_K` defaults are raised significantly (`SUMMARY_TOP_K`=15 alone pushes retrieved context to ≈10,500 tokens for the Operator summary sub-flow — still comfortably within budget today, but the number to watch first if either top-k default grows).

## 6. Interaction With Retention (clarifying an edge case)

`07-database-design.md` §7 purges `messages` older than `MESSAGE_RETENTION_DAYS` but leaves `sessions` rows in place. Because `history_summary` lives on `sessions`, **it is not purged by the retention job** — a very old session retains its condensed memory (and would resume using it, if that session were somehow reused) even after its raw `messages` have aged out and been deleted. This is called out explicitly rather than left as an implicit side effect: it's a reasonable consequence of the existing retention design, not a bug, but worth knowing before anyone builds tooling that assumes `history_summary` decays on the same schedule as raw messages.
