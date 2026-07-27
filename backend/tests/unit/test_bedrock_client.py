"""Unit tests for app.clients.bedrock_client — docs/14-bedrock-integration.md.

Everything here runs against a mocked boto3 client (`docs/IMPLEMENTATION_PLAN.md`
Phase 3 Verification) — no network access, no real AWS credentials needed.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError

from app.clients.bedrock_client import (
    EMBEDDING_OUTPUT_DIMENSION,
    BedrockClient,
    BedrockInvocationError,
    BedrockUnavailableError,
    CircuitState,
    PromptContentBlock,
    PromptMessage,
    PromptPayload,
    _classify_exception,
)
from app.config import settings


def _client_error(code: str, message: str = "boom", operation: str = "InvokeModel") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


def _embed_response(vectors: list[list[float]]) -> dict[str, object]:
    body = MagicMock()
    body.read.return_value = json.dumps({"embeddings": vectors}).encode("utf-8")
    return {"body": body}


@pytest.fixture
def make_client(monkeypatch: pytest.MonkeyPatch) -> Callable[..., BedrockClient]:
    def _make(**overrides: object) -> BedrockClient:
        defaults: dict[str, object] = {
            "BEDROCK_MAX_RETRIES": 2,
            "BEDROCK_RETRY_BACKOFF_BASE_MS": 1,
            "BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD": 3,
            "BEDROCK_CIRCUIT_BREAKER_COOLDOWN_SECONDS": 0.05,
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


# --- Error taxonomy (docs/14-bedrock-integration.md §5) ---------------------


@pytest.mark.parametrize(
    ("code", "expected_retryable"),
    [
        ("ThrottlingException", True),
        ("ModelTimeoutException", True),
        ("InternalServerException", True),
        ("ModelNotReadyException", True),
        ("ValidationException", False),
        ("AccessDeniedException", False),
        ("ModelStreamErrorException", False),
        ("SomeUnknownFutureException", False),
    ],
)
def test_classify_client_error_taxonomy(code: str, expected_retryable: bool) -> None:
    classified_code, retryable = _classify_exception(_client_error(code))
    assert classified_code == code
    assert retryable is expected_retryable


def test_classify_socket_timeouts_as_retryable_model_timeout() -> None:
    for exc in (
        ReadTimeoutError(endpoint_url="https://bedrock.example"),
        ConnectTimeoutError(endpoint_url="https://bedrock.example"),
    ):
        code, retryable = _classify_exception(exc)
        assert code == "ModelTimeoutException"
        assert retryable is True


def test_access_denied_logs_error(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.ERROR):
        _classify_exception(_client_error("AccessDeniedException"))
    assert any("access denied" in record.message.lower() for record in caplog.records)


# --- embed() -----------------------------------------------------------------


async def test_embed_success_returns_vectors(make_client: Callable[..., BedrockClient]) -> None:
    client = make_client()
    client._client.invoke_model.return_value = _embed_response([[0.1, 0.2]])

    result = await client.embed(["hello"], input_type="search_query")

    assert result == [[0.1, 0.2]]
    assert client.circuit_breaker_state is CircuitState.CLOSED


async def test_embed_parses_by_type_response_shape(
    make_client: Callable[..., BedrockClient],
) -> None:
    """Confirmed via a live smoke test against real Bedrock: Cohere Embed v4
    returns `{"float": [[...]]}` even for a single requested embedding type,
    contradicting AWS's own docs (which describe a flat-list shape here)."""
    client = make_client()
    body = MagicMock()
    body.read.return_value = json.dumps(
        {"embeddings": {"float": [[0.3, 0.4]]}, "response_type": "embeddings_by_type"}
    ).encode("utf-8")
    client._client.invoke_model.return_value = {"body": body}

    result = await client.embed(["hello"], input_type="search_query")

    assert result == [[0.3, 0.4]]


async def test_embed_passes_output_dimension_and_input_type(
    make_client: Callable[..., BedrockClient],
) -> None:
    client = make_client()
    client._client.invoke_model.return_value = _embed_response([[0.0] * EMBEDDING_OUTPUT_DIMENSION])

    await client.embed(["doc text"], input_type="search_document")

    call_kwargs = client._client.invoke_model.call_args.kwargs
    assert call_kwargs["modelId"] == "test-embedding-model"
    body = json.loads(call_kwargs["body"])
    assert body["output_dimension"] == EMBEDDING_OUTPUT_DIMENSION
    assert body["input_type"] == "search_document"
    assert body["texts"] == ["doc text"]


