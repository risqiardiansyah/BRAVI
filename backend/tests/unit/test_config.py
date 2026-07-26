"""Unit tests for app.config.Settings — startup validation, docs/23-configuration.md §4."""

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def _settings(**overrides: object) -> Settings:
    """Build a Settings instance from explicit values only (no dotenv/env-file)."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


class TestValidEnv:
    def test_defaults_load_successfully(self) -> None:
        settings = _settings()
        assert settings.APP_ENV == "development"
        assert settings.CHUNK_SIZE_TOKENS == 700

    def test_loads_from_real_env_file(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text(
            "APP_ENV=production\n"
            "DATABASE_URL=postgresql://user:pass@host:5432/db\n"
            "REDIS_URL=redis://localhost:6379/0\n"
            "CORS_ALLOWED_ORIGINS=https://example.com\n",
            encoding="utf-8",
        )
        settings = Settings(_env_file=str(env_path))  # type: ignore[call-arg]
        assert settings.APP_ENV == "production"
        assert settings.DATABASE_URL == "postgresql://user:pass@host:5432/db"

    def test_loads_documented_env_example(self) -> None:
        """The literal `.env.example` at the repo root must parse cleanly —
        it is what docs/10-deployment.md §3 documents as the default block."""
        env_example = Path(__file__).resolve().parents[3] / ".env.example"
        settings = Settings(_env_file=str(env_example))  # type: ignore[call-arg]
        assert settings.APP_ENV == "development"
        assert settings.DAILY_COST_BUDGET_USD is None
        assert settings.AWS_ACCESS_KEY_ID is None or settings.AWS_ACCESS_KEY_ID == ""


class TestBlankDailyCostBudget:
    def test_blank_string_becomes_none(self) -> None:
        settings = _settings(DAILY_COST_BUDGET_USD="")
        assert settings.DAILY_COST_BUDGET_USD is None

    def test_numeric_value_passes_through(self) -> None:
        settings = _settings(DAILY_COST_BUDGET_USD=50.0)
        assert settings.DAILY_COST_BUDGET_USD == 50.0


class TestChunkOverlapLessThanSize:
    def test_overlap_less_than_size_passes(self) -> None:
        _settings(CHUNK_SIZE_TOKENS=700, CHUNK_OVERLAP_TOKENS=100)

    def test_overlap_equal_to_size_fails(self) -> None:
        with pytest.raises(ValidationError, match="CHUNK_OVERLAP_TOKENS"):
            _settings(CHUNK_SIZE_TOKENS=700, CHUNK_OVERLAP_TOKENS=700)

    def test_overlap_greater_than_size_fails(self) -> None:
        with pytest.raises(ValidationError, match="CHUNK_OVERLAP_TOKENS"):
            _settings(CHUNK_SIZE_TOKENS=700, CHUNK_OVERLAP_TOKENS=800)


class TestSimilarityScoreThreshold:
    @pytest.mark.parametrize("value", [0.01, 0.75, 1.0])
    def test_in_range_passes(self, value: float) -> None:
        _settings(SIMILARITY_SCORE_THRESHOLD=value)

    @pytest.mark.parametrize("value", [0.0, -0.1, 1.1])
    def test_out_of_range_fails(self, value: float) -> None:
        with pytest.raises(ValidationError, match="SIMILARITY_SCORE_THRESHOLD"):
            _settings(SIMILARITY_SCORE_THRESHOLD=value)


class TestBedrockTemperature:
    @pytest.mark.parametrize("value", [0.0, 0.2, 1.0])
    def test_in_range_passes(self, value: float) -> None:
        _settings(BEDROCK_TEMPERATURE=value)

    @pytest.mark.parametrize("value", [-0.1, 1.1])
    def test_out_of_range_fails(self, value: float) -> None:
        with pytest.raises(ValidationError, match="BEDROCK_TEMPERATURE"):
            _settings(BEDROCK_TEMPERATURE=value)


class TestBedrockMaxRetries:
    @pytest.mark.parametrize("value", [0, 3])
    def test_non_negative_passes(self, value: int) -> None:
        _settings(BEDROCK_MAX_RETRIES=value)

    def test_negative_fails(self) -> None:
        with pytest.raises(ValidationError, match="BEDROCK_MAX_RETRIES"):
            _settings(BEDROCK_MAX_RETRIES=-1)


class TestBedrockCircuitBreakerFailureThreshold:
    @pytest.mark.parametrize("value", [1, 5])
    def test_at_least_one_passes(self, value: int) -> None:
        _settings(BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD=value)

    def test_zero_fails(self) -> None:
        with pytest.raises(ValidationError, match="BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD"):
            _settings(BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD=0)


class TestIngestionCronSchedule:
    @pytest.mark.parametrize("value", ["0 2 * * *", "*/15 * * * *", "0 0 1 1 *", "30 6 * * 1-5"])
    def test_valid_cron_expression_passes(self, value: str) -> None:
        settings = _settings(INGESTION_CRON_SCHEDULE=value)
        assert settings.INGESTION_CRON_SCHEDULE == value

    @pytest.mark.parametrize(
        "value",
        [
            "not a cron",  # wrong field count
            "99 * * * *",  # minute out of range
            "* 99 * * *",  # hour out of range
        ],
    )
    def test_invalid_cron_expression_fails(self, value: str) -> None:
        with pytest.raises(ValidationError, match="INGESTION_CRON_SCHEDULE"):
            _settings(INGESTION_CRON_SCHEDULE=value)


class TestAwsCredentialPairing:
    def test_both_empty_passes(self) -> None:
        _settings(AWS_ACCESS_KEY_ID=None, AWS_SECRET_ACCESS_KEY=None)

    def test_both_set_passes(self) -> None:
        _settings(AWS_ACCESS_KEY_ID="AKIAEXAMPLE", AWS_SECRET_ACCESS_KEY="secret")

    def test_only_access_key_set_fails(self) -> None:
        with pytest.raises(ValidationError, match="AWS_ACCESS_KEY_ID"):
            _settings(AWS_ACCESS_KEY_ID="AKIAEXAMPLE", AWS_SECRET_ACCESS_KEY=None)

    def test_only_secret_key_set_fails(self) -> None:
        with pytest.raises(ValidationError, match="AWS_ACCESS_KEY_ID"):
            _settings(AWS_ACCESS_KEY_ID=None, AWS_SECRET_ACCESS_KEY="secret")


class TestProductionRequiredSecrets:
    def test_missing_database_url_in_production_fails(self) -> None:
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            _settings(
                APP_ENV="production",
                DATABASE_URL=None,
                REDIS_URL="redis://localhost:6379/0",
            )

    def test_missing_redis_url_in_production_fails(self) -> None:
        with pytest.raises(ValidationError, match="REDIS_URL"):
            _settings(
                APP_ENV="production",
                DATABASE_URL="postgresql://user:pass@host:5432/db",
                REDIS_URL=None,
            )

    def test_missing_both_in_production_fails(self) -> None:
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            _settings(APP_ENV="production", DATABASE_URL=None, REDIS_URL=None)

    def test_present_secrets_in_production_passes(self) -> None:
        _settings(
            APP_ENV="production",
            DATABASE_URL="postgresql://user:pass@host:5432/db",
            REDIS_URL="redis://localhost:6379/0",
        )

    @pytest.mark.parametrize("app_env", ["local", "development", "staging"])
    def test_missing_secrets_outside_production_passes(self, app_env: str) -> None:
        _settings(APP_ENV=app_env, DATABASE_URL=None, REDIS_URL=None)


class TestCorsProductionWarning:
    def test_empty_cors_in_production_warns_but_passes(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            settings = _settings(
                APP_ENV="production",
                DATABASE_URL="postgresql://user:pass@host:5432/db",
                REDIS_URL="redis://localhost:6379/0",
                CORS_ALLOWED_ORIGINS="",
            )
        assert settings.CORS_ALLOWED_ORIGINS == ""
        assert any("CORS_ALLOWED_ORIGINS" in record.message for record in caplog.records)

    def test_nonempty_cors_in_production_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            _settings(
                APP_ENV="production",
                DATABASE_URL="postgresql://user:pass@host:5432/db",
                REDIS_URL="redis://localhost:6379/0",
                CORS_ALLOWED_ORIGINS="https://example.com",
            )
        assert not any("CORS_ALLOWED_ORIGINS" in record.message for record in caplog.records)

    def test_empty_cors_outside_production_no_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            _settings(APP_ENV="development", CORS_ALLOWED_ORIGINS="")
        assert not any("CORS_ALLOWED_ORIGINS" in record.message for record in caplog.records)
