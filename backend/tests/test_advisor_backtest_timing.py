from __future__ import annotations

from datetime import date, timedelta

from app.services import advisor_backtest as module
from app.services.advisor_backtest import run_advisor_backtest
from app.services.trading_advisor import AdvisorConfig


class DummyAdvice:
    action = "hold"
    composite_score = 0.0
    confidence = 0.5
    suggested_amount = 0.0
    fee_estimate = None


def _nav_records(n: int = 90) -> list[tuple[str, float]]:
    start = date(2024, 1, 1)
    return [((start + timedelta(days=i)).isoformat(), 1.0 + i * 0.01) for i in range(n)]


def test_pre_nav_timing_uses_previous_nav_for_decision_and_current_nav_for_execution(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_generate(**kwargs):
        records = kwargs["nav_records"]
        calls.append(
            {
                "last_visible_date": records[-1][0],
                "as_of_date": kwargs["as_of_date"].isoformat(),
            }
        )
        return DummyAdvice()

    monkeypatch.setattr(module, "_generate_live_advice_for_history", fake_generate)

    result = run_advisor_backtest(
        fund_code="000001",
        nav_records=_nav_records(),
        config=AdvisorConfig(),
        lookback_window=20,
        rebalance_freq=7,
        execution_mode="pre_nav",
    )

    assert calls
    assert calls[0]["last_visible_date"] == "2024-01-20"
    assert calls[0]["as_of_date"] == "2024-01-20"
    assert result.advice_records[0]["date"] == "2024-01-21"
    assert result.config["execution_assumption"] == "pre_nav"


def test_post_nav_timing_executes_next_trading_observation(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_generate(**kwargs):
        records = kwargs["nav_records"]
        calls.append(
            {
                "last_visible_date": records[-1][0],
                "as_of_date": kwargs["as_of_date"].isoformat(),
            }
        )
        return DummyAdvice()

    monkeypatch.setattr(module, "_generate_live_advice_for_history", fake_generate)

    result = run_advisor_backtest(
        fund_code="000001",
        nav_records=_nav_records(),
        config=AdvisorConfig(),
        lookback_window=20,
        rebalance_freq=7,
        execution_mode="post_nav_next_day",
    )

    assert calls
    assert calls[0]["last_visible_date"] == "2024-01-21"
    assert calls[0]["as_of_date"] == "2024-01-21"
    assert result.advice_records[0]["date"] == "2024-01-22"
    assert result.config["execution_assumption"] == "post_nav_next_day"
