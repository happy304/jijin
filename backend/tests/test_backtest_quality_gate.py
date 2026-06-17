from __future__ import annotations

from app.api.v1.backtests import _quality_from_metrics
from app.domain.backtest.result import BacktestQuality


def test_quality_from_metrics_prefers_persisted_quality_payload() -> None:
    quality = BacktestQuality(
        pit_data_quality="strict",
        survivorship_bias_control="full",
        warnings=["audit-ready"],
    ).to_dict()

    payload = _quality_from_metrics({"quality": quality, "pit_data_quality": "missing"})

    assert payload["pit_data_quality"] == "strict"
    assert payload["decision_grade"] == "decision_support"
    assert "audit-ready" in payload["warnings"]


def test_quality_from_legacy_metrics_downgrades_missing_pit() -> None:
    payload = _quality_from_metrics({"pit_data_quality": "missing"})

    assert payload["pit_data_quality"] == "missing"
    assert payload["decision_grade"] == "research_approximation"
    assert any("PIT" in warning for warning in payload["warnings"])


def test_quality_from_nav_warning_adds_research_warning() -> None:
    payload = _quality_from_metrics({"pit_data_quality": "fallback", "nav_quality_warning": {"message": "mixed"}})

    assert payload["pit_data_quality"] == "fallback"
    assert payload["decision_grade"] == "research_approximation"
    assert any("NAV" in warning for warning in payload["warnings"])
