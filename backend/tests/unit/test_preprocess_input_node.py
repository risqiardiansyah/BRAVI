"""Direct unit tests for `app.graphs.nodes.preprocess_input` —
docs/05-ai-agent-design.md §2.2/§2.3, docs/IMPLEMENTATION_PLAN.md Phase 14.

No current integration test attaches an image to a chat turn, so this node's
multimodal-description branch (everything past the "no image attached" early return)
is otherwise never exercised. Bedrock is stubbed at this node module's own
`bedrock_client` binding (same pattern as `tests/integration/test_user_chat_graph.py`).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.clients.bedrock_client import PromptPayload
from app.config import settings
from app.graphs.chat_state import ChatState
from app.graphs.nodes import preprocess_input as preprocess_input_module


class _StubBedrockClient:
    def __init__(self, tokens: list[str] | None = None) -> None:
        self.generate_calls: list[PromptPayload] = []
        self.tokens = tokens or ["Gambar ", "menunjukkan struk pembelian."]

    async def generate_stream(self, prompt: PromptPayload, **_params: Any) -> Any:
        self.generate_calls.append(prompt)
        for token in self.tokens:
            yield token


@pytest.fixture
def stub_bedrock(monkeypatch: pytest.MonkeyPatch) -> _StubBedrockClient:
    stub = _StubBedrockClient()
    monkeypatch.setattr(preprocess_input_module, "bedrock_client", stub)
    return stub


async def test_preprocess_input_without_image_returns_only_started_monotonic() -> None:
    state: ChatState = {
        "question": "Apa syarat klaim asuransi?",
        "image_bytes": None,
        "image_format": None,
    }
    result = await preprocess_input_module.preprocess_input(state)
    assert set(result.keys()) == {"started_monotonic"}
    assert isinstance(result["started_monotonic"], float)


async def test_preprocess_input_with_image_merges_description_into_question(
    stub_bedrock: _StubBedrockClient,
) -> None:
    state: ChatState = {
        "question": "Apa isi struk ini?",
        "image_bytes": b"\xff\xd8\xff fake-jpeg-bytes",
        "image_format": "jpeg",
    }

    result = await preprocess_input_module.preprocess_input(state)

    assert result["image_description"] == "Gambar menunjukkan struk pembelian."
    assert result["question"] == (
        "Apa isi struk ini?\n\n[Deskripsi gambar terlampir]: " "Gambar menunjukkan struk pembelian."
    )
    assert result["text_model_used"] == settings.BEDROCK_TEXT_MODEL
    assert isinstance(result["started_monotonic"], float)

    assert len(stub_bedrock.generate_calls) == 1
    prompt = stub_bedrock.generate_calls[0]
    assert prompt.system == preprocess_input_module.IMAGE_DESCRIPTION_SYSTEM_PROMPT
    content_blocks = prompt.messages[0].content
    assert content_blocks[0].text == "Apa isi struk ini?"
    assert content_blocks[1].image_bytes == state["image_bytes"]
    assert content_blocks[1].image_format == "jpeg"
