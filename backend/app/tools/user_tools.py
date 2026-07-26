"""Tools reachable ONLY from `user_chat_graph` — docs/11-coding-standard.md §2/§8.1.

The User QA pipeline is fully expressed as a fixed LangGraph node/edge sequence
(`graphs/user_chat_graph.py`) with no LLM-driven tool-calling (`docs/16-tool-calling.md`
§1-§2 — no `bind_tools`/function-calling schema is ever handed to a Bedrock model), so no
extra callable "tool" is needed beyond the node pipeline itself
(docs/IMPLEMENTATION_PLAN.md Phase 9: "minimal is fine if no extra tools are needed"). This
module exists so the persona-isolation import boundary in `11-coding-standard.md` §8.1 has
a concrete, dedicated home to grow into if a later phase adds a real User-only tool —
`user_chat_graph.py` must never import `tools/operator_tools.py` regardless.
"""

from __future__ import annotations
