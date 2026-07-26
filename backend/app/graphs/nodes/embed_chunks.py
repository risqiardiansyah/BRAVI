"""`embed_chunks` node — docs/05-ai-agent-design.md §3.2, batched via `EMBEDDING_BATCH_SIZE`.

Goes through `clients/bedrock_client.py` exclusively (docs/11-coding-standard.md §12)
— no node constructs its own Bedrock client or retry logic. `input_type="search_document"`
since these are corpus chunks being indexed, not a query (mirrors Cohere's own
document-vs-query embedding guidance, see `BedrockClient.embed`'s docstring).
"""

from __future__ import annotations

import logging
from typing import Any

from app.clients.bedrock_client import BedrockInvocationError, bedrock_client
from app.config import settings
from app.graphs.ingestion_state import IngestionState

logger = logging.getLogger(__name__)


async def embed_chunks(state: IngestionState) -> dict[str, Any]:
    chunks = state["chunks"]
    batch_size = settings.EMBEDDING_BATCH_SIZE
    try:
        embeddings: list[list[float]] = []
        for start in range(0, len(chunks), batch_size):
            texts = [chunk["content"] for chunk in chunks[start : start + batch_size]]
            embeddings.extend(await bedrock_client.embed(texts, input_type="search_document"))
        return {"embeddings": embeddings}
    except BedrockInvocationError as exc:
        logger.warning(
            "embed_chunks failed for document_id=%s", state.get("document_id"), exc_info=True
        )
        return {"status": "failed", "error": f"embed_chunks: {exc}"}
