"""Tests for unified performance metric helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.performance.metrics import (
    annualized_return_from_nav,
    annualized_volatility_from_returns,
    calmar_ratio_from_nav,
    downside_deviation_from_returns,
    drawdown_details_from_nav,
    historical_cvar,
    historical_var,
    max_drawdown_from_nav,
    returns_from_nav,
    sharpe_ratio_from_returns,
    sortino_ratio_from_returns,
    total_return_from_nav,
)


def test_returns_from_nav_drops_nan_and_non_finite_returns() -> None:
    nav = pd.Series([1.0, np.nan, 1.1, 1.21])
    result = returns_from_nav(nav)

    assert len(result) == 2
    assert result.iloc[0] == pytest.approx(0.10)
    assert result.iloc[1] == pytest.approx(0.10)


def test_total_return_from_nav() -> None:
    nav = pd.Series([1.0, np.nan, 1.1, 1.2])

    assert total_return_from_nav(nav) == pytest.approx(0.20)


def test_annualized_return_uses_return_intervals() -> None:
    total_ret = 0.10
    n_intervals = 252
    daily_ret = (1 + total_ret) ** (1.0 / n_intervals) - 1
    navs = [1.0]
    for _ in range(n_intervals):
        navs.append(navs[-1] * (1 + daily_ret))
    nav = pd.Series(navs)

    assert annualized_return_from_nav(nav, freq=252) == pytest.approx(0.10, abs=1e-6)


def test_annualized_volatility_uses_sample_std() -> None:
    returns = pd.Series([0.01, -0.01, 0.02, -0.02])
    expected = returns.std(ddof=1) * np.sqrt(252)

    assert annualized_volatility_from_returns(returns) == pytest.approx(expected)


def test_sharpe_ratio_from_returns() -> None:
    returns = pd.Series([0.01, -0.005, 0.015, 0.0, 0.02])
    expected = returns.mean() / returns.std(ddof=1) * np.sqrt(252)

    assert sharpe_ratio_from_returns(returns, risk_free_rate=0.0) == pytest.approx(expected)


def test_downside_deviation_uses_full_sample_denominator() -> None:
    returns = pd.Series([0.02, -0.01, 0.03, -0.02])
    expected_daily = np.sqrt((0.0**2 + (-0.01) ** 2 + 0.0**2 + (-0.02) ** 2) / 4)

    assert downside_deviation_from_returns(returns) == pytest.approx(expected_daily * np.sqrt(252))


def test_sortino_ratio_uses_full_sample_downside_deviation() -> None:
    returns = pd.Series([0.02, -0.01, 0.03, -0.02])
    downside_daily = np.sqrt((0.0**2 + (-0.01) ** 2 + 0.0**2 + (-0.02) ** 2) / 4)
    expected = returns.mean() / downside_daily * np.sqrt(252)

    assert sortino_ratio_from_returns(returns, risk_free_rate=0.0) == pytest.approx(expected)


def test_historical_var_and_cvar_are_positive_losses() -> None:
    returns = pd.Series([-0.05, -0.04, -0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03, 0.04])

    assert historical_var(returns, confidence=0.90) == pytest.approx(0.05)
    assert historical_cvar(returns, confidence=0.90) == pytest.approx(0.05)


def test_drawdown_details_include_recovery_date_and_days() -> None:
    idx = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"])
    nav = pd.Series([1.0, 1.2, 0.9, 1.1, 1.21], index=idx)

    details = drawdown_details_from_nav(nav)

    assert details["max_drawdown"] == pytest.approx(-0.25)
    assert details["peak_date"].isoformat() == "2024-01-03"
    assert details["trough_date"].isoformat() == "2024-01-04"
    assert details["recovery_date"].isoformat() == "2024-01-08"
    assert details["recovery_days"] == 4


def test_insufficient_data_returns_nan() -> None:
    assert np.isnan(annualized_return_from_nav(pd.Series([1.0])))
    assert np.isnan(annualized_volatility_from_returns([0.01]))
    assert np.isnan(sharpe_ratio_from_returns([0.01]))
    assert np.isnan(downside_deviation_from_returns([0.01]))
    assert np.isnan(sortino_ratio_from_returns([0.01]))
    assert np.isnan(historical_var([0.01] * 9))
    assert np.isnan(historical_cvar([0.01] * 9))
