# 15 — Model Management

## 1. Scope

LLM/embedding model registration, selection, and inference-parameter configuration. The model table itself (which model serves which purpose, and the env vars naming them) already exists in `05-ai-agent-design.md` §5 and `10-deployment.md` §3 — this document does not repeat it. What's new here: how models are organized in code as a "registry," the sampling/inference parameters that were never specified anywhere, the upgrade procedure, and the explicit no-fallback decision.

## 2. Model Registry Pattern

- `app/config.py` exposes a small, purpose-keyed mapping — `EMBEDDING_MODEL`, `TEXT_MODEL` — read from `BEDROCK_EMBEDDING_MODEL`/`BEDROCK_TEXT_MODEL`. This is a **static mapping, not a dynamic selection algorithm**: Phase 1 has exactly one model per purpose, always. Calling it a "registry" should not imply runtime model routing/selection logic that doesn't exist — flagging this explicitly so a future reader doesn't go looking for selection logic that was never built.
- Two purposes only, per `05-ai-agent-design.md` §5: **embeddings** (Cohere Embed v4) and **text generation/summarization/condensation** (Claude Sonnet 4.6, used identically for `generate_answer`, `generate_summary`, and `condense_history` — there is no third, smaller model for condensation).

## 3. Inference Parameters (new settings — not previously specified anywhere)

No document in this project previously specified sampling parameters for text generation. Adding:

- `BEDROCK_TEMPERATURE` (default `0.2`) — applies uniformly to `generate_answer`, `generate_summary`, and `condense_history`. A low value favors grounded, repeatable output over creative variation, consistent with the "answer only from `<context>`, never hallucinate" instruction already in every system prompt (`05-ai-agent-design.md` §4, `docs/prompts/ai-agent.md`). No per-node override in Phase 1 — a single knob keeps the config surface small until there's evidence a per-purpose split is needed.
- `top_p`/`top_k` are left at the model's own defaults, not overridden — tuning two interacting sampling knobs without empirical output-quality data risks fighting `BEDROCK_TEMPERATURE`'s effect rather than complementing it. Revisit only if temperature alone doesn't achieve the desired determinism.
- `BEDROCK_MAX_OUTPUT_TOKENS` already bounds output length (`05-ai-agent-design.md` §5) — no duplication here.

(Patched into `10-deployment.md` §3 and `23-configuration.md` §3.)

## 4. Model Versioning & Upgrade Procedure (new — not previously documented)

Because the canonical prompts (`docs/prompts/ai-agent.md`) are tuned against a specific model's behavior, changing `BEDROCK_TEXT_MODEL` or `BEDROCK_EMBEDDING_MODEL` is a **behavior-affecting change**, not a routine config bump:

1. Validate the candidate model version in `staging` against the full golden-path/short-circuit integration suite (`12-testing-strategy.md` §3), plus a manual spot-check of Bahasa Indonesia output quality and answer groundedness — a model swap can silently regress either even if every existing automated assertion still passes.
2. If the **embedding** model changes: the vector space is not portable across embedding model versions. Every row in `knowledge_chunks.embedding` becomes invalid for the new model and must be regenerated — treat this as a data migration (full re-ingestion of the existing knowledge base), not a config change. Do not deploy a new `BEDROCK_EMBEDDING_MODEL` value without a corresponding re-embedding plan.
3. If the **text** model changes: re-run the manual checklist in `12-testing-strategy.md` §10 before promoting to production, since prompt behavior (tone, groundedness, adherence to the Bahasa Indonesia and freshness-mention rules) can shift between model versions even when the API contract doesn't.
4. Roll out via the same blue/green deployment process as any other release (`10-deployment.md` §8) — no special-cased deployment path for a model swap.

## 5. No Fallback Model (documented decision)

Phase 1 has **no** secondary/fallback model configured for either purpose. Resilience against Bedrock unavailability is handled entirely by the circuit breaker (`14-bedrock-integration.md` §6) — a fast, explicit `BEDROCK_UNAVAILABLE` error, not a silent failover to a different model with different cost, latency, and (for text generation) output-quality characteristics. A multi-model fallback strategy is a reasonable candidate for a later phase if single-model availability proves insufficient in practice — it is not added now because it isn't a known problem, and speculative fallback logic adds real complexity (which model's output do you trust, how do you keep two models' prompts both tuned) for a risk that circuit-breaking already mitigates adequately at Phase 1 scale.

## 6. Open Item (cross-reference, not re-decided here)

The exact output vector dimension of the Cohere Embed v4 inference profile is unconfirmed — already tracked as an open risk in `01-prd.md` §11 item 4 and blocking the final `VECTOR(n)` size in `07-database-design.md` §3.5. Not re-litigated in this document; resolve it once, at the source.
