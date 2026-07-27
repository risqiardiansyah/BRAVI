"""Shared `/api/chat` + `/api/opr/chat` request parsing — dual wire format (JSON, or
`multipart/form-data` when an optional image is attached), docs/06-api-specification.md
§2/§5.

Factored out of `app/api/user_router.py` (Phase 9) so `/api/opr/chat` (Phase 10) reuses
the identical parsing/validation instead of duplicating it (docs/11-coding-standard.md
§4's reuse-over-duplication rule) — `06-api-specification.md` §5's own prose ("Same
multimodal handling as `/api/chat` applies if an image is attached") calls for the same
image-upload path on the Operator endpoint even though its JSON request-body example
doesn't list a `file` field explicitly.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app.clients.malware_scanner import scan_bytes
from app.config import settings
from app.errors import (
    FileTooLargeError,
    InvalidRequestError,
    MalwareDetectedError,
    UnsupportedMediaTypeError,
)
from app.schemas.chat import ChatRequestFields

# docs/08-security.md §3 — chat image upload MIME allowlist, mapped to the
# `image_format` literal `clients/bedrock_client.py`'s multimodal payload expects.
_ALLOWED_IMAGE_MIME_TYPES = {"image/png": "png", "image/jpeg": "jpeg", "image/webp": "webp"}


async def parse_chat_request(request: Request) -> tuple[ChatRequestFields, UploadFile | None]:
    """`/api/chat`/`/api/opr/chat` accept `multipart/form-data` (when `file` is attached)
    or plain JSON otherwise (docs/06-api-specification.md §2/§5) — genuinely dual wire
    formats on one route isn't expressible via FastAPI's declarative `Body`/`Form` params
    (which commit to one shape at decoration time), so the raw `Request` is parsed
    manually here and validated against the same `ChatRequestFields` model either way.
    """
    content_type = request.headers.get("content-type", "")
    raw: dict[str, Any]
    file: UploadFile | None = None

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        raw = {
            "session_id": form.get("session_id") or None,
            "question": form.get("question"),
            "user_id": form.get("user_id"),
        }
        maybe_file = form.get("file")
        if isinstance(maybe_file, UploadFile) and maybe_file.filename:
            file = maybe_file
    else:
        try:
            raw = await request.json()
        except ValueError as exc:
            raise InvalidRequestError("Request body must be valid JSON.") from exc
        if not isinstance(raw, dict):
            raise InvalidRequestError("Request body must be a JSON object.")

    try:
        fields = ChatRequestFields.model_validate(raw)
    except ValidationError as exc:
        raise InvalidRequestError(str(exc)) from exc

    if not fields.question.strip():
        raise InvalidRequestError("Missing required field `question`.")
    if not fields.user_id.strip():
        raise InvalidRequestError("Missing required field `user_id`.")

    return fields, file


async def read_and_validate_image(file: UploadFile | None) -> tuple[bytes | None, str | None]:
    """MIME allowlist + size-limit + content-scanning checks (docs/08-security.md §3/§8a)
    for the optional image upload — 415/413 per docs/06-api-specification.md §2's error
    table (§5 shares the same error table, no Operator-specific variant is documented).
    """
    if file is None:
        return None, None

    content_type = file.content_type or ""
    image_format = _ALLOWED_IMAGE_MIME_TYPES.get(content_type)
    if image_format is None:
        raise UnsupportedMediaTypeError(f"Unsupported file type: {content_type!r}.")

    raw_bytes = await file.read()
    max_bytes = settings.MAX_IMAGE_UPLOAD_MB * 1024 * 1024
    if len(raw_bytes) > max_bytes:
        raise FileTooLargeError(f"File exceeds the {settings.MAX_IMAGE_UPLOAD_MB}MB limit.")

    if not scan_bytes(raw_bytes):
        raise MalwareDetectedError("Uploaded file failed content scanning.")

    return raw_bytes, image_format
