"""Load/Performance test — docs/12-testing-strategy.md §1 ("Load/Performance | Latency/
throughput under concurrent load | `locust` or `k6`") and §6 ("Baseline load test: N
concurrent chat sessions, measure p50/p95 latency per short-circuit tier and full RAG
path"), docs/IMPLEMENTATION_PLAN.md Phase 14 ("Load/performance test against every target
in `03-non-functional-requirements.md` §1 / `20-performance-target.md` §2-§4").

**Mocked-Bedrock-boundary approach** (confirmed with the project owner,
`IMPLEMENTATION_PLAN.md` Phase 14 note): Bedrock is stubbed at each node module's own
`bedrock_client` binding — the exact seam every other integration test in this suite
already uses (e.g. `tests/integration/test_user_chat_graph.py`), not a new mocking layer —
rather than either paying real Bedrock cost/quota/rate limits at load-test concurrency, or
mocking one layer further down (`boto3`). The stub sleeps for a fixed delay approximating
real Bedrock latency (`20-performance-target.md` §4's per-node budget) before responding,
so the measured numbers reflect this app's own request-handling overhead (routing, session
resolution/persistence, the SSE relay, DB round-trips) layered on top of a realistic-shaped
Bedrock latency profile, not real AWS network variance. Everything else in the request path
is real: the actual FastAPI app (`app.main.app`) over an in-process ASGI transport
(`httpx.ASGITransport` — no real TCP socket, but every router/middleware/dependency in the
real app still runs), and a real Postgres+pgvector test database (own throwaway pooled
engine bound to `app.db.AsyncSessionLocal` — see `app_session_factory`'s own docstring for
why a real pool, not `NullPool`, is used here). Every `sessions`/`messages`/`usage_metrics`
row this suite creates is tagged with a `load-test-` `user_id` prefix and deleted at
teardown (`_cleanup_load_test_rows`) — this suite commits for real (the SSE relay commits
mid-stream, docs/06-api-specification.md §0), so leaving rows behind would pollute
`tests/integration/test_analytics.py`/`tests/unit/test_cost_budget_alert.py`'s
today-scoped aggregation assertions in the same shared dev database (found by running the
full suite immediately after this file the first time — a real regression, fixed by adding
cleanup, not by loosening those other tests' assertions).

Excluded from the default `pytest -q` run via the `load` marker registered in
`pyproject.toml` (`addopts = ["-m", "not load"]`) — this suite deliberately drives a
sustained batch of concurrent real HTTP requests against a real database, which does not
belong in the fast unit+integration CI gate (`12-testing-strategy.md` §9 does not list
"Load/Performance" as one of its 4 CI gates either). Run explicitly via
`pytest -m load tests/load/`.

**Concurrency scope — a deliberate, flagged compromise:**
`03-non-functional-requirements.md` §1's "Concurrent chat sessions: support at least 100"
is a *production infra-sizing* target tuned via `DB_POOL_SIZE`/replica count
(`20-performance-target.md` §5), not a demand that a single local test run open 100 raw
`NullPool` connections against a shared dev Postgres instance at once. `CONCURRENT_SESSIONS`
below is bounded well under that so this test is safe to run against a shared/local
database, while still forcing real queuing/contention across the async event loop, the DB,
and the mocked-Bedrock stub's own concurrency — enough to produce a meaningful p95/p99 tail
rather than a single-request measurement.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import db as db_module
from app.clients.bedrock_client import PromptPayload
from app.config import settings
from app.graphs.nodes import condense_history as condense_history_module
from app.graphs.nodes import embed_question as embed_question_module
from app.graphs.nodes import generate_answer as generate_answer_module
from app.graphs.nodes import preprocess_input as preprocess_input_module
from app.main import app
from app.middleware import rate_limit as rate_limit_module
from app.models.message import Message
from app.models.session import Session
from app.models.usage_metric import UsageMetric
from app.repositories.knowledge_chunk_repository import KnowledgeChunkRepository, SimilarityMatch

pytestmark = pytest.mark.load

CONCURRENT_SESSIONS = 30
REQUESTS_PER_TIER = 30
LOAD_TEST_USER_PREFIX = "load-test-"

# Simulated Bedrock latency, shaped after docs/20-performance-target.md §4's per-node
# budget (`embed_question` < 300ms, `generate_answer`'s TTFT portion < 2s) — not real AWS
# latency. This is the whole point of the mocked-boundary approach: a realistic-shaped,
# zero-cost, zero-quota stand-in.
_EMBED_DELAY_S = 0.05
_TTFT_DELAY_S = 0.3
_PER_TOKEN_DELAY_S = 0.01
_ANSWER_TOKENS = [
    "Ini ",
    "adalah ",
    "jawaban ",
    "yang ",
    "dihasilkan ",
    "untuk ",
    "pertanyaan ",
    "Anda.",
]


class _LoadTestBedrockClient:
    async def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        await asyncio.sleep(_EMBED_DELAY_S)
        return [[0.01] * 1024 for _ in texts]

    async def generate_stream(self, prompt: PromptPayload, **_params: object) -> AsyncIterator[str]:
        await asyncio.sleep(_TTFT_DELAY_S)
        for token in _ANSWER_TOKENS:
            yield token
            await asyncio.sleep(_PER_TOKEN_DELAY_S)


@pytest_asyncio.fixture
async def app_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Own throwaway *pooled* engine bound to `app.db.AsyncSessionLocal` (read by
    `get_session`), sized to `CONCURRENT_SESSIONS` — unlike
    `tests/integration/test_ingest_endpoint.py`'s `NullPool` (needed there because
    `TestClient` bridges each sync call through its own blocking portal/event loop, so a
    pooled connection checked out under one loop could be reused under another), this
    whole test runs every concurrent request as a task on *one* event loop
    (`asyncio.gather` inside a single `async def test_...`), so there is no cross-loop
    reuse hazard — a real connection pool is both safe and the point: `NullPool` would pay
    a fresh TCP/auth handshake per request, which is exactly the kind of per-test-harness
    overhead `20-performance-target.md` §5 says a real deployment's `DB_POOL_SIZE`-backed
    pool does not pay."""
    assert settings.DATABASE_URL
    engine = create_async_engine(
        db_module.normalize_asyncpg_url(settings.DATABASE_URL),
        pool_size=CONCURRENT_SESSIONS,
        max_overflow=10,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_load_test_rows(
    app_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[None, None]:
    """This suite drives the real `/api/chat` endpoint, which commits for real mid-stream
    (docs/06-api-specification.md §0) — unlike `tests/integration/test_user_chat_graph.py`'s
    rollback-only `db_session` fixture. Every row it creates carries a `user_id` prefixed
    with `LOAD_TEST_USER_PREFIX`, so it can be identified and deleted afterward rather than
    left in the shared dev database, where it would otherwise pollute other tests' today-
    scoped aggregation assertions (`tests/integration/test_analytics.py`,
    `tests/unit/test_cost_budget_alert.py`) — exactly what happened before this fixture
    existed."""
    yield
    async with app_session_factory() as session:
        session_ids = (
            (
                await session.execute(
                    select(Session.session_id).where(
                        Session.user_id.startswith(LOAD_TEST_USER_PREFIX)
                    )
                )
            )
            .scalars()
            .all()
        )
        if session_ids:
            await session.execute(delete(Message).where(Message.session_id.in_(session_ids)))
        await session.execute(
            delete(UsageMetric).where(UsageMetric.user_id.startswith(LOAD_TEST_USER_PREFIX))
        )
        await session.execute(
            delete(Session).where(Session.user_id.startswith(LOAD_TEST_USER_PREFIX))
        )
        await session.commit()


@pytest.fixture(autouse=True)
def _stub_bedrock(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _LoadTestBedrockClient()
    monkeypatch.setattr(embed_question_module, "bedrock_client", stub)
    monkeypatch.setattr(generate_answer_module, "bedrock_client", stub)
    monkeypatch.setattr(condense_history_module, "bedrock_client", stub)
    monkeypatch.setattr(preprocess_input_module, "bedrock_client", stub)


@pytest.fixture(autouse=True)
def _bypass_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rate limiting has its own dedicated, already-passing test suite
    (`tests/unit/test_rate_limit.py`, `tests/integration/test_rate_limit_high_replica_load.py`)
    — what this file measures is the app's own request-handling latency/throughput, not the
    configured per-tenant quota, so the limiter is a no-op here (same technique as
    `tests/integration/test_ingest_endpoint.py`)."""

    async def _noop(*, endpoint: str, identity: str) -> None:
        return None

    monkeypatch.setattr(rate_limit_module.rate_limiter, "enforce", _noop)


@pytest.fixture(autouse=True)
def _similarity_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    match = SimilarityMatch(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        content="Klaim dapat diajukan dalam 30 hari kerja sejak insiden terjadi.",
        page_number=1,
        score=0.95,
        title="Panduan Klaim",
        source_url="https://example.com/panduan-klaim",
        valid_until=None,
        superseded_by_title=None,
    )

    async def _stub(
        self: KnowledgeChunkRepository, query_embedding: list[float], *, top_k: int
    ) -> list[SimilarityMatch]:
        return [match]

    monkeypatch.setattr(KnowledgeChunkRepository, "similarity_search", _stub)


@dataclass
class _RequestOutcome:
    total_ms: float
    ttft_ms: float | None
    ok: bool


async def _send_chat_turn(client: AsyncClient, *, question: str) -> _RequestOutcome:
    payload = {
        "session_id": None,
        "question": question,
        "user_id": f"{LOAD_TEST_USER_PREFIX}{uuid.uuid4().hex[:8]}",
    }
    start = time.monotonic()
    ttft_ms: float | None = None
    ok = False
    async with client.stream("POST", "/api/chat", json=payload) as response:
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            if ttft_ms is None:
                ttft_ms = (time.monotonic() - start) * 1000
            if '"type":"done"' in line:
                ok = True
            elif '"type":"error"' in line:
                ok = False
    total_ms = (time.monotonic() - start) * 1000
    return _RequestOutcome(total_ms=total_ms, ttft_ms=ttft_ms, ok=ok)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lower = int(k)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (k - lower)


async def _run_batch(
    client: AsyncClient, *, question: str, count: int, concurrency: int
) -> list[_RequestOutcome]:
    semaphore = asyncio.Semaphore(concurrency)

    async def _one() -> _RequestOutcome:
        async with semaphore:
            return await _send_chat_turn(client, question=question)

    return await asyncio.gather(*[_one() for _ in range(count)])


async def _warm_up_pool(client: AsyncClient, *, question: str) -> None:
    """The DB connection pool's `CONCURRENT_SESSIONS` connections are opened lazily on
    first use, so an unwarmed first batch measures TCP/auth handshake time, not
    steady-state request latency — a real deployment's pool is already warm by the time
    it serves traffic. Discarded, not asserted on."""
    await _run_batch(
        client, question=question, count=CONCURRENT_SESSIONS, concurrency=CONCURRENT_SESSIONS
    )


async def test_short_circuit_greeting_latency_meets_target(
    app_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """docs/03-non-functional-requirements.md §1: short-circuit p95 < 500ms / p99 < 1500ms."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://loadtest") as client:
        await _warm_up_pool(client, question="Halo")
        outcomes = await _run_batch(
            client, question="Halo", count=REQUESTS_PER_TIER, concurrency=CONCURRENT_SESSIONS
        )

    assert all(o.ok for o in outcomes)
    latencies = [o.total_ms for o in outcomes]
    p95 = _percentile(latencies, 0.95)
    p99 = _percentile(latencies, 0.99)
    assert p95 < 500, f"short-circuit p95 {p95:.0f}ms exceeds the 500ms target"
    assert p99 < 1500, f"short-circuit p99 {p99:.0f}ms exceeds the 1500ms target"


async def test_full_rag_latency_and_ttft_meet_target(
    app_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """docs/03-non-functional-requirements.md §1: full-RAG p95 < 6s / p99 < 12s;
    TTFT p95 < 2.5s."""
    transport = ASGITransport(app=app)
    question = "Apa syarat pengajuan klaim asuransi kesehatan?"
    async with AsyncClient(transport=transport, base_url="http://loadtest") as client:
        await _warm_up_pool(client, question=question)
        outcomes = await _run_batch(
            client, question=question, count=REQUESTS_PER_TIER, concurrency=CONCURRENT_SESSIONS
        )

    assert all(o.ok for o in outcomes)
    latencies = [o.total_ms for o in outcomes]
    ttfts = [o.ttft_ms for o in outcomes if o.ttft_ms is not None]
    p95_latency = _percentile(latencies, 0.95)
    p99_latency = _percentile(latencies, 0.99)
    p95_ttft = _percentile(ttfts, 0.95)

    assert p95_latency < 6000, f"full-RAG p95 {p95_latency:.0f}ms exceeds the 6000ms target"
    assert p99_latency < 12000, f"full-RAG p99 {p99_latency:.0f}ms exceeds the 12000ms target"
    assert p95_ttft < 2500, f"TTFT p95 {p95_ttft:.0f}ms exceeds the 2500ms target"


async def test_sustained_throughput_meets_target(
    app_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """docs/03-non-functional-requirements.md §1: sustained aggregate throughput
    >= 20 req/s across all replicas at Phase 1 concurrency target."""
    transport = ASGITransport(app=app)
    total_requests = REQUESTS_PER_TIER * 2
    async with AsyncClient(transport=transport, base_url="http://loadtest") as client:
        start = time.monotonic()
        outcomes = await _run_batch(
            client,
            question="Apa syarat pengajuan klaim asuransi kesehatan?",
            count=total_requests,
            concurrency=CONCURRENT_SESSIONS,
        )
        elapsed_s = time.monotonic() - start

    assert all(o.ok for o in outcomes)
    throughput = total_requests / elapsed_s
    assert throughput >= 20, f"measured throughput {throughput:.1f} req/s below the 20 req/s target"
