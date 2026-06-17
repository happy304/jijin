from __future__ import annotations

from datetime import date

import pytest

from app.services.advisor_execution import AdvisorExecutionRequest, _apply_advisor_risk_constraints
from app.services.trading_advisor import (
    TradingAdvice,
    load_nav_data_for_advisor,
    load_nav_quality_diagnostics_for_advisor,
)


class FailingSession:
    def __init__(self) -> None:
        self.rollback_called = False

    async def execute(self, *args, **kwargs):
        raise RuntimeError("simulated aborted transaction")

    async def rollback(self) -> None:
        self.rollback_called = True


@pytest.mark.asyncio
async def test_load_nav_data_rolls_back_and_degrades_on_db_error() -> None:
    session = FailingSession()

    result = await load_nav_data_for_advisor(
        ["006265"],
        session,
        lookback_days=30,
        as_of_date=date(2026, 6, 12),
    )

    assert result == {}
    assert session.rollback_called is True


@pytest.mark.asyncio
async def test_load_nav_quality_rolls_back_and_returns_empty_diagnostics() -> None:
    session = FailingSession()

    result = await load_nav_quality_diagnostics_for_advisor(
        ["006265"],
        session,
        lookback_days=30,
        as_of_date=date(2026, 6, 12),
    )

    assert session.rollback_called is True
    assert result["006265"]["source_consistency"]["point_count"] == 0
    assert result["006265"]["cross_source_consistency"]["status"] == "insufficient_sources"


def test_advisor_risk_constraints_adjust_oversized_buy() -> None:
    request = AdvisorExecutionRequest(
        fund_codes=["006265"],
        total_capital=100000,
        current_positions={"006265": 20000},
        risk_level="moderate",
        user_profile={"risk_level": "moderate", "liquidity_need": "high"},
    )
    advice = TradingAdvice(
        fund_code="006265",
        fund_type="stock",
        action="buy",
        suggested_amount=50000,
        suggested_pct=0.5,
        position_after=0.7,
    )

    _apply_advisor_risk_constraints(request, [advice])

    assert advice.suggested_amount <= 8000
    assert advice.risk_constraints["status"] in {"adjusted", "blocked"}
    assert advice.risk_constraints["violations"]
    assert any(v["code"] == "max_single_trade" for v in advice.risk_constraints["violations"])
