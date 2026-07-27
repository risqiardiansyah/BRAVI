"""Direct unit tests for `app.services.chat_service` internals not already exercised
by direct-call integration tests (`test_session_resolution.py`, `test_session_title.py`,
`test_graceful_shutdown.py`) — docs/06-api-specification.md §0/§2/§3/§5,
docs/22-error-handling.md §2/§6, docs/IMPLEMENTATION_PLAN.md Phase 14.

`get_session_messages`/`_map_exception_to_error_event`/`_stream_chat_graph`'s
keepalive, short-circuited-answer, and exception-mapping branches are otherwise only
reachable via `/api/messages`/`/api/chat`/`/api/opr/chat` through `TestClient` — which
runs the ASGI app on its own dedicated event-loop thread (`anyio`'s blocking portal)
whenever called from an `async def` test, a thread `coverage.py` never traces unless
`concurrency = thread` is configured (it isn't, here). Calling these functions
directly, in-process, avoids that gap — mirroring
`tests/integration/test_graceful_shutdown.py`'s own direct call to
`chat_service.stream_user_chat_response`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.clients.bedrock_client import BedrockInvocationError, BedrockUnavailableError
from app.config import settings
from app.db import normalize_asyncpg_url
from app.errors import SessionNotFoundError
from app.graphs.chat_state import ChatState
from app.models.session import Session
from app.repositories.session_repository import SessionRepository
from app.services import chat_service


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    assert settings.DATABASE_URL
    engine = create_async_engine(normalize_asyncpg_url(settings.DATABASE_URL), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# --- get_session_messages (lines 92-95) -----------------------------------------


async def test_get_session_messages_unknown_session_raises_session_not_found(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(SessionNotFoundError):
        await chat_service.get_session_messages(db_session, session_id=uuid.uuid4())


async def test_get_session_messages_returns_ordered_history(db_session: AsyncSession) -> None:
    created = await chat_service.resolve_session(
        db_session, session_id=None, user_id=_unique("user"), persona="user"
    )
    await chat_service.persist_message(
        db_session, session_id=created.session_id, role="user", content="Pertanyaan"
    )
    await chat_service.persist_message(
        db_session, session_id=created.session_id, role="assistant", content="Jawaban"
    )

    messages = await chat_service.get_session_messages(db_session, session_id=created.session_id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert [m.content for m in messages] == ["Pertanyaan", "Jawaban"]


# --- _map_exception_to_error_event (lines 98-118) -------------------------------


@pytest.mark.parametrize("app_env", ["development", "production"])
def test_map_exception_bedrock_unavailable(monkeypatch: pytest.MonkeyPatch, app_env: str) -> None:
    monkeypatch.setattr(settings, "APP_ENV", app_env)
    code, message = chat_service._map_exception_to_error_event(BedrockUnavailableError())
    assert code == "BEDROCK_UNAVAILABLE"
    # Never redacted, even in production - docs/22-error-handling.md §6's own message.
    assert "circuit breaker is open" in message


def test_map_exception_bedrock_invocation_retryable_maps_to_bedrock_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "development")
    exc = BedrockInvocationError("throttled", error_code="THROTTLING", retryable=True)
    code, message = chat_service._map_exception_to_error_event(exc)
    assert code == "BEDROCK_TIMEOUT"
    assert message == "throttled"


def test_map_exception_bedrock_invocation_non_retryable_maps_to_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "development")
    exc = BedrockInvocationError("bad request", error_code="VALIDATION", retryable=False)
    code, message = chat_service._map_exception_to_error_event(exc)
    assert code == "INTERNAL_ERROR"
    assert message == "bad request"


def test_map_exception_bedrock_invocation_redacts_message_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    exc = BedrockInvocationError("internal detail", error_code="INTERNAL", retryable=False)
    code, message = chat_service._map_exception_to_error_event(exc)
    assert code == "INTERNAL_ERROR"
    assert message == "Internal server error."


def test_map_exception_generic_exception_maps_to_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "development")
    code, message = chat_service._map_exception_to_error_event(RuntimeError("boom"))
    assert code == "INTERNAL_ERROR"
    assert message == "boom"


def test_map_exception_generic_exception_redacts_message_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    code, message = chat_service._map_exception_to_error_event(RuntimeError("boom"))
    assert code == "INTERNAL_ERROR"
    assert message == "Internal server error."


# --- _stream_chat_graph: keepalive / short-circuit-answer / exception ----------


class _FakeSession:
    """Stands in for `AsyncSession` in `_stream_chat_graph`, which only ever calls
    `commit()`/`rollback()` on it — a real DB session is unnecessary noise for these
    pure control-flow tests."""

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class _FakeGraph:
    def __init__(self, items: list[tuple[str, Any]], *, delay_before_first: float = 0.0) -> None:
        self._items = items
        self._delay_before_first = delay_before_first

    def astream(
        self, initial_state: ChatState, config: dict[str, Any], stream_mode: list[str]
    ) -> AsyncIterator[tuple[str, Any]]:
        async def _gen() -> AsyncIterator[tuple[str, Any]]:
            first = True
            for item in self._items:
                if first and self._delay_before_first:
                    await asyncio.sleep(self._delay_before_first)
                first = False
                yield item

        return _gen()


class _ExplodingGraph:
    def astream(
        self, initial_state: ChatState, config: dict[str, Any], stream_mode: list[str]
    ) -> AsyncIterator[tuple[str, Any]]:
        async def _gen() -> AsyncIterator[tuple[str, Any]]:
            raise RuntimeError("graph exploded mid-stream")
            yield ("custom", {"content": "unreachable"})  # pragma: no cover

        return _gen()


def _initial_state() -> ChatState:
    return {
        "session_id": uuid.uuid4(),
        "user_id": "test-user",
        "persona": "user",
        "question": "Halo",
        "original_question": "Halo",
        "image_bytes": None,
        "image_format": None,
        "short_circuited": False,
        "short_circuit_reason": None,
        "mode": None,
    }


async def test_stream_chat_graph_emits_keepalive_before_slow_first_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SSE_KEEPALIVE_INTERVAL_SECONDS", 0.01)
    session_id = uuid.uuid4()
    graph = _FakeGraph(
        [
            (
                "values",
                {
                    "short_circuited": False,
                    "answer": "jawaban",
                    "sources": [],
                    "short_circuit_reason": None,
                    "mode": "qa",
                },
            )
        ],
        delay_before_first=0.05,
    )
    fake_session = _FakeSession()

    events = [
        event
        async for event in chat_service._stream_chat_graph(
            graph, fake_session, session_id=session_id, initial_state=_initial_state()
        )
    ]

    assert ": keepalive" in events[0]
    assert any('"type":"done"' in event for event in events)
    assert fake_session.committed is True


async def test_stream_chat_graph_reemits_answer_as_token_when_short_circuited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SSE_KEEPALIVE_INTERVAL_SECONDS", 15)
    session_id = uuid.uuid4()
    graph = _FakeGraph(
        [
            (
                "values",
                {
                    "short_circuited": True,
                    "short_circuit_reason": "greeting",
                    "answer": "Halo juga!",
                    "mode": None,
                },
            )
        ]
    )
    fake_session = _FakeSession()

    events = [
        event
        async for event in chat_service._stream_chat_graph(
            graph, fake_session, session_id=session_id, initial_state=_initial_state()
        )
    ]

    # The short-circuited answer is emitted as its own `token` event (no custom-mode
    # streaming happened for canned responses), followed by the terminal `done` event.
    assert any('"type":"token"' in event and "Halo juga!" in event for event in events)
    done_event = next(event for event in events if '"type":"done"' in event)
    assert '"short_circuited":true' in done_event
    assert '"sources":null' in done_event


async def test_stream_chat_graph_maps_exception_to_error_event_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "development")
    session_id = uuid.uuid4()
    fake_session = _FakeSession()

    events = [
        event
        async for event in chat_service._stream_chat_graph(
            _ExplodingGraph(), fake_session, session_id=session_id, initial_state=_initial_state()
        )
    ]

    assert len(events) == 1
    assert '"type":"error"' in events[0]
    assert '"code":"INTERNAL_ERROR"' in events[0]
    assert "graph exploded mid-stream" in events[0]
    assert fake_session.rolled_back is True
    assert fake_session.committed is False


# --- stream_operator_chat_response (lines 244-274) ------------------------------


class _StubBedrockClient:
    def __init__(self) -> None:
        self.embed_calls: list[tuple[list[str], str]] = []

    async def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        self.embed_calls.append((list(texts), input_type))
        return [[0.01] * 1024 for _ in texts]

    async def generate_stream(self, prompt: Any, **_params: Any) -> AsyncIterator[str]:
        for token in ("Ini ", "jawaban operator."):
            yield token


async def test_stream_operator_chat_response_runs_operator_graph_end_to_end(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.graphs.nodes import condense_history as condense_history_module
    from app.graphs.nodes import embed_question as embed_question_module
    from app.graphs.nodes import generate_answer as generate_answer_module
    from app.graphs.nodes import preprocess_input as preprocess_input_module
    from app.repositories.knowledge_chunk_repository import KnowledgeChunkRepository

    stub = _StubBedrockClient()
    monkeypatch.setattr(embed_question_module, "bedrock_client", stub)
    monkeypatch.setattr(generate_answer_module, "bedrock_client", stub)
    monkeypatch.setattr(condense_history_module, "bedrock_client", stub)
    monkeypatch.setattr(preprocess_input_module, "bedrock_client", stub)

    async def _no_matches(
        self: KnowledgeChunkRepository, query_embedding: list[float], *, top_k: int
    ) -> list[Any]:
        return []

    monkeypatch.setattr(KnowledgeChunkRepository, "similarity_search", _no_matches)

    async def _noop_commit() -> None:
        return None

    monkeypatch.setattr(db_session, "commit", _noop_commit)

    session_row = await SessionRepository(db_session).create(
        Session(user_id="test-operator", persona="operator")
    )
    await db_session.flush()

    events = [
        event
        async for event in chat_service.stream_operator_chat_response(
            db_session,
            session_id=session_row.session_id,
            user_id="test-operator",
            original_question="Tolong buatkan puisi tentang cinta",
            question="Tolong buatkan puisi tentang cinta",
            image_bytes=None,
            image_format=None,
        )
    ]

    assert any('"type":"done"' in event for event in events)
    done_event = next(event for event in events if '"type":"done"' in event)
    assert '"short_circuited":true' in done_event
    assert '"short_circuit_reason":"out_of_topic"' in done_event
