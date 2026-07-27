"""AWS Bedrock resilience client — docs/14-bedrock-integration.md, docs/11-coding-standard.md §12.

The only module in this codebase that constructs a `boto3` `bedrock-runtime`
client (docs/11-coding-standard.md §4/§12) or references Bedrock model IDs
directly. Exposes exactly two operations to the rest of the codebase —
`embed()` and `generate_stream()` — both purpose-keyed (embedding vs. text),
never given a raw model ID by the caller (docs/14-bedrock-integration.md §7).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, TypeVar

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError

from app.config import settings
from app.utils.metrics import (
    bedrock_circuit_breaker_state,
    bedrock_embedding_calls_total,
    bedrock_text_calls_total,
)

logger = logging.getLogger(__name__)

# Confirmed with the project owner (docs/IMPLEMENTATION_PLAN.md Phase 2 note):
# Cohere Embed v4 on Bedrock defaults to 1536 dimensions if `output_dimension`
# is unspecified — always pass this explicitly; it must match
# knowledge_chunks.embedding's VECTOR(1024) column.
EMBEDDING_OUTPUT_DIMENSION = 1024

_T = TypeVar("_T")


class BedrockInvocationError(Exception):
    """Raised for any Bedrock invocation failure — docs/11-coding-standard.md §6."""

    def __init__(self, message: str, *, error_code: str, retryable: bool) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class BedrockUnavailableError(BedrockInvocationError):
    """Circuit breaker is open — maps to `BEDROCK_UNAVAILABLE` (docs/22-error-handling.md §2/§6)."""

    def __init__(self) -> None:
        super().__init__(
            "Bedrock circuit breaker is open; failing fast without attempting the call.",
            error_code="BEDROCK_UNAVAILABLE",
            retryable=False,
        )


@dataclass(frozen=True)
class PromptContentBlock:
    """One block of a multimodal message — docs/14-bedrock-integration.md §8."""

    text: str | None = None
    image_bytes: bytes | None = None
    image_format: Literal["png", "jpeg", "webp", "gif"] | None = None


@dataclass(frozen=True)
class PromptMessage:
    role: Literal["user", "assistant"]
    content: list[PromptContentBlock]


@dataclass(frozen=True)
class PromptPayload:
    """Input to `generate_stream` — a system prompt plus message history,
    mapped onto Bedrock's Converse API message format."""

    system: str | None = None
    messages: list[PromptMessage] = field(default_factory=list)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


_RETRYABLE_ERROR_CODES = {
    "ThrottlingException",
    "ModelTimeoutException",
    "InternalServerException",
    "ModelNotReadyException",
}


def _classify_exception(exc: Exception) -> tuple[str, bool]:
    """Map a boto3/botocore exception to (error_code, retryable) — 14-bedrock-integration.md §5."""
    if isinstance(exc, ReadTimeoutError | ConnectTimeoutError):
        return "ModelTimeoutException", True
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "UnknownError")
        if code in _RETRYABLE_ERROR_CODES:
            return code, True
        if code == "AccessDeniedException":
            logger.error("Bedrock access denied — check IAM permissions: %s", exc)
        return code, False
    return "UnknownError", False


