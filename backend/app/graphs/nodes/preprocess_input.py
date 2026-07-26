"""`preprocess_input` node — docs/05-ai-agent-design.md §2.2/§2.3.

If an image is attached, it is passed as multimodal input directly to the vision-capable
`BEDROCK_TEXT_MODEL` alongside the question — no separate captioning/OCR model or service
(a firm decision, not an open option). The resulting description is merged into `question`
so every downstream node (classifiers, `embed_question`, `generate_answer`) operates on a
single enriched text string; `original_question` is left untouched for message/analytics
persistence (docs/06-api-specification.md §4 trending relies on the question actually asked,
not image-description text).
"""

from __future__ import annotations

import time
from typing import Any

from app.clients.bedrock_client import (
    PromptContentBlock,
    PromptMessage,
    PromptPayload,
    bedrock_client,
)
from app.config import settings
from app.graphs.chat_state import ChatState
from app.graphs.prompts import IMAGE_DESCRIPTION_SYSTEM_PROMPT


async def preprocess_input(state: ChatState) -> dict[str, Any]:
    started = time.monotonic()
    image_bytes = state.get("image_bytes")
    if not image_bytes:
        return {"started_monotonic": started}

    prompt = PromptPayload(
        system=IMAGE_DESCRIPTION_SYSTEM_PROMPT,
        messages=[
            PromptMessage(
                role="user",
                content=[
                    PromptContentBlock(text=state["question"]),
                    PromptContentBlock(
                        image_bytes=image_bytes, image_format=state.get("image_format")
                    ),
                ],
            )
        ],
    )
    chunks: list[str] = []
    async for token in bedrock_client.generate_stream(prompt):
        chunks.append(token)
    description = "".join(chunks).strip()

    merged_question = f"{state['question']}\n\n[Deskripsi gambar terlampir]: {description}"
    return {
        "started_monotonic": started,
        "image_description": description,
        "question": merged_question,
        "text_model_used": settings.BEDROCK_TEXT_MODEL,
    }
