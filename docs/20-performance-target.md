# 20 — Performance Targets

## 1. Scope

Latency, throughput, and concurrency targets. p50/p95 targets for the short-circuit and full-RAG paths already exist in `03-non-functional-requirements.md` §1 — this document does not restate them, only adds what that table never covered: p99, Time to First Token (TTFT), aggregate throughput, a per-node latency budget, and concurrency-scaling guidance. New target rows (p99/TTFT/throughput) are patched directly into `03-non-functional-requirements.md` §1 so the authoritative target table stays in exactly one place; this document is the rationale/deep-dive behind those numbers.

## 2. Latency/Throughput Targets

The p50/p95/p99, TTFT, and throughput numbers are defined **only** in `03-non-functional-requirements.md` §1 — not reproduced here, so there is exactly one place to update if a target changes. The rest of this document is the rationale and per-node breakdown behind those numbers.

## 3. Why TTFT Is Tracked Separately From Total Latency (new rationale — not stated anywhere)

For a streaming SSE chat UI, perceived responsiveness is dominated by how quickly the *first* token arrives, not by total completion time. A 6-second p95 full answer with a 400ms TTFT feels responsive throughout; the same 6-second answer with a 4-second TTFT feels stuck before it feels fast. TTFT is measured from request-received to the first `token` SSE event, and is dominated by everything that happens **before** the first streamed byte: session resolution, short-circuit checks, `embed_question`, `similarity_search`, `condense_history` (if triggered), plus Bedrock's own time-to-first-chunk — not the full generation. Tracking it separately from total p95/p99 latency prevents a long-tail-but-fast-starting generation from being incorrectly flagged as a UX problem by an aggregate-latency-only view.

## 4. Per-Node Latency Budget (target, not observed — new)

`09-observability.md` §4 shows the *shape* of per-node latency logging but sets no target/budget per node. Indicative p95 budget for a full-RAG request (should sum to within the full-RAG p95 target in `03-non-functional-requirements.md` §1):

| Node | Budget |
|---|---|
| `preprocess_input` | < 50ms (< 800ms if an image is present — a Bedrock vision call) |
| `classify_greeting` / `classify_out_of_topic` (and, Operator-only, `classify_add_knowledge_intent`) | < 20ms combined |
| `embed_question` | < 300ms |
| `similarity_search` | < 100ms |
| `condense_history` (only when triggered) | < 1.5s |
| `generate_answer`/`generate_summary` — TTFT portion | < 2s |
| `generate_answer`/`generate_summary` — full stream completion | remainder of budget, Bedrock-dependent |
| `append_sources` / `persist_message` / `log_metrics` | < 50ms combined |

Use this table to localize a regression found during load testing (`12-testing-strategy.md` §6) to a specific node instead of only having an end-to-end number to work from.

## 5. Concurrency Guidance (new)

Chat request handling is I/O-bound — waiting on Bedrock and PostgreSQL, not computing — so realistic per-replica concurrency is bounded by `DB_POOL_SIZE` and the async event loop's capacity to hold open SSE connections, not by CPU core count. Horizontal autoscaling (`10-deployment.md` §7) should trigger on concurrent-connection count and/or p95 latency degradation rather than CPU utilization alone — a CPU-based-only trigger systematically under-scales an I/O-bound streaming workload, since CPU stays low even as connection count and latency climb.

## 6. Load Testing Cross-Reference

Cross-reference only, no new content: these targets are validated via the load/performance testing approach already defined in `12-testing-strategy.md` §6.
