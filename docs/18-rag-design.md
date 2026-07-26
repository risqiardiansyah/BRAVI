# 18 — RAG Design

## 1. Scope

Retrieval-augmented generation mechanics: embedding, chunking, retrieval, and — since these are commonly expected parts of a RAG design but are **not currently built** — an explicit, documented stance on reranking and caching. Settings (`CHUNK_SIZE_TOKENS`, `RETRIEVAL_TOP_K`, `SIMILARITY_SCORE_THRESHOLD`, etc.) are already defined in `10-deployment.md` §3 and `05-ai-agent-design.md` §3.3/§5 — not repeated here. This document adds the retrieval query's actual SQL shape, a tokenizer-precision correction to the chunking description, and design sketches for reranking/caching that don't exist yet in Phase 1.

## 2. Embedding

Cross-reference only, no new content: model/batching config already in `05-ai-agent-design.md` §3.2/§5, output vector column in `07-database-design.md` §3.5.

## 3. Chunking — Tokenizer Precision (gap fill)

`05-ai-agent-design.md` §3.2 describes the chunker generically as "recursive/character-based chunking with overlap." Because `CHUNK_SIZE_TOKENS`/`CHUNK_OVERLAP_TOKENS` are named — and validated (`23-configuration.md` §4) — as **token** counts, `chunk_text` must measure chunk length using a tokenizer compatible with the embedding model's own tokenization, not a character-count approximation. A character-based splitter cannot actually enforce "stay comfortably under `embed-v4`'s max input tokens" (`05-ai-agent-design.md` §3.3), because character count and token count diverge unpredictably across languages/content (notably relevant here given the system's Bahasa Indonesia focus). The exact tokenizer library is an implementation detail (a Cohere-compatible tokenizer if available; a conservative proxy such as `tiktoken` otherwise) — but chunking purely on character count should be treated as a bug against the documented setting names, not an acceptable approximation.

## 4. Retrieval Query (new — exact shape not previously written)

```sql
SELECT c.id, c.content, c.page_number, c.document_id,
       d.title, d.source_url, d.valid_until, d.superseded_by_document_id
FROM knowledge_chunks c
JOIN knowledge_documents d ON d.id = c.document_id
ORDER BY c.embedding <=> :query_embedding
LIMIT :top_k;
```

`<=>` is pgvector's cosine-distance operator, matching the `vector_cosine_ops` HNSW index (`07-database-design.md` §3.5) — ascending distance corresponds to descending similarity, so ordering by it and applying `LIMIT` directly yields the top-k nearest chunks. `similarity_search` joins `knowledge_documents` in the same query (rather than a second round-trip) to pull `title`/`source_url`/`valid_until`/`superseded_by_document_id` needed for citations and freshness metadata (`07-database-design.md` §5b) in one pass. `:top_k` is `RETRIEVAL_TOP_K` for QA mode or `SUMMARY_TOP_K` for the Operator summary sub-flow.

## 5. Reranking — Not Implemented in Phase 1 (documented decision)

No reranking stage exists anywhere in the pipeline: `similarity_search` returns the raw pgvector top-k directly as `top_matches`, with no cross-encoder or LLM-based rerank pass narrowing or reordering results before generation. This keeps the retrieval path to a single Bedrock embedding call with no added latency/cost, consistent with the cost-control-first design principle in `05-ai-agent-design.md` §1.

**Future design sketch** (not built — a backlog candidate, alongside the existing caching backlog item in `13-roadmap.md`): over-fetch `top_k × N` candidates from pgvector, rerank with a cross-encoder or a cheap Bedrock classification call, then truncate to `RETRIEVAL_TOP_K` before generation. Only worth adding once retrieval precision is empirically shown to be a bottleneck — this ties to the open similarity-threshold-tuning risk already tracked in `01-prd.md` §11 item 3. Not a known problem today; not pre-built speculatively.

## 6. Caching — Not Implemented in Phase 1 (documented decision)

No retrieval-result or response cache exists — every non-short-circuited request re-embeds the question and re-queries pgvector, even for a repeated or common question. This is already tracked as a backlog item (`13-roadmap.md`: "Semantic/response caching layer for repeated common questions"); this section is the design sketch that entry currently lacks:

- **Cache key**: normalized question text (lowercased, whitespace-trimmed) + `persona` — User and Operator answers can legitimately differ (summary mode never applies to User), so the persona must be part of the key.
- **Cache value**: either the full assembled answer + sources, or just the retrieved chunk ids (cheaper to invalidate correctly — still saves the embedding + pgvector round-trip, but not the generation call).
- **Invalidation**: must be invalidated whenever `DELETE /api/opr/knowledge/{id}` or `/api/opr/ingest` changes the knowledge base — a stale cached answer citing a now-deleted document would silently violate the "deletion is immediate" guarantee (`07-database-design.md` §5a). This correctness requirement is precisely *why* caching wasn't added casually in Phase 1: it isn't a plain `SET`/`GET`, it's a correctness-sensitive cache that needs a real invalidation trigger wired into both mutation endpoints.
- Not scheduled for Phase 1. Revisit once `/api/opr/analytics` shows a meaningfully repetitive question distribution (the data needed to justify it doesn't exist yet, since the system hasn't run in production).

## 7. Freshness/Versioning in Retrieval

Cross-reference only, no new content: `07-database-design.md` §5b and `05-ai-agent-design.md` §2.3/§4 already fully specify how `valid_until`/`superseded_by_document_id` flow from `similarity_search` into the prompt.
