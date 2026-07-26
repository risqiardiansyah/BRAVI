# 16 — Tool Calling & Agent Orchestration

## 1. Scope & Key Clarification

This document clarifies something no other document states explicitly: **this system does not use LLM-driven dynamic tool-calling.** There is no function-calling/`bind_tools` loop where a Bedrock model is given a set of tools and decides at inference time which one to invoke. All orchestration is a **deterministic LangGraph DAG** — which node runs next is decided by application code (rule-based classifiers, similarity thresholds, intent routing), never by the model choosing among available tools. The fixed node graphs are already fully specified in `05-ai-agent-design.md` §2.2/§3.2; this document explains the "why" behind that shape and defines what "tool" actually means in this codebase, neither of which was previously written down.

## 2. Why Not Dynamic Tool-Calling

- **Determinism & cost control**: the short-circuit cost-control ordering (greeting → [Operator: add-knowledge-intent] → out-of-topic → similarity threshold → RAG, `05-ai-agent-design.md` §2.2) is guaranteed by construction — a fixed graph edge, not a probabilistic outcome of the model "deciding" to check cheaply first. An LLM-driven tool-calling agent offers no such guarantee; it could choose to call the expensive generation path directly.
- **Security — this is the load-bearing reason**: the entire persona-isolation design (`11-coding-standard.md` §8.1) depends on each graph's tool/node registry being a **compile-time Python import boundary** — `user_chat_graph.py` structurally cannot reach `tools/operator_tools.py` because it never imports it. An LLM-driven tool-calling agent inverts this: the model is handed a set of *bound* tools and decides, based on its own reasoning over the conversation, which one to invoke. A prompt-injection payload or a reasoning failure could cause the model to call a tool that is technically bound to it but was never meant to be reachable from that request — exactly the class of risk `11-coding-standard.md` §8.1 documents wanting to make "structurally impossible instead of relying on a runtime check." Keeping tool invocation entirely outside the model's decision-making removes that failure mode rather than mitigating it with prompt hardening.
- This is a **deliberate architectural choice**, not an oversight. Per the Architect persona's rule in `docs/prompts/architect.md` ("flag the conflict explicitly before proceeding"), any future proposal to introduce model-driven tool selection must be evaluated against this rationale first, not adopted as a routine LangChain upgrade.

## 3. What "Tool" Means In This Codebase

- **Not** a LangChain `Tool`/`@tool`-decorated object bound to a model via a function-calling schema. There is no JSON-schema tool-description passed to Bedrock anywhere in this system.
- A "tool" here is a plain Python function or module living in `tools/user_tools.py` or `tools/operator_tools.py` (`11-coding-standard.md` §2), invoked **directly by a graph node's own code** — never by the model emitting a "call this tool" instruction that the runtime then dispatches.
- Interface convention: a tool function takes typed, code-constructed arguments (never raw LLM-generated JSON parsed as a function call) and returns a typed result merged into graph state. There is no function-calling JSON-schema contract to maintain because nothing except deterministic node code ever invokes these functions.

## 4. Node vs. Tool — the Distinction

- **Node** = a step in the graph's fixed DAG. Always executes when the graph reaches it (e.g., `classify_greeting`, `embed_question`, `generate_answer`) — presence in the graph, not model choice, determines execution.
- **Tool** = a capability a node's own code may call out to (e.g., an Operator-path node invoking a knowledge-management query function). Which node can reach which tool is fixed at graph-construction time (import boundaries, §3 above) — never chosen per-request by the model.
- In the current design, the clearest real example of a "tool" in this sense lives entirely on the Operator side (e.g., a knowledge-listing/query helper a node calls). Notably, the two most obviously "tool-like" Operator capabilities are **explicitly not implemented as tools at all**: add-knowledge-intent returns a fixed canned string with no action performed (`05-ai-agent-design.md` §2.5), and knowledge deletion is a plain REST/service-layer operation with no graph involvement whatsoever (`04-system-architecture.md` §4a). Both were deliberately kept out of any graph's tool surface — see the rationale already captured in `04-system-architecture.md` §9's design-decisions table.

## 5. Error Propagation

A tool/node failure raises a typed exception (`11-coding-standard.md` §6), caught by the owning node, which either:
- transitions the graph to a failure edge (the ingestion graph's per-document failure handling, `05-ai-agent-design.md` §3.2), or
- propagates up to the chat service, which maps it to a terminal SSE `error` event (`06-api-specification.md` §0, `22-error-handling.md` §2).

Nothing is silently swallowed at the node/tool boundary.

## 6. Testing

Cross-reference only: `12-testing-strategy.md` §3's persona tool-isolation regression test (`user_chat_graph`'s module has no import path reaching `tools/operator_tools.py`) is the automated check that protects the design decision in §2 above. No new test guidance to add here.
