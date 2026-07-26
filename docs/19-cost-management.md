# 19 — Cost Management

## 1. Scope

Token/cost tracking mechanics and Bedrock quota management. The `usage_metrics` schema and the cost-efficiency NFR already exist (`07-database-design.md` §3.7, `03-non-functional-requirements.md` §5); this document fills in three things referenced but never fully specified: the actual cost-calculation mechanism, a concrete quota-management plan, and a parameterized budget alert.

## 2. Cost Calculation Mechanism (gap fill)

`09-observability.md` §2 states `estimated_cost_usd` is "computed from token counts × known Bedrock pricing table (configurable)" without ever defining where that table lives or how it's applied. Concretely:

- A pricing table (e.g., `bedrock_pricing.yaml`, or an equivalent config-loaded structure) lists `$ per 1,000 input tokens` and `$ per 1,000 output tokens` per model id (`BEDROCK_TEXT_MODEL`, `BEDROCK_EMBEDDING_MODEL`), loaded at startup — **not** hardcoded in Python, since AWS pricing changes independently of any code deploy.
- `log_metrics` computes `estimated_cost_usd = (input_tokens / 1000 × input_rate) + (output_tokens / 1000 × output_rate)`, using the rate row for whichever model was actually invoked (`model_text_used`/`model_embedding_used` — `null` when short-circuited, per `07-database-design.md` §3.7, so no cost is attributed to short-circuited requests).
- **Update procedure**: a pricing change is a config update (edit and redeploy the pricing table), never a schema or code change. This document intentionally does not hardcode current AWS Bedrock $ rates — they change over time and would go stale here; pull actual rates from the AWS Bedrock pricing page at implementation time.

## 3. Quota Management (gap fill)

Bedrock enforces account-level throughput quotas (requests/minute, tokens/minute) per model. `05-ai-agent-design.md` §3.2 and `03-non-functional-requirements.md` §2 both reference this qualitatively ("avoid saturating Bedrock's account-level embedding throughput") without a concrete monitoring/response plan:

- **Monitoring**: quota utilization is observed via AWS Service Quotas / CloudWatch (Bedrock publishes throttle-related metrics natively) — this application does not compute its own quota-utilization percentage; it only reacts to `ThrottlingException` when it occurs (`14-bedrock-integration.md` §5).
- **Backpressure levers** (already existing config, cross-referenced here as the quota-management toolkit): `INGESTION_CONCURRENCY` and `EMBEDDING_BATCH_SIZE` bound ingestion-side load; the Bedrock client's circuit breaker (`14-bedrock-integration.md` §6) is the chat-side backpressure valve once throttling starts.
- **If sustained throttling occurs in production**, the correct response is a quota-increase request via AWS Support — not a code change. Flagging this explicitly so a production throttling incident isn't misdiagnosed as an application bug when it's actually an account-level ceiling.

## 4. Budget Alerting (new setting)

`DAILY_COST_BUDGET_USD` (optional; unset = no budget alert) — a new setting that parameterizes the previously-defined-but-thresholdless "Daily estimated cost exceeds budget threshold → Notify" alert in `09-observability.md` §7. A scheduled check (same cadence as `retention_service`, or a dedicated lightweight job) sums `usage_metrics.estimated_cost_usd` for the current calendar day and compares it against this threshold, firing the alert when exceeded.

(Patched into `10-deployment.md` §3 and `23-configuration.md` §3.)

## 5. Cost Attribution

Cross-reference only, no new content: `usage_metrics.persona`/`endpoint` already support grouping cost by persona/endpoint (`07-database-design.md` §3.7/§4), and `estimated_cost_usd` is already surfaced via `/api/opr/analytics` (`06-api-specification.md` §8).

## 6. Cost-Reduction Levers — Quick Reference (new consolidation, existing mechanisms)

No document previously compiled the full set of cost levers in one place:

| Lever | Mechanism | Where defined |
|---|---|---|
| Avoid unnecessary generation calls | Short-circuit pipeline (greeting/add-knowledge-intent/out-of-topic/similarity threshold) | `02-functional-requirements.md` FR-6 |
| Bound output length per call | `BEDROCK_MAX_OUTPUT_TOKENS` | `05-ai-agent-design.md` §5 |
| Batch embedding calls during ingestion | `EMBEDDING_BATCH_SIZE` | `05-ai-agent-design.md` §3.2 |
| Bound condensation frequency/size | `CONTEXT_CONDENSATION_MAX_TURNS`, incremental (not from-scratch) condensation | `17-memory-strategy.md` §4 |
| Avoid recomputation on repeated questions | Response/retrieval caching (not yet built) | `18-rag-design.md` §6 |
