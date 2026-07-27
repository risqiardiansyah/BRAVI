"""Typed application settings — the one place environment variables are read.

Every configuration value in the system flows through this module via
`pydantic_settings.BaseSettings`. No other module may read `os.environ`
directly (docs/11-coding-standard.md §5). Startup-validation rules are
enforced in `_validate` below (docs/23-configuration.md §4).
"""

from __future__ import annotations

import logging
from typing import Literal

from apscheduler.triggers.cron import CronTrigger
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application configuration, sourced from process env vars / `.env`.

    Field names match the environment variable names exactly
    (docs/10-deployment.md §3 is the authoritative default-value list).
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- App ---
    APP_ENV: Literal["local", "development", "staging", "production"] = "development"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    PORT: int = 8000

    # --- Database ---
    DATABASE_URL: str | None = None
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_STATEMENT_TIMEOUT_MS: int = 30000

    # --- AWS Bedrock ---
    AWS_REGION: str = "ap-southeast-3"
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None
    BEDROCK_EMBEDDING_MODEL: str = (
        "arn:aws:bedrock:ap-southeast-3:586794442374:" "inference-profile/global.cohere.embed-v4:0"
    )
    BEDROCK_TEXT_MODEL: str = "global.anthropic.claude-sonnet-4-6"

    # --- Bedrock resilience & behavior ---
    BEDROCK_TIMEOUT_SECONDS: int = 30
    BEDROCK_MAX_RETRIES: int = 3
    BEDROCK_RETRY_BACKOFF_BASE_MS: int = 500
    BEDROCK_MAX_OUTPUT_TOKENS: int = 1024
    BEDROCK_TEMPERATURE: float = 0.2
    BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    BEDROCK_CIRCUIT_BREAKER_COOLDOWN_SECONDS: int = 30

    # --- Ingestion ---
    DOCUMENT_BASE_URL: str | None = None
    INGESTION_RUN_ONCE: bool = True
    CHUNK_SIZE_TOKENS: int = 700
    CHUNK_OVERLAP_TOKENS: int = 100
    EMBEDDING_BATCH_SIZE: int = 16
    INGESTION_CONCURRENCY: int = 4
    # 5-field cron expression (minute hour day month weekday), evaluated in UTC —
    # `app/jobs/ingestion_scheduler.py` runs the startup ingestion job at each
    # occurrence; it is never run automatically at app/container startup itself
    # (user-directed deviation from `10-deployment.md` §4's original "run once at
    # deploy time" model — see `IMPLEMENTATION_PLAN.md` Phase 6's dated correction note).
    INGESTION_CRON_SCHEDULE: str = "0 2 * * *"

    # --- Retrieval / cost control ---
    SIMILARITY_SCORE_THRESHOLD: float = 0.75
    CONTEXT_CONDENSATION_MAX_TURNS: int = 10
    RETRIEVAL_TOP_K: int = 5
    SUMMARY_TOP_K: int = 15

    # --- Uploads ---
    MAX_IMAGE_UPLOAD_MB: int = 5
    MAX_FILE_UPLOAD_MB: int = 25

    # --- Redis / rate limiting ---
    REDIS_URL: str | None = None
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 30
    RATE_LIMIT_BURST: int = 10

    # --- pgvector tuning ---
    PGVECTOR_HNSW_EF_SEARCH: int = 40

    # --- Streaming (SSE) ---
    SSE_KEEPALIVE_INTERVAL_SECONDS: int = 15

    # --- Retention ---
    MESSAGE_RETENTION_DAYS: int = 90
    USAGE_METRICS_RETENTION_DAYS: int = 180
    # 5-field cron expression (minute hour day month weekday), evaluated in UTC —
    # `app/jobs/retention_scheduler.py` runs `services/retention_service.py` at each
    # occurrence, mirroring `INGESTION_CRON_SCHEDULE`'s pattern (see that field's note
    # and `IMPLEMENTATION_PLAN.md` Phase 13's dated note). Default `0 3 * * *` (daily
    # 03:00 UTC, after the 02:00 UTC ingestion run so the two never overlap).
    RETENTION_CRON_SCHEDULE: str = "0 3 * * *"

    # --- Cost management ---
    DAILY_COST_BUDGET_USD: float | None = None
    # 5-field cron expression (minute hour day month weekday), evaluated in UTC —
    # `app/jobs/cost_budget_scheduler.py` runs `services/cost_budget_service.py` at each
    # occurrence, mirroring `INGESTION_CRON_SCHEDULE`/`RETENTION_CRON_SCHEDULE`'s pattern.
    # Default hourly (not once daily like retention) — docs/19-cost-management.md §4's
    # check sums the *current* UTC calendar day so far, so it needs to run intra-day to
    # catch a budget breach as it happens rather than only after the day has ended.
    COST_BUDGET_CRON_SCHEDULE: str = "0 * * * *"

    # --- CORS ---
    CORS_ALLOWED_ORIGINS: str = ""

    @field_validator("DAILY_COST_BUDGET_USD", mode="before")
    @classmethod
    def _blank_budget_is_unset(cls, value: object) -> object:
        """An empty `.env` value means "no budget alert" (docs/10-deployment.md §3),
        not an invalid number — treat blank/whitespace-only as unset."""
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        """Startup-validation checklist — docs/23-configuration.md §4.

        Raises `ValueError` (wrapped by pydantic into `ValidationError`) on
        any violation, so an invalid configuration fails fast at process
        startup rather than surfacing a confusing error later.
        """
        errors: list[str] = []

        if self.CHUNK_OVERLAP_TOKENS >= self.CHUNK_SIZE_TOKENS:
            errors.append(
                "CHUNK_OVERLAP_TOKENS must be strictly less than CHUNK_SIZE_TOKENS "
                f"(got CHUNK_OVERLAP_TOKENS={self.CHUNK_OVERLAP_TOKENS}, "
                f"CHUNK_SIZE_TOKENS={self.CHUNK_SIZE_TOKENS})."
            )

        if not (0 < self.SIMILARITY_SCORE_THRESHOLD <= 1):
            errors.append(
                "SIMILARITY_SCORE_THRESHOLD must be in the range (0, 1], got "
                f"{self.SIMILARITY_SCORE_THRESHOLD}."
            )

        if not (0 <= self.BEDROCK_TEMPERATURE <= 1):
            errors.append(
                "BEDROCK_TEMPERATURE must be in the range [0, 1], got "
                f"{self.BEDROCK_TEMPERATURE}."
            )

        if self.BEDROCK_MAX_RETRIES < 0:
            errors.append(f"BEDROCK_MAX_RETRIES must be >= 0, got {self.BEDROCK_MAX_RETRIES}.")

        try:
            CronTrigger.from_crontab(self.INGESTION_CRON_SCHEDULE, timezone="UTC")
        except ValueError as exc:
            errors.append(
                "INGESTION_CRON_SCHEDULE must be a valid 5-field cron expression "
                f"(minute hour day month weekday), got {self.INGESTION_CRON_SCHEDULE!r}: {exc}"
            )

        try:
            CronTrigger.from_crontab(self.RETENTION_CRON_SCHEDULE, timezone="UTC")
        except ValueError as exc:
            errors.append(
                "RETENTION_CRON_SCHEDULE must be a valid 5-field cron expression "
                f"(minute hour day month weekday), got {self.RETENTION_CRON_SCHEDULE!r}: {exc}"
            )

        try:
            CronTrigger.from_crontab(self.COST_BUDGET_CRON_SCHEDULE, timezone="UTC")
        except ValueError as exc:
            errors.append(
                "COST_BUDGET_CRON_SCHEDULE must be a valid 5-field cron expression "
                f"(minute hour day month weekday), got {self.COST_BUDGET_CRON_SCHEDULE!r}: {exc}"
            )

        if self.BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD < 1:
            errors.append(
                "BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD must be >= 1, got "
                f"{self.BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD}."
            )

        if bool(self.AWS_ACCESS_KEY_ID) != bool(self.AWS_SECRET_ACCESS_KEY):
            errors.append(
                "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must both be set or both "
                "left empty — empty means the AWS default credential chain / IAM role "
                "is used (docs/14-bedrock-integration.md §3); one set without the "
                "other is an inconsistent configuration."
            )

        if self.APP_ENV == "production":
            missing_secrets = [
                name
                for name, value in (
                    ("DATABASE_URL", self.DATABASE_URL),
                    ("REDIS_URL", self.REDIS_URL),
                )
                if not value
            ]
            if missing_secrets:
                errors.append(
                    "The following secret(s) must be set when APP_ENV=production: "
                    f"{', '.join(missing_secrets)}."
                )

            if not self.CORS_ALLOWED_ORIGINS:
                logger.warning(
                    "CORS_ALLOWED_ORIGINS is empty while APP_ENV=production — no "
                    "browser origins will be allowed until this is set "
                    "(docs/08-security.md §6a)."
                )

        if self.APP_ENV in ("staging", "production") and "*" in (
            origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",")
        ):
            errors.append(
                "CORS_ALLOWED_ORIGINS must not include a wildcard ('*') when "
                f"APP_ENV={self.APP_ENV!r} (docs/08-security.md §6a)."
            )

        if errors:
            raise ValueError("Invalid configuration:\n- " + "\n- ".join(errors))

        return self


settings = Settings()
