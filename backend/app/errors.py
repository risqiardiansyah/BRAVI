"""Domain exceptions + FastAPI exception handlers for the standard error envelope.

Every error response body across the API is `{ "error": { "code", "message" } }`
(docs/11-coding-standard.md §6); the full `code` registry is
docs/22-error-handling.md §2. Routers raise an `AppError` subclass; `app/main.py`
registers `app_error_handler` once against the `AppError` base class — Starlette's
exception-handler lookup walks `type(exc).__mro__`, so one registration catches every
subclass without a handler needing to be added per exception type.

`06-api-specification.md` §10's status-code summary never lists `422` anywhere in the
API — FastAPI's own automatic request-validation errors default to `422`, which would
silently break the "matches exactly" contract every route in this system is held to.
`request_validation_error_handler` remaps those to `400`/`INVALID_REQUEST` so that
never happens, and `internal_error_handler` guarantees an unhandled exception still
produces the documented `INTERNAL_ERROR`/500 envelope instead of Starlette's default
plain-text response, while never leaking the raw exception message in production
(docs/11-coding-standard.md §6).
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base domain exception — every subclass carries a fixed `code`/`status_code`
    pair from the docs/22-error-handling.md §2 registry."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)


class InvalidRequestError(AppError):
    """`INVALID_REQUEST` / 400 — missing/invalid field or malformed request body."""

    code = "INVALID_REQUEST"
    status_code = 400


class KnowledgeNotFoundError(AppError):
    """`KNOWLEDGE_NOT_FOUND` / 404 — unknown or already-deleted `knowledge_id`."""

    code = "KNOWLEDGE_NOT_FOUND"
    status_code = 404


class SessionNotFoundError(AppError):
    """`SESSION_NOT_FOUND` / 404 — a client-supplied `session_id` does not exist
    (docs/06-api-specification.md §2/§5: no silent auto-create in this case)."""

    code = "SESSION_NOT_FOUND"
    status_code = 404


class IdempotencyKeyConflictError(AppError):
    """`IDEMPOTENCY_KEY_CONFLICT` / 409 — same `Idempotency-Key` reused with
    different content (docs/22-error-handling.md §4)."""

    code = "IDEMPOTENCY_KEY_CONFLICT"
    status_code = 409


class FileTooLargeError(AppError):
    """`FILE_TOO_LARGE` / 413 — exceeds `MAX_IMAGE_UPLOAD_MB`/`MAX_FILE_UPLOAD_MB`
    (docs/22-error-handling.md §2, docs/08-security.md §3)."""

    code = "FILE_TOO_LARGE"
    status_code = 413


class UnsupportedMediaTypeError(AppError):
    """`UNSUPPORTED_MEDIA_TYPE` / 415 — MIME type not in the allowlist
    (docs/22-error-handling.md §2, docs/08-security.md §3)."""

    code = "UNSUPPORTED_MEDIA_TYPE"
    status_code = 415


class MalwareDetectedError(AppError):
    """`MALWARE_DETECTED` / 415 — file failed content scanning
    (docs/22-error-handling.md §2, docs/08-security.md §8a)."""

    code = "MALWARE_DETECTED"
    status_code = 415


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": str(exc)}},
    )


async def request_validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return JSONResponse(
        status_code=400,
        content={"error": {"code": "INVALID_REQUEST", "message": str(exc)}},
    )


async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception processing %s %s", request.method, request.url.path)
    message = "Internal server error." if settings.APP_ENV == "production" else str(exc)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": message}},
    )
