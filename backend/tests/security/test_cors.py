"""CORS enforcement — docs/08-security.md §6a, docs/12-testing-strategy.md §5
("assert disallowed origins are rejected and `CORS_ALLOWED_ORIGINS` origins are
accepted"), docs/IMPLEMENTATION_PLAN.md Phase 12.

The real `app.main.app` builds its `CORSMiddleware` (if any) once at import time from
whatever `CORS_ALLOWED_ORIGINS` happens to be in this environment's `.env` — not
something a single test module can flip per-case against the already-built app. These
tests instead exercise `app.main.parse_cors_origins` (the exact parsing `app.main`
wires in) against a small standalone app carrying the identical `CORSMiddleware`
configuration, plus the fail-fast config validation for the wildcard-in-staging/
production rule.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import parse_cors_origins


def _build_app(allowed_origins: list[str]) -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    return app


def test_parse_cors_origins_splits_and_strips() -> None:
    assert parse_cors_origins("https://a.example.com, https://b.example.com") == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_parse_cors_origins_empty_yields_no_origins() -> None:
    assert parse_cors_origins("") == []
    assert parse_cors_origins("   ") == []


def test_allowed_origin_accepted() -> None:
    client = TestClient(_build_app(parse_cors_origins("https://allowed.example.com")))
    response = client.get("/ping", headers={"Origin": "https://allowed.example.com"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://allowed.example.com"


def test_disallowed_origin_rejected() -> None:
    client = TestClient(_build_app(parse_cors_origins("https://allowed.example.com")))
    response = client.get("/ping", headers={"Origin": "https://evil.example.com"})

    # Starlette's CORSMiddleware still lets the same-origin-server response through
    # (CORS is a browser-enforced restriction), but omits the header that tells a
    # browser it may read the response cross-origin.
    assert "access-control-allow-origin" not in response.headers


def test_no_configured_origins_never_grants_cross_origin_access() -> None:
    client = TestClient(_build_app(parse_cors_origins("")))
    response = client.get("/ping", headers={"Origin": "https://anything.example.com"})

    assert "access-control-allow-origin" not in response.headers


# --- Fail-fast config validation — no wildcard in staging/production ------------------


def _settings(**overrides: object) -> Settings:
    """Mirrors `tests/unit/test_config.py`'s helper — explicit values only, no
    dotenv/env-file, so `DATABASE_URL`/`REDIS_URL` are absent here (fine outside
    `APP_ENV=production`, which these overrides never combine with `CORS_ALLOWED_ORIGINS`
    absent)."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_wildcard_cors_rejected_in_production() -> None:
    with pytest.raises(ValueError, match="wildcard"):
        _settings(
            APP_ENV="production",
            CORS_ALLOWED_ORIGINS="*",
            DATABASE_URL="postgresql://user:pass@host:5432/db",
            REDIS_URL="redis://localhost:6379/0",
        )


def test_wildcard_cors_rejected_in_staging() -> None:
    with pytest.raises(ValueError, match="wildcard"):
        _settings(APP_ENV="staging", CORS_ALLOWED_ORIGINS="*")


def test_wildcard_cors_allowed_in_development() -> None:
    settings = _settings(APP_ENV="development", CORS_ALLOWED_ORIGINS="*")
    assert settings.CORS_ALLOWED_ORIGINS == "*"


def test_specific_origins_allowed_in_production() -> None:
    settings = _settings(
        APP_ENV="production",
        CORS_ALLOWED_ORIGINS="https://app.example.com",
        DATABASE_URL="postgresql://user:pass@host:5432/db",
        REDIS_URL="redis://localhost:6379/0",
    )
    assert settings.CORS_ALLOWED_ORIGINS == "https://app.example.com"
