"""Unit tests for `app.core.config.Settings`."""

from __future__ import annotations

from app.core.config import Settings


def test_defaults_are_applied_when_env_is_clean() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.app_env == "development"
    assert s.api_prefix == "/api/v1"
    assert s.api_port == 8000
    assert s.is_development is True
    assert s.is_production is False


def test_personal_mode_defaults_are_lightweight() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.personal_mode is True
    assert s.feature_ai is False
    assert s.feature_advisor_governance is False
    assert s.feature_full_monitoring is False
    assert s.schedule_mode == "light"


def test_schedule_mode_accepts_research_and_full() -> None:
    assert Settings(_env_file=None, SCHEDULE_MODE="research").schedule_mode == "research"  # type: ignore[call-arg]
    assert Settings(_env_file=None, SCHEDULE_MODE="full").schedule_mode == "full"  # type: ignore[call-arg]


def test_cors_origins_csv_is_parsed_into_list() -> None:
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        API_CORS_ORIGINS="http://a.example, http://b.example ,,  ",
    )
    assert s.cors_origins == ["http://a.example", "http://b.example"]


def test_cors_origins_accept_list_input() -> None:
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        API_CORS_ORIGINS=["http://a.example", "http://b.example"],
    )
    assert s.cors_origins == ["http://a.example", "http://b.example"]


def test_cors_origins_empty_string_gives_empty_list() -> None:
    s = Settings(_env_file=None, API_CORS_ORIGINS="")  # type: ignore[call-arg]
    assert s.cors_origins == []


def test_is_production_and_test_flags() -> None:
    assert Settings(_env_file=None, APP_ENV="production").is_production is True  # type: ignore[call-arg]
    assert Settings(_env_file=None, APP_ENV="test").is_test is True  # type: ignore[call-arg]