async def test_embed_retries_on_throttling_then_succeeds(
    make_client: Callable[..., BedrockClient],
) -> None:
    client = make_client()
    client._client.invoke_model.side_effect = [
        _client_error("ThrottlingException"),
        _embed_response([[1.0]]),
    ]

    result = await client.embed(["x"], input_type="search_query")

    assert result == [[1.0]]
    assert client._client.invoke_model.call_count == 2
    assert client.circuit_breaker_state is CircuitState.CLOSED


async def test_embed_exhausts_retries_and_raises(make_client: Callable[..., BedrockClient]) -> None:
    client = make_client(BEDROCK_MAX_RETRIES=2)
    client._client.invoke_model.side_effect = _client_error("ThrottlingException")

    with pytest.raises(BedrockInvocationError) as exc_info:
        await client.embed(["x"], input_type="search_query")

    assert exc_info.value.retryable is True
    assert client._client.invoke_model.call_count == 3  # initial + 2 retries


async def test_embed_validation_error_not_retried(
    make_client: Callable[..., BedrockClient],
) -> None:
    client = make_client()
    client._client.invoke_model.side_effect = _client_error("ValidationException")

    with pytest.raises(BedrockInvocationError) as exc_info:
        await client.embed(["x"], input_type="search_query")

    assert exc_info.value.retryable is False
    assert client._client.invoke_model.call_count == 1


# --- Circuit breaker (docs/14-bedrock-integration.md §6) ---------------------


async def test_circuit_breaker_trips_after_consecutive_retry_exhausted_failures(
    make_client: Callable[..., BedrockClient],
) -> None:
    client = make_client(BEDROCK_MAX_RETRIES=0, BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3)
    client._client.invoke_model.side_effect = _client_error("ThrottlingException")

    for _ in range(3):
        with pytest.raises(BedrockInvocationError):
            await client.embed(["x"], input_type="search_query")

    assert client.circuit_breaker_state is CircuitState.OPEN
    # exactly one boto3 call per embed() (no internal retries), 3 total for 3 failures
    assert client._client.invoke_model.call_count == 3


async def test_circuit_breaker_open_fails_fast_without_calling_boto3(
    make_client: Callable[..., BedrockClient],
) -> None:
    client = make_client(BEDROCK_MAX_RETRIES=0, BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD=1)
    client._client.invoke_model.side_effect = _client_error("ThrottlingException")

    with pytest.raises(BedrockInvocationError):
        await client.embed(["x"], input_type="search_query")
    assert client.circuit_breaker_state is CircuitState.OPEN

    client._client.invoke_model.reset_mock()
    with pytest.raises(BedrockUnavailableError):
        await client.embed(["x"], input_type="search_query")

    client._client.invoke_model.assert_not_called()


async def test_circuit_breaker_half_open_probe_success_closes(
    make_client: Callable[..., BedrockClient],
) -> None:
    client = make_client(
        BEDROCK_MAX_RETRIES=0,
        BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD=1,
        BEDROCK_CIRCUIT_BREAKER_COOLDOWN_SECONDS=0.01,
    )
    client._client.invoke_model.side_effect = _client_error("ThrottlingException")
    with pytest.raises(BedrockInvocationError):
        await client.embed(["x"], input_type="search_query")
    assert client.circuit_breaker_state is CircuitState.OPEN

    import asyncio

    await asyncio.sleep(0.02)  # let the cooldown elapse
    client._client.invoke_model.side_effect = None
    client._client.invoke_model.return_value = _embed_response([[1.0]])

    result = await client.embed(["x"], input_type="search_query")

    assert result == [[1.0]]
    assert client.circuit_breaker_state is CircuitState.CLOSED


async def test_circuit_breaker_half_open_probe_failure_reopens(
    make_client: Callable[..., BedrockClient],
) -> None:
    client = make_client(
        BEDROCK_MAX_RETRIES=0,
        BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD=1,
        BEDROCK_CIRCUIT_BREAKER_COOLDOWN_SECONDS=0.01,
    )
    client._client.invoke_model.side_effect = _client_error("ThrottlingException")
    with pytest.raises(BedrockInvocationError):
        await client.embed(["x"], input_type="search_query")
    assert client.circuit_breaker_state is CircuitState.OPEN

    import asyncio

    await asyncio.sleep(0.02)

    with pytest.raises(BedrockInvocationError):
        await client.embed(["x"], input_type="search_query")

    assert client.circuit_breaker_state is CircuitState.OPEN


# --- generate_stream() --------------------------------------------------------


