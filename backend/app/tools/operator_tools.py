"""Tools reachable ONLY from `operator_chat_graph` — docs/11-coding-standard.md §2/§8.1.

Same "no LLM-driven tool-calling" rationale as `tools/user_tools.py` (Phase 9): the
Operator QA/summary pipeline is a fixed LangGraph node/edge sequence, not an agentic
tool-calling loop (docs/16-tool-calling.md §1-§2 — no `bind_tools`/function-calling schema
is ever handed to a Bedrock model). Knowledge management itself (`POST /api/opr/ingest`,
`GET /api/opr/knowledge`, `DELETE /api/opr/knowledge/{id}`) is already exposed as ordinary
REST endpoints (Phase 7) that a human operator calls directly from the frontend — the chat
graph never needs to invoke them as a "tool"; it only *tells* the operator to use the
button via `classify_add_knowledge_intent`'s fixed template (docs/05-ai-agent-design.md
§2.5). No extra callable is needed beyond that for this phase.

This module exists so the persona-isolation import boundary in `11-coding-standard.md`
§8.1 has a concrete, dedicated home to grow into if a later phase adds a real
Operator-only tool — `graphs/user_chat_graph.py` must never import this file
(docs/11-coding-standard.md §8.1, verified by
`tests/integration/test_persona_isolation.py`).
"""

from __future__ import annotations
