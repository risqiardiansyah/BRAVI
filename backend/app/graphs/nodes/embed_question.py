"""`embed_question` node — docs/05-ai-agent-design.md §2.2/§2.3.

Exactly one Bedrock embedding call per non-short-circuited request. `input_type=
"search_query"` (mirrors Cohere's document-vs-query embedding guidance) — the corpus side
was already embedded with `input_type="search_document"` by `graphs/nodes/embed_chunks.py`
(Phase 6).
"""

from __future__ import annotations

from typing import Any

from app.clients.bedrock_client import bedrock_client
from app.config import settings
from app.graphs.chat_state import ChatState


async def embed_question(state: ChatState) -> dict[str, Any]:
    embeddings = await bedrock_client.embed([state["question"]], input_type="search_query")
    return {
        "question_embedding": embeddings[0],
        "embedding_model_used": settings.BEDROCK_EMBEDDING_MODEL,
    }
