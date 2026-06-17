"""Tests for Combinatorial Purged Cross-Validation (CPCV)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from app.domain.backtest.cpcv import (
    CPCVConfig,
    CPCVResult,
    run_cpcv,
)


def _make_dates(n: int = 500) -> list[date]:
    """Generate n consecutive business dates."""
    start = date(2020, 1, 2)
    return [start + timedelta(days=i) for i in range(n)]


def _dummy_backtest(
    train_ranges: list[tuple[date, date]],
    test_ranges: list[tuple[date, date]],
) -> tuple[float, float, float, float]:
    """Dummy backtest: IS Sharpe = 1.5, OOS Sharpe = random around 0.5."""
    rng = np.random.default_rng(hash(str(test_ranges)) % 2**31)
    is_sharpe = 1.5 + rng.normal(0, 0.1)
    oos_sharpe = 0.5 + rng.normal(0, 0.3)
    return is_sharpe, oos_sharpe, 0.15, 0.05


def _overfit_backtest(
    train_ranges: list[tuple[date, date]],
    test_ranges: list[tuple[date, date]],
) -> tuple[float, float, float, float]:
    """Simulates an overfit strategy: great IS, terrible OOS."""
    rng = np.random.default_rng(hash(str(test_ranges)) % 2**31)
    is_sharpe = 3.0 + rng.normal(0, 0.1)
    oos_sharpe = -0.5 + rng.normal(0, 0.2)
    return is_sharpe, oos_sharpe, 0.30, -0.05


class TestCPCVConfig:
    def test_default_config(self):
        config = CPCVConfig()
        assert config.n_splits == 6
        assert config.n_test_splits == 2
        assert config.n_combinations == 15  # C(6,2)

    def test_invalid_splits_raises(self):
        with pytest.raises(ValueError):
            CPCVConfig(n_splits=2)

    def test_invalid_test_splits_raises(self):
        with pytest.raises(ValueError):
            CPCVConfig(n_splits=6, n_test_splits=6)

    def test_combinations_count(self):
        config = CPCVConfig(n_splits=8, n_test_splits=3)
        assert config.n_combinations == 56  # C(8,3)


class TestRunCPCV:
    def test_basic_run_produces_paths(self):
        dates = _make_dates(600)
        result = run_cpcv(dates, _dummy_backtest, CPCVConfig(n_splits=6, n_test_splits=2))
        assert result.n_paths > 0
        assert result.n_paths <= 15  # C(6,2)
        assert 0 <= result.pbo <= 1

    def test_overfit_strategy_has_high_pbo(self):
        dates = _make_dates(600)
        result = run_cpcv(dates, _overfit_backtest, CPCVConfig(n_splits=6, n_test_splits=2))
        # Overfit strategy: IS great, OOS terrible → PBO should be high
        assert result.pbo > 0.3  # generous threshold for stochastic test
        assert result.is_overfit or result.pbo > 0.3

    def test_healthy_strategy_has_low_pbo(self):
        dates = _make_dates(600)
        result = run_cpcv(dates, _dummy_backtest, CPCVConfig(n_splits=6, n_test_splits=2))
        # Healthy strategy: OOS positive → PBO should be low
        assert result.pbo < 0.8  # generous

    def test_insufficient_dates_raises(self):
        dates = _make_dates(20)  # too few for 6 splits
        with pytest.raises(ValueError, match="Need at least"):
            run_cpcv(dates, _dummy_backtest, CPCVConfig(n_splits=6, n_test_splits=2))

    def test_max_paths_limits_combinations(self):
        dates = _make_dates(600)
        config = CPCVConfig(n_splits=8, n_test_splits=3)  # C(8,3)=56
        result = run_cpcv(dates, _dummy_backtest, config, max_paths=10)
        assert result.n_paths <= 10

    def test_to_dict(self):
        dates = _make_dates(600)
        result = run_cpcv(dates, _dummy_backtest, CPCVConfig(n_splits=6, n_test_splits=2))
        d = result.to_dict()
        assert "pbo" in d
        assert "avg_oos_sharpe" in d
        assert "is_overfit" in d
        assert "n_paths" in d

    def test_purge_and_embargo_applied(self):
        """With purge/embargo, paths should still be generated (fewer obs per group)."""
        dates = _make_dates(600)
        config = CPCVConfig(n_splits=6, n_test_splits=2, purge_days=5, embargo_days=5)
        result = run_cpcv(dates, _dummy_backtest, config)
        assert result.n_paths > 0
