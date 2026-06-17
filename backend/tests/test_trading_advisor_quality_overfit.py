from __future__ import annotations

from datetime import date, timedelta

from app.services.trading_advisor import (
    AdvisorConfig,
    ReliabilityAdjustment,
    TradingAdvice,
    TradingAdvisor,
    build_advisor_data_quality_report,
    build_advisor_overfit_risk,
)


def test_trading_advice_to_dict_includes_quality_overfit_and_contributions() -> None:
    advice = TradingAdvice(fund_code="000001", advice_date="2026-05-27")
    advice.technical_score = 0.2
    advice.momentum_score = 0.4
    advice.strategy_score = 0.0
    advice.prediction_score = 0.1
    advice.cross_sectional_score = 0.3
    advice.composite_score = 0.25
    advice._signal_weights = {
        "technical": 0.1,
        "momentum": 0.3,
        "strategy": 0.0,
        "prediction": 0.2,
        "cross_sectional": 0.4,
    }
    advice._signal_availability = {
        "technical": True,
        "momentum": True,
        "strategy": False,
        "prediction": True,
        "cross_sectional": True,
    }
    advisor = TradingAdvisor(
        engine_health={"status": "healthy", "rolling_ic_samples": 60},
        oos_snapshots={},
        as_of_date=date(2026, 5, 27),
    )
    nav_records = [
        ((date(2026, 1, 1) + timedelta(days=i)).isoformat(), 1 + i * 0.001)
        for i in range(140)
    ]
    advice.data_quality = build_advisor_data_quality_report(
        advice.fund_code,
        nav_records,
        as_of_date=date(2026, 5, 27),
        lookback_days=200,
        prediction_sample_size=140,
    )
    advice.reliability_adjustment = ReliabilityAdjustment(
        status="healthy",
        metrics={"oos_avg_ic": 0.06, "oos_ic_degradation": 0.8, "oos_total_signals": 40},
    )
    advice.overfit_risk = build_advisor_overfit_risk(
        advice,
        engine_health_status="healthy",
        rolling_ic_samples=60,
    )
    advice.decision_audit = advisor._build_decision_audit(advice, nav_records)

    payload = advice.to_dict()
    assert payload["data_quality"]["status"] in {"good", "warning"}
    assert "source_consistency" in payload["data_quality"]
    assert "adjustment_consistency" in payload["data_quality"]
    assert "cross_source_consistency" in payload["data_quality"]
    assert payload["overfit_risk"]["level"] == "low"
    assert payload["decision_audit"]["signal_contributions"]
    assert payload["decision_audit"]["dominant_signal"] is not None
    assert payload["decision_support_only"] is True
    assert payload["support_action"] in {"consider_increase", "consider_reduce", "observe", "review_required"}
    assert "不构成投资建议" in payload["not_investment_advice_disclaimer"]


def test_decision_support_wording_is_softened_for_buy_and_sell() -> None:
    advisor = TradingAdvisor(config=AdvisorConfig(), total_capital=100000)
    banned_terms = ["建议买入", "建议卖出", "应买入", "应卖出", "强烈", "保证", "必然"]

    for action in ["buy", "sell"]:
        advice = TradingAdvice(fund_code="000001", advice_date="2026-06-20")
        advice.action = action
        advice.confidence = 0.72
        advice.suggested_amount = 8000
        advice.composite_score = 0.4 if action == "buy" else -0.4
        advice.reasoning = advisor._build_reasoning(advice)
        payload = advice.to_dict()
        text = " ".join(
            [
                payload["support_label"],
                payload["not_investment_advice_disclaimer"],
                payload["reasoning"]["summary"],
                " ".join(payload["reasons"]),
                " ".join(payload["risk_warnings"]),
                " ".join(payload["limitations"]),
            ]
        )
        assert payload["decision_support_only"] is True
        assert payload["support_action"] in {"consider_increase", "consider_reduce"}
        assert not any(term in text for term in banned_terms)


def test_source_and_adjustment_diagnostics_lower_data_quality() -> None:
    nav_records = [
        ((date(2026, 1, 1) + timedelta(days=i)).isoformat(), 1 + i * 0.001)
        for i in range(160)
    ]
    report = build_advisor_data_quality_report(
        "000001",
        nav_records,
        as_of_date=date(2026, 6, 10),
        lookback_days=220,
        nav_diagnostics={
            "source_consistency": {
                "point_count": 160,
                "source_count": 2,
                "primary_source": "eastmoney",
                "source_switch_count": 4,
                "source_switch_ratio": 0.025,
                "missing_source_count": 1,
                "sources": {"eastmoney": 120, "akshare": 39},
            },
            "adjustment_consistency": {
                "point_count": 160,
                "adjusted_count": 145,
                "unit_nav_count": 160,
                "fallback_to_unit_count": 15,
                "adjusted_coverage_ratio": 0.9063,
                "factor_jump_count": 1,
                "factor_jump_dates": ["2026-03-01"],
                "missing_unit_count": 0,
                "missing_adj_count": 15,
            },
        },
    )

    assert report.status == "warning"
    assert report.source_consistency["source_switch_count"] == 4
    assert report.adjustment_consistency["fallback_to_unit_count"] == 15
    assert any("数据源" in warning for warning in report.warnings)
    assert any("复权" in warning for warning in report.warnings)


def test_cross_source_hard_gate_forces_poor_quality_and_hold() -> None:
    advisor = TradingAdvisor(
        config=AdvisorConfig(),
        total_capital=100000,
        as_of_date=date(2026, 6, 20),
    )
    advice = TradingAdvice(fund_code="000001", advice_date="2026-06-20")
    advice.action = "buy"
    advice.confidence = 0.8
    advice.suggested_amount = 10000
    advice.suggested_pct = 0.1
    advice.position_after = 0.1
    nav_records = [
        ((date(2026, 1, 1) + timedelta(days=i)).isoformat(), 1 + i * 0.001)
        for i in range(160)
    ]
    advice.data_quality = build_advisor_data_quality_report(
        advice.fund_code,
        nav_records,
        as_of_date=date(2026, 6, 20),
        lookback_days=220,
        nav_diagnostics={
            "cross_source_consistency": {
                "status": "fail",
                "hard_gate": True,
                "provider_count": 2,
                "providers": ["eastmoney", "akshare"],
                "alert_count": 6,
                "alert_ratio": 0.08,
                "reason": "跨源 NAV 冲突达到硬门禁",
            },
        },
    )

    advisor._apply_quality_and_overfit_gates(advice)

    assert advice.data_quality.status == "poor"
    assert advice.action == "hold"
    assert advice.suggested_amount == 0
    assert any("数据质量" in item for item in advice.risk_warnings)
    payload = advice.to_dict()
    assert payload["support_action"] == "review_required"
    assert "复核" in payload["support_label"]

