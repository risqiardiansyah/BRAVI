"""Full `/metrics` counter set from docs/09-observability.md §5 —
docs/IMPLEMENTATION_PLAN.md Phase 13 task 6 ("not just the Phase 5 skeleton").

Each metric is exercised at the exact call site that increments/observes it, rather than
only asserting the name appears in `generate_latest()` output — a metric registered but
never actually incremented anywhere would still show up in exposition text.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from app.clients import bedrock_client as bedrock_client_module
from app.clients.bedrock_client import (
    BedrockClient,
    BedrockInvocationError,
    CircuitState,
    PromptContentBlock,
    PromptMessage,
    PromptPayload,
)
from app.config import settings
from app.graphs.chat_state import ChatState
from app.graphs.nodes.log_chat_metrics import log_chat_metrics
from app.middleware.rate_limit import TokenBucketRateLimiter
from app.utils.metrics import (
    bedrock_circuit_breaker_state,
    bedrock_embedding_calls_total,
    bedrock_text_calls_total,
    bedrock_tokens_total,
    chat_latency_ms,
    chat_requests_total,
    chat_ttft_ms,
    estimated_cost_usd_total,
    rate_limit_rejections_total,
)

# --- Bedrock call counters + circuit-breaker gauge (docs/14-bedrock-integration.md) ----


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "InvokeModel")


@pytest.fixture
def make_client(monkeypatch: pytest.MonkeyPatch) -> Callable[..., BedrockClient]:
    def _make(**overrides: object) -> BedrockClient:
        defaults: dict[str, object] = {
            "BEDROCK_MAX_RETRIES": 0,
            "BEDROCK_RETRY_BACKOFF_BASE_MS": 1,
            "BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD": 1,
            "BEDROCK_CIRCUIT_BREAKER_COOLDOWN_SECONDS": 60,
            "BEDROCK_TIMEOUT_SECONDS": 5,
            "BEDROCK_TEMPERATURE": 0.2,
            "BEDROCK_MAX_OUTPUT_TOKENS": 256,
            "BEDROCK_TEXT_MODEL": "test-text-model",
            "BEDROCK_EMBEDDING_MODEL": "test-embedding-model",
        }
        defaults.update(overrides)
        for key, value in defaults.items():
            monkeypatch.setattr(settings, key, value)
        client = BedrockClient()
        client._client = MagicMock()
        return client

    return _make


async def test_embed_increments_bedrock_embedding_calls_total(
    make_client: Callable[..., BedrockClient],
) -> None:
    client = make_client()
    body = MagicMock()
    body.read.return_value = json.dumps({"embeddings": [[0.1] * 4]}).encode("utf-8")
    client._client.invoke_model.return_value = {"body": body}

    before = bedrock_embedding_calls_total._value.get()
    await client.embed(["hello"], input_type="search_query")
    assert bedrock_embedding_calls_total._value.get() == before + 1


async def test_generate_stream_increments_bedrock_text_calls_total(
    make_client: Callable[..., BedrockClient],
) -> None:
    client = make_client()
    client._client.converse_stream.return_value = {"stream": iter([])}

    before = bedrock_text_calls_total._value.get()
    prompt = PromptPayload(
        messages=[PromptMessage(role="user", content=[PromptContentBlock(text="hi")])]
    )
    async for _ in client.generate_stream(prompt):
        pass
    assert bedrock_text_calls_total._value.get() == before + 1


async def test_circuit_breaker_gauge_reflects_live_state(
    make_client: Callable[..., BedrockClient],
) -> None:
    # Threshold=2: the circuit breaker counts *every* failed call toward the trip
    # threshold regardless of retryability (`BedrockClient.embed`'s `except
    # BedrockInvocationError: record_failure()` doesn't distinguish) — one failure must
    # leave it CLOSED, a second must trip it OPEN.
    client = make_client(BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD=2)

    # Point the module-level singleton at this test's client so the Gauge's
    # `set_function` callback (bound to `bedrock_client_module.bedrock_client`) reads
    # this client's state.
    original_client = bedrock_client_module.bedrock_client
    bedrock_client_module.bedrock_client = client
    try:
        client._client.invoke_model.side_effect = _client_error("ValidationException")
        with pytest.raises(BedrockInvocationError):
            await client.embed(["x"], input_type="search_query")
        assert client.circuit_breaker_state is CircuitState.CLOSED
        assert bedrock_circuit_breaker_state.collect()[0].samples[0].value == 0

        with pytest.raises(BedrockInvocationError):
            await client.embed(["x"], input_type="search_query")
        assert client.circuit_breaker_state is CircuitState.OPEN
        assert bedrock_circuit_breaker_state.collect()[0].samples[0].value == 1
    finally:
        bedrock_client_module.bedrock_client = original_client


# --- Rate-limit rejections (docs/08-security.md §6) ------------------------------------


async def test_rate_limit_rejection_increments_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    import fakeredis

    from app.clients.redis_client import RedisClient
    from app.middleware.rate_limit import RateLimitExceededError

    monkeypatch.setattr(settings, "RATE_LIMIT_BURST", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS_PER_MINUTE", 1)
    limiter = TokenBucketRateLimiter(RedisClient(fakeredis.FakeAsyncRedis(decode_responses=True)))

    before = rate_limit_rejections_total.labels(endpoint="/api/chat")._value.get()
    await limiter.enforce(endpoint="/api/chat", identity="user:test-metrics")
    with pytest.raises(RateLimitExceededError):
        await limiter.enforce(endpoint="/api/chat", identity="user:test-metrics")
    assert rate_limit_rejections_total.labels(endpoint="/api/chat")._value.get() == before + 1


# --- Chat-turn metrics (docs/05-ai-agent-design.md §2.2/§2.3) --------------------------


class _FakeSession:
    async def flush(self) -> None:
        return None


class _FakeUsageMetricRepository:
    def __init__(self, session: object) -> None:
        pass

    async def create(self, row: object) -> object:
        return row


async def test_log_chat_metrics_updates_chat_and_bedrock_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.graphs.nodes import log_chat_metrics as log_chat_metrics_module

    monkeypatch.setattr(
        log_chat_metrics_module, "UsageMetricRepository", _FakeUsageMetricRepository
    )

    state: ChatState = {
        "session_id": uuid.uuid4(),
        "user_id": "test-user",
        "persona": "user",
        "question": "Pertanyaan",
        "original_question": "Pertanyaan",
        "image_bytes": None,
        "image_format": None,
        "short_circuited": False,
        "short_circuit_reason": None,
        "mode": None,
        "text_model_used": "global.anthropic.claude-sonnet-4-6",
        "input_tokens": 100,
        "output_tokens": 50,
    }

    before_requests = chat_requests_total.labels(
        persona="user", short_circuit_reason="none"
    )._value.get()
    before_input_tokens = bedrock_tokens_total.labels(direction="input")._value.get()
    before_output_tokens = bedrock_tokens_total.labels(direction="output")._value.get()
    before_cost = estimated_cost_usd_total._value.get()

    await log_chat_metrics(state, {"configurable": {"session": _FakeSession()}})  # type: ignore[arg-type]

    assert (
        chat_requests_total.labels(persona="user", short_circuit_reason="none")._value.get()
        == before_requests + 1
    )
    assert bedrock_tokens_total.labels(direction="input")._value.get() == before_input_tokens + 100
    assert bedrock_tokens_total.labels(direction="output")._value.get() == before_output_tokens + 50
    assert estimated_cost_usd_total._value.get() > before_cost


async def test_log_chat_metrics_observes_chat_latency(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.graphs.nodes import log_chat_metrics as log_chat_metrics_module

    monkeypatch.setattr(
        log_chat_metrics_module, "UsageMetricRepository", _FakeUsageMetricRepository
    )

    state: ChatState = {
        "session_id": uuid.uuid4(),
        "user_id": "test-user",
        "persona": "user",
        "question": "Halo",
        "original_question": "Halo",
        "image_bytes": None,
        "image_format": None,
        "short_circuited": True,
        "short_circuit_reason": "greeting",
        "mode": None,
        "started_monotonic": 0.0,
    }

    def _observed_count() -> float:
        for sample in chat_latency_ms.collect()[0].samples:
            if sample.name.endswith("_count") and sample.labels == {
                "endpoint": "/api/chat",
                "short_circuited": "True",
            }:
                return sample.value
        return 0.0

    before_count = _observed_count()

    await log_chat_metrics(state, {"configurable": {"session": _FakeSession()}})  # type: ignore[arg-type]

    assert _observed_count() == before_count + 1


async def test_log_chat_metrics_observes_chat_ttft(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.graphs.nodes import log_chat_metrics as log_chat_metrics_module

    monkeypatch.setattr(
        log_chat_metrics_module, "UsageMetricRepository", _FakeUsageMetricRepository
    )

    state: ChatState = {
        "session_id": uuid.uuid4(),
        "user_id": "test-user",
        "persona": "user",
        "question": "Apa syarat klaim?",
        "original_question": "Apa syarat klaim?",
        "image_bytes": None,
        "image_format": None,
        "short_circuited": False,
        "short_circuit_reason": None,
        "mode": None,
        "started_monotonic": 0.0,
        "ttft_ms": 420,
    }

    def _observed_count() -> float:
        for sample in chat_ttft_ms.collect()[0].samples:
            if sample.name.endswith("_count") and sample.labels == {"endpoint": "/api/chat"}:
                return sample.value
        return 0.0

    before_count = _observed_count()

    await log_chat_metrics(state, {"configurable": {"session": _FakeSession()}})  # type: ignore[arg-type]

    assert _observed_count() == before_count + 1
