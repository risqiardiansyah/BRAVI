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

from app.api.operator_router import router as operator_router
from app.api.system_router import router as system_router
from app.api.user_router import router as user_router
from app.errors import AppError, app_error_handler, internal_error_handler
from app.errors import request_validation_error_handler as _request_validation_error_handler
from app.middleware.rate_limit import RateLimitExceededError, rate_limit_exception_handler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("bravi-ai-chatbot starting up")
    yield
    # TODO(phase 13, docs/IMPLEMENTATION_PLAN.md): drain in-flight SSE streams here
    # before returning (docs/10-deployment.md §4.1 — bounded by a shutdown grace period
    # comfortably longer than BEDROCK_TIMEOUT_SECONDS plus generation time). Uvicorn's
    # own SIGTERM handling invokes this lifespan's shutdown phase, so this is the hook
    # Phase 13 extends; it only logs for now.
    logger.info("bravi-ai-chatbot shutting down")


app = FastAPI(title="bravi-ai-chatbot", lifespan=lifespan)
app.include_router(system_router)
app.include_router(operator_router)
app.include_router(user_router)

# Standard error envelope for every route — docs/11-coding-standard.md §6,
# docs/22-error-handling.md §2 (app/errors.py's module docstring has the full rationale).
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RateLimitExceededError, rate_limit_exception_handler)
app.add_exception_handler(RequestValidationError, _request_validation_error_handler)
app.add_exception_handler(Exception, internal_error_handler)