def _text_prompt(text: str = "hello") -> PromptPayload:
    return PromptPayload(
        system="system prompt",
        messages=[PromptMessage(role="user", content=[PromptContentBlock(text=text)])],
    )


async def test_generate_stream_yields_text_chunks_in_order(
    make_client: Callable[..., BedrockClient],
) -> None:
    client = make_client()
    events = [
        {"contentBlockDelta": {"delta": {"text": "Hal"}}},
        {"contentBlockDelta": {"delta": {"text": "o"}}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]
    client._client.converse_stream.return_value = {"stream": iter(events)}

    chunks = [chunk async for chunk in client.generate_stream(_text_prompt())]

    assert chunks == ["Hal", "o"]
    assert client.circuit_breaker_state is CircuitState.CLOSED


async def test_generate_stream_passes_temperature_and_max_tokens(
    make_client: Callable[..., BedrockClient],
) -> None:
    client = make_client(BEDROCK_TEMPERATURE=0.33, BEDROCK_MAX_OUTPUT_TOKENS=111)
    client._client.converse_stream.return_value = {"stream": iter([])}

    async for _ in client.generate_stream(_text_prompt()):
        pass

    call_kwargs = client._client.converse_stream.call_args.kwargs
    assert call_kwargs["modelId"] == "test-text-model"
    assert call_kwargs["inferenceConfig"] == {"maxTokens": 111, "temperature": 0.33}
    assert call_kwargs["system"] == [{"text": "system prompt"}]
    assert call_kwargs["messages"] == [{"role": "user", "content": [{"text": "hello"}]}]


async def test_generate_stream_rejects_per_call_overrides(
    make_client: Callable[..., BedrockClient],
) -> None:
    client = make_client()

    with pytest.raises(TypeError):
        async for _ in client.generate_stream(_text_prompt(), temperature=0.9):
            pass


async def test_generate_stream_retries_before_first_chunk_on_throttling(
    make_client: Callable[..., BedrockClient],
) -> None:
    client = make_client()
    client._client.converse_stream.side_effect = [
        _client_error("ThrottlingException", operation="ConverseStream"),
        {"stream": iter([{"contentBlockDelta": {"delta": {"text": "ok"}}}])},
    ]

    chunks = [chunk async for chunk in client.generate_stream(_text_prompt())]

    assert chunks == ["ok"]
    assert client._client.converse_stream.call_count == 2


async def test_generate_stream_mid_stream_error_event_is_terminal_not_retried(
    make_client: Callable[..., BedrockClient],
) -> None:
    client = make_client()

    def event_iter() -> Iterator[dict[str, object]]:
        yield {"contentBlockDelta": {"delta": {"text": "partial"}}}
        yield {"modelStreamErrorException": {"message": "stream broke"}}

    client._client.converse_stream.return_value = {"stream": event_iter()}

    received: list[str] = []
    with pytest.raises(BedrockInvocationError) as exc_info:
        async for chunk in client.generate_stream(_text_prompt()):
            received.append(chunk)

    assert received == ["partial"]
    assert exc_info.value.error_code == "modelStreamErrorException"
    assert exc_info.value.retryable is False
    # no retry attempted after a chunk was already yielded
    assert client._client.converse_stream.call_count == 1
    # one failure recorded, but default threshold (3) not yet reached
    assert client.circuit_breaker_state is CircuitState.CLOSED


async def test_generate_stream_mid_stream_python_exception_is_terminal(
    make_client: Callable[..., BedrockClient],
) -> None:
    client = make_client()

    def event_iter() -> Iterator[dict[str, object]]:
        yield {"contentBlockDelta": {"delta": {"text": "partial"}}}
        raise ConnectionResetError("connection dropped")

    client._client.converse_stream.return_value = {"stream": event_iter()}

    received: list[str] = []
    with pytest.raises(BedrockInvocationError):
        async for chunk in client.generate_stream(_text_prompt()):
            received.append(chunk)

    assert received == ["partial"]
    assert client._client.converse_stream.call_count == 1


# --- Timeout / retry-disabling boto3 config ----------------------------------


def test_build_client_disables_boto3_builtin_retries_and_sets_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_client(service_name: str, **kwargs: object) -> MagicMock:
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(settings, "BEDROCK_TIMEOUT_SECONDS", 7)
    import app.clients.bedrock_client as bedrock_client_module

    monkeypatch.setattr(bedrock_client_module.boto3, "client", fake_client)

    BedrockClient()

    config = captured["config"]
    assert config.connect_timeout == 7
    assert config.read_timeout == 7
    assert config.retries == {"max_attempts": 1}