class _CircuitBreaker:
    """`closed`/`open`/`half_open` state machine — docs/14-bedrock-integration.md §6."""

    def __init__(self, *, failure_threshold: int, cooldown_seconds: float) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    async def before_call(self) -> None:
        """Raise `BedrockUnavailableError` if open (or a probe is already in flight)."""
        async with self._lock:
            if self._state is CircuitState.OPEN:
                assert self._opened_at is not None
                if time.monotonic() - self._opened_at < self._cooldown_seconds:
                    raise BedrockUnavailableError()
                self._state = CircuitState.HALF_OPEN
            elif self._state is CircuitState.HALF_OPEN:
                raise BedrockUnavailableError()

    async def record_success(self) -> None:
        async with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None

    async def record_failure(self) -> None:
        async with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._trip()
                return
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold:
                self._trip()

    def _trip(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        logger.warning("Bedrock circuit breaker tripped OPEN")


class BedrockClient:
    """The only place `boto3`'s `bedrock-runtime` client is constructed."""

    def __init__(self) -> None:
        self._client = self._build_client()
        self._circuit_breaker = _CircuitBreaker(
            failure_threshold=settings.BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            cooldown_seconds=settings.BEDROCK_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
        )

    @property
    def circuit_breaker_state(self) -> CircuitState:
        """Observable circuit breaker state (full `/metrics` wiring lands in Phase 13)."""
        return self._circuit_breaker.state

    @staticmethod
    def _build_client() -> Any:
        # docs/14-bedrock-integration.md §3: boto3's default credential provider
        # chain, as-is. Passing the two config values through (when set) is not
        # custom resolution logic — `aws_access_key_id`/`aws_secret_access_key`
        # are boto3.client()'s own first-class kwargs; when both are None (no
        # static keys configured), boto3 falls through to its normal chain
        # (e.g. an attached IAM role in staging/production).
        boto_config = BotoConfig(
            connect_timeout=settings.BEDROCK_TIMEOUT_SECONDS,
            read_timeout=settings.BEDROCK_TIMEOUT_SECONDS,
            retries={"max_attempts": 1},  # our own bounded retry loop replaces boto3's
        )
        return boto3.client(
            "bedrock-runtime",
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
            config=boto_config,
        )

    async def _with_retry(self, operation: Callable[[], _T]) -> _T:
        """Bounded retry with exponential backoff + jitter — docs/11-coding-standard.md §12.

        Only retries transient/throttling errors; validation-class errors
        raised by `operation` propagate immediately (docs/14-bedrock-integration.md §5).
        """
        last_error: BedrockInvocationError | None = None
        for attempt in range(settings.BEDROCK_MAX_RETRIES + 1):
            try:
                return await asyncio.to_thread(operation)
            except BedrockInvocationError as exc:
                if not exc.retryable:
                    raise
                last_error = exc
                if attempt == settings.BEDROCK_MAX_RETRIES:
                    break
                delay = self._backoff_seconds(attempt)
                logger.warning(
                    "Bedrock call failed (%s), retrying in %.3fs (attempt %d/%d)",
                    exc.error_code,
                    delay,
                    attempt + 1,
                    settings.BEDROCK_MAX_RETRIES,
                )
                await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        base: float = (settings.BEDROCK_RETRY_BACKOFF_BASE_MS / 1000) * (2**attempt)
        jitter: float = random.uniform(0, base * 0.1)
        return base + jitter

    async def embed(
        self, texts: list[str], *, input_type: Literal["search_document", "search_query"]
    ) -> list[list[float]]:
        """Embed `texts` via Cohere Embed v4. `input_type` must reflect which side of
        a similarity search this call is for — Cohere's own guidance is to embed
        corpus content with `search_document` and queries with `search_query`."""
        await self._circuit_breaker.before_call()
        bedrock_embedding_calls_total.inc()
        try:
            result = await self._with_retry(lambda: self._invoke_embed(texts, input_type))
        except BedrockInvocationError:
            await self._circuit_breaker.record_failure()
            raise
        await self._circuit_breaker.record_success()
        return result

    def _invoke_embed(
        self, texts: list[str], input_type: Literal["search_document", "search_query"]
    ) -> list[list[float]]:
        body = json.dumps(
            {
                "texts": texts,
                "input_type": input_type,
                "output_dimension": EMBEDDING_OUTPUT_DIMENSION,
                "embedding_types": ["float"],
            }
        )
        try:
            response = self._client.invoke_model(
                modelId=settings.BEDROCK_EMBEDDING_MODEL,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
        except (ClientError, BotoCoreError) as exc:
            code, retryable = _classify_exception(exc)
            raise BedrockInvocationError(str(exc), error_code=code, retryable=retryable) from exc
        payload = json.loads(response["body"].read())
        embeddings = payload["embeddings"]
        # Empirically (live smoke test against real Bedrock), Cohere Embed v4
        # returns the `{"float": [[...]]}` by-type shape even when only one
        # embedding_types entry was requested — AWS's own docs describe a
        # flat-list shape in that case, but the real API disagrees. Handle
        # both so this doesn't silently misparse if that ever changes back.
        if isinstance(embeddings, dict):
            embeddings = embeddings["float"]
        return list(embeddings)

    async def generate_stream(self, prompt: PromptPayload, **params: Any) -> AsyncIterator[str]:
        """Stream a text generation for `BEDROCK_TEXT_MODEL`.

        `BEDROCK_TEMPERATURE`/`BEDROCK_MAX_OUTPUT_TOKENS` always come from
        config and apply uniformly — docs/15-model-management.md §3 explicitly
        rules out a per-call override in this phase, so `**params` (kept to
        match the documented signature) must be empty.
        """
        if params:
            raise TypeError(
                f"generate_stream() accepts no per-call overrides in this phase: {sorted(params)}"
            )
        await self._circuit_breaker.before_call()
        bedrock_text_calls_total.inc()
        try:
            stream = await self._with_retry(lambda: self._start_stream(prompt))
        except BedrockInvocationError:
            await self._circuit_breaker.record_failure()
            raise

        try:
            async for chunk in self._consume_stream(stream):
                yield chunk
        except BedrockInvocationError:
            await self._circuit_breaker.record_failure()
            raise
        else:
            await self._circuit_breaker.record_success()

    def _start_stream(self, prompt: PromptPayload) -> Any:
        request: dict[str, Any] = {
            "modelId": settings.BEDROCK_TEXT_MODEL,
            "messages": [self._render_message(message) for message in prompt.messages],
            "inferenceConfig": {
                "maxTokens": settings.BEDROCK_MAX_OUTPUT_TOKENS,
                "temperature": settings.BEDROCK_TEMPERATURE,
            },
        }
        if prompt.system:
            request["system"] = [{"text": prompt.system}]
        try:
            return self._client.converse_stream(**request)
        except (ClientError, BotoCoreError) as exc:
            code, retryable = _classify_exception(exc)
            raise BedrockInvocationError(str(exc), error_code=code, retryable=retryable) from exc

    @staticmethod
    def _render_message(message: PromptMessage) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        for block in message.content:
            if block.text is not None:
                content.append({"text": block.text})
            if block.image_bytes is not None:
                content.append(
                    {
                        "image": {
                            "format": block.image_format,
                            "source": {"bytes": block.image_bytes},
                        }
                    }
                )
        return {"role": message.role, "content": content}

    async def _consume_stream(self, response: dict[str, Any]) -> AsyncIterator[str]:
        """Relay Bedrock's streaming chunks 1:1 — docs/14-bedrock-integration.md §4.

        Once any chunk has been yielded, any further error (raised or
        embedded as a stream event) is terminal and never retried, matching
        "does not attempt to resume or replay" mid-stream.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        sentinel = object()

        def _emit(item: Any) -> None:
            # If the consumer already stopped (generator closed/abandoned,
            # event loop torn down), there's nothing left to signal — drop
            # it rather than crash this daemon thread with an unhandled
            # RuntimeError("Event loop is closed").
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                pass

        def _produce() -> None:
            try:
                for event in response["stream"]:
                    _emit(event)
            except Exception as exc:  # noqa: BLE001 - relayed to the consumer, not swallowed
                _emit(exc)
            finally:
                _emit(sentinel)

        threading.Thread(target=_produce, daemon=True).start()

        while True:
            item = await queue.get()
            if item is sentinel:
                return
            if isinstance(item, Exception):
                code, _ = _classify_exception(item)
                raise BedrockInvocationError(str(item), error_code=code, retryable=False) from item
            self._raise_if_error_event(item)
            text = self._extract_text(item)
            if text:
                yield text

    @staticmethod
    def _extract_text(event: dict[str, Any]) -> str | None:
        delta = event.get("contentBlockDelta", {}).get("delta", {})
        text = delta.get("text")
        return text if isinstance(text, str) else None

    @staticmethod
    def _raise_if_error_event(event: dict[str, Any]) -> None:
        for code in (
            "internalServerException",
            "modelStreamErrorException",
            "throttlingException",
            "validationException",
        ):
            if code in event:
                raise BedrockInvocationError(str(event[code]), error_code=code, retryable=False)


bedrock_client = BedrockClient()

_CIRCUIT_STATE_GAUGE_VALUES = {
    CircuitState.CLOSED: 0,
    CircuitState.OPEN: 1,
    CircuitState.HALF_OPEN: 2,
}
# Evaluated live at each `/metrics` scrape (docs/09-observability.md §5) rather than set
# on every transition — `circuit_breaker_state` is already a cheap accessor, so a
# collect-time callback can't drift out of sync the way a manually-updated Gauge could.
bedrock_circuit_breaker_state.set_function(
    lambda: _CIRCUIT_STATE_GAUGE_VALUES[bedrock_client.circuit_breaker_state]
)
