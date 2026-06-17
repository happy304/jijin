"""Application configuration.

All runtime configuration is loaded from environment variables (and, for
local development, from a `.env` file at the repo root). Values are
validated and coerced through a single `Settings` Pydantic model so the
rest of the codebase can rely on typed access.

Design notes
------------
* The settings object is **immutable** (`frozen=True`) — once constructed
  it should not be mutated at runtime. Tests that need a different value
  should build their own `Settings(...)` instance or use
  `get_settings.cache_clear()` after monkeypatching `os.environ`.
* `get_settings()` is LRU-cached so importing modules share a single
  instance and FastAPI can use it as a `Depends(get_settings)` provider.
* Values are intentionally lenient: missing optional fields default to
  sensible development defaults so `pytest` works without a `.env`.
  The production compose file supplies every non-default value.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = backend/.. — `.env` lives at the repo root (see
# `.env.example`). Resolve from this file's location so the config works
# regardless of the process's current working directory.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOTENV_PATH = _REPO_ROOT / ".env"


AppEnv = Literal["development", "staging", "production", "test"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
ScheduleMode = Literal["light", "research", "full"]


class Settings(BaseSettings):
    """Typed configuration loaded from the environment.

    See `.env.example` at the repo root for the full list of variables.
    """

    model_config = SettingsConfigDict(
        env_file=str(_DOTENV_PATH) if _DOTENV_PATH.exists() else None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    # ---------------------------------------------------------------
    # Runtime
    # ---------------------------------------------------------------
    app_env: AppEnv = Field(default="development", alias="APP_ENV")
    app_name: str = Field(default="fund-quant-platform", alias="APP_NAME")
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: LogLevel = Field(default="INFO", alias="LOG_LEVEL")
    timezone: str = Field(default="Asia/Shanghai", alias="TIMEZONE")
    secret_key: str = Field(default="change-me-in-dev", alias="SECRET_KEY")

    # ---------------------------------------------------------------
    # API
    # ---------------------------------------------------------------
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_workers: int = Field(default=1, alias="API_WORKERS")
    api_prefix: str = Field(default="/api/v1", alias="API_PREFIX")
    api_cors_origins: str = Field(
        default="http://localhost:5173,http://localhost:3000",
        alias="API_CORS_ORIGINS",
    )
    admin_api_enabled: bool = Field(default=False, alias="ADMIN_API_ENABLED")
    admin_api_token: str = Field(default="", alias="ADMIN_API_TOKEN")

    # ---------------------------------------------------------------
    # Personal-use feature profile
    # ---------------------------------------------------------------
    personal_mode: bool = Field(default=True, alias="PERSONAL_MODE")
    feature_ai: bool = Field(default=False, alias="FEATURE_AI")
    feature_advisor_governance: bool = Field(
        default=False,
        alias="FEATURE_ADVISOR_GOVERNANCE",
    )
    feature_full_monitoring: bool = Field(default=False, alias="FEATURE_FULL_MONITORING")
    schedule_mode: ScheduleMode = Field(default="light", alias="SCHEDULE_MODE")

    # ---------------------------------------------------------------
    # Database
    # ---------------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://fundquant:fundquant@localhost:5432/fundquant",
        alias="DATABASE_URL",
    )
    database_sync_url: str = Field(
        default="postgresql+psycopg://fundquant:fundquant@localhost:5432/fundquant",
        alias="DATABASE_SYNC_URL",
    )
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")
    # Auto-apply Alembic migrations on application startup.
    # Defaults to True for dev/staging/test so a fresh checkout "just
    # works"; production deployments should set this to False and run
    # `alembic upgrade head` from a dedicated migration step in the
    # release pipeline (see design §9.2 / requirement 9.4).
    db_auto_migrate: bool = Field(default=False, alias="DB_AUTO_MIGRATE")

    # ---------------------------------------------------------------
    # Redis / Celery
    # ---------------------------------------------------------------
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    celery_broker_url: str = Field(
        default="redis://localhost:6379/1",
        alias="CELERY_BROKER_URL",
    )
    celery_result_backend: str = Field(
        default="redis://localhost:6379/2",
        alias="CELERY_RESULT_BACKEND",
    )
    celery_timezone: str = Field(default="Asia/Shanghai", alias="CELERY_TIMEZONE")

    # ---------------------------------------------------------------
    # Observability
    # ---------------------------------------------------------------
    prometheus_enabled: bool = Field(default=True, alias="PROMETHEUS_ENABLED")
    prometheus_path: str = Field(default="/metrics", alias="PROMETHEUS_PATH")

    # ---------------------------------------------------------------
    # Storage paths
    # ---------------------------------------------------------------
    snapshot_dir: str = Field(default="./local_data/snapshots", alias="SNAPSHOT_DIR")
    backup_dir: str = Field(default="./local_data/backups", alias="BACKUP_DIR")

    # ---------------------------------------------------------------
    # AI
    # ---------------------------------------------------------------
    ai_enabled: bool = Field(default=False, alias="AI_ENABLED")
    ai_data_masking: bool = Field(default=False, alias="AI_DATA_MASKING")

    # LLM Provider 配置
    ai_default_provider: str = Field(default="openai_compat", alias="AI_DEFAULT_PROVIDER")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-3-5-sonnet-latest", alias="ANTHROPIC_MODEL")

    # ================================================================
    # Validators
    # ================================================================

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def _coerce_cors_origins(cls, value: object) -> str:
        """Accept both CSV strings and lists; normalise to CSV.

        Many deployment pipelines inject list-like values. We keep the
        canonical internal form as a comma-separated string and expose
        a parsed property below.
        """
        if isinstance(value, (list, tuple)):
            return ",".join(str(v).strip() for v in value if str(v).strip())
        if value is None:
            return ""
        return str(value)

    # ================================================================
    # Derived helpers
    # ================================================================

    @property
    def cors_origins(self) -> list[str]:
        """Parsed CORS origins list. Empty CSV → empty list."""
        if not self.api_cors_origins:
            return []
        return [origin.strip() for origin in self.api_cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached `Settings` instance.

    Use `get_settings.cache_clear()` in tests that need to re-read
    environment variables.
    """
    return Settings()
