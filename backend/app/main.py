"""FastAPI application entrypoint.

`user_router`'s `POST /api/chat`/`GET /api/trending` are added by the phases that
build them (9/11) — this module wires the system router (docs/IMPLEMENTATION_PLAN.md
Phase 5), the operator router's knowledge-management endpoints (Phase 7,
`POST /api/opr/chat` itself still lands in Phase 10), and, as of Phase 8, the user
router's session-listing/message-history endpoints.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.operator_router import router as operator_router
from app.api.system_router import router as system_router
from app.api.user_router import router as user_router
from app.config import settings
from app.errors import AppError, app_error_handler, internal_error_handler
from app.errors import request_validation_error_handler as _request_validation_error_handler
from app.middleware.rate_limit import RateLimitExceededError, rate_limit_exception_handler
from app.shutdown import shutdown_state

logger = logging.getLogger(__name__)


def graceful_shutdown_grace_period_seconds() -> float:
    """Bound for draining in-flight SSE streams on `SIGTERM` (docs/10-deployment.md
    §4.1: "comfortably longer than `BEDROCK_TIMEOUT_SECONDS` plus generation time").
    No dedicated setting exists for this in `10-deployment.md` §3/`23-configuration.md`
    — derived from the worst-case Bedrock retry loop (`BEDROCK_TIMEOUT_SECONDS` ×
    (`BEDROCK_MAX_RETRIES` + 1), docs/14-bedrock-integration.md §5) plus a fixed buffer
    for the surrounding non-Bedrock node latency (embedding call, condensation, DB
    writes) that also runs during a single chat turn."""
    return settings.BEDROCK_TIMEOUT_SECONDS * (settings.BEDROCK_MAX_RETRIES + 1) + 30


def parse_cors_origins(raw: str) -> list[str]:
    """Splits `CORS_ALLOWED_ORIGINS` (docs/08-security.md §6a) into a browser-origin
    list for `CORSMiddleware` — empty/unset yields `[]` (no cross-origin browser
    access allowed). Factored out so `tests/security/test_cors.py` can exercise the
    exact parsing this module wires at import time."""
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("bravi-ai-chatbot starting up")
    yield
    # docs/10-deployment.md §4.1: uvicorn's own SIGTERM handling stops accepting new
    # connections and invokes this lifespan shutdown phase; the one thing this app must
    # still do itself is not return from here until every in-flight SSE stream
    # (app/shutdown.py's `track_stream`, wired into `chat_service._stream_chat_graph`)
    # has finished, bounded by a grace period — otherwise a still-streaming response
    # risks being cut off once the surrounding process actually exits.
    shutdown_state.begin_shutdown()
    logger.info(
        "bravi-ai-chatbot shutting down: draining %d in-flight SSE stream(s)",
        shutdown_state.active_stream_count,
    )
    await shutdown_state.wait_drained(timeout_seconds=graceful_shutdown_grace_period_seconds())
    logger.info("bravi-ai-chatbot shutdown complete")


app = FastAPI(title="bravi-ai-chatbot", lifespan=lifespan)
app.include_router(system_router)
app.include_router(operator_router)
app.include_router(user_router)

# docs/08-security.md §6a — empty/unset `CORS_ALLOWED_ORIGINS` means no cross-origin
# browser access is allowed (server-to-server calls are unaffected). `app/config.py`'s
# startup validation already fails fast on a wildcard origin in staging/production.
_cors_origins = parse_cors_origins(settings.CORS_ALLOWED_ORIGINS)
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Standard error envelope for every route — docs/11-coding-standard.md §6,
# docs/22-error-handling.md §2 (app/errors.py's module docstring has the full rationale).
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RateLimitExceededError, rate_limit_exception_handler)
app.add_exception_handler(RequestValidationError, _request_validation_error_handler)
app.add_exception_handler(Exception, internal_error_handler)
