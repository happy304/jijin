"""Tests for PSR / DSR / Sharpe statistical inference."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.domain.performance.sharpe_inference import (
    _compute_sharpe_std_error,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
    sharpe_inference,
)


@pytest.fixture
def positive_returns() -> np.ndarray:
    """Returns with strongly positive mean (annualized Sharpe ≈ 2.5).

    Mean=0.002, std=0.012, n=2000 → daily Sharpe ≈ 0.167, T-1 ≈ 1999
    z ≈ 7.5 → PSR ≈ 1.0 robustly under sampling noise.
    """
    rng = np.random.default_rng(42)
    return rng.normal(0.002, 0.012, size=2000)


@pytest.fixture
def zero_mean_returns() -> np.ndarray:
    """Zero-mean returns (true Sharpe = 0)."""
    rng = np.random.default_rng(42)
    return rng.normal(0.0, 0.01, size=1000)


@pytest.fixture
def short_returns() -> np.ndarray:
    """Tiny series — should be flagged as insufficient."""
    return np.array([0.001, -0.002, 0.003])


class TestSharpeStandardError:
    """Sharpe 标准误公式测试。"""

    def test_uses_regular_kurtosis_term_from_excess_kurtosis(self):
        """PSR 分母应使用 ((regular_kurtosis - 1) / 4)。

        scipy 返回的是超额峰度 excess_kurtosis = regular_kurtosis - 3，
        因此实现中应转换为 ((excess_kurtosis + 2) / 4)。
        对正态收益（超额峰度约 0），SR=1 时方差项应为 1.5，而不是 1.0。
        """
        se = _compute_sharpe_std_error(
            sharpe_obs=1.0,
            n=101,
            skew=0.0,
            excess_kurt=0.0,
        )
        expected = math.sqrt(1.5 / 100)
        assert se == pytest.approx(expected, rel=1e-12)

    def test_standard_error_increases_with_positive_excess_kurtosis(self):
        """肥尾收益应提高 Sharpe 估计不确定性。"""
        normal_like = _compute_sharpe_std_error(1.0, 101, 0.0, 0.0)
        fat_tail = _compute_sharpe_std_error(1.0, 101, 0.0, 4.0)
        assert fat_tail > normal_like


class TestProbabilisticSharpeRatio:
    """PSR tests."""

    def test_psr_high_when_strong_positive_sharpe(self, positive_returns):
        """A return series with clearly positive Sharpe should give high PSR."""
        psr = probabilistic_sharpe_ratio(positive_returns, sharpe_threshold=0.0)
        assert psr > 0.95, f"Expected PSR > 0.95, got {psr}"
        assert psr <= 1.0

    def test_psr_around_half_for_zero_mean(self, zero_mean_returns):
        """Zero-mean returns should give PSR roughly in (0.05, 0.95) range.

        Wide tolerance because under H0: SR=0, PSR is uniform on [0,1] →
        any single sample can land far from 0.5. We just check it's not
        wildly biased toward 1.
        """
        psr = probabilistic_sharpe_ratio(zero_mean_returns, sharpe_threshold=0.0)
        # Should not be strongly significant in either direction
        assert 0.05 < psr < 0.95, f"PSR for zero-mean returns: {psr}"

    def test_psr_returns_nan_for_insufficient_data(self):
        """PSR returns NaN for empty input."""
        result = probabilistic_sharpe_ratio([])
        assert math.isnan(result)

    def test_psr_in_unit_interval(self, positive_returns):
        """PSR is always in [0, 1] when defined."""
        for threshold in [-0.5, 0.0, 0.1, 0.5, 2.0]:
            psr = probabilistic_sharpe_ratio(positive_returns, sharpe_threshold=threshold)
            if not math.isnan(psr):
                assert 0.0 <= psr <= 1.0

    def test_psr_decreases_with_higher_threshold(self, positive_returns):
        """Higher Sharpe threshold → lower PSR."""
        psr_low = probabilistic_sharpe_ratio(positive_returns, sharpe_threshold=0.0)
        psr_high = probabilistic_sharpe_ratio(positive_returns, sharpe_threshold=0.5)
        assert psr_low > psr_high


class TestExpectedMaxSharpe:
    """E[max SR | N trials] tests."""

    def test_zero_for_single_trial(self):
        """N=1 means no selection, expected max = 0."""
        assert expected_max_sharpe(1) == 0.0

    def test_grows_with_trials(self):
        """More trials → higher expected max under zero-true-Sharpe null."""
        e10 = expected_max_sharpe(10)
        e100 = expected_max_sharpe(100)
        e1000 = expected_max_sharpe(1000)
        assert e10 < e100 < e1000

    def test_scales_with_variance(self):
        """E[max] scales with √V."""
        e1 = expected_max_sharpe(100, variance_of_trials=1.0)
        e4 = expected_max_sharpe(100, variance_of_trials=4.0)
        # √4 / √1 = 2
        assert e4 == pytest.approx(2.0 * e1, rel=0.01)

    def test_handles_zero_trials_gracefully(self):
        """N=0 → 0."""
        assert expected_max_sharpe(0) == 0.0


class TestDeflatedSharpeRatio:
    """DSR tests."""

    def test_dsr_lower_than_psr_when_n_trials_large(self, positive_returns):
        """DSR should be ≤ PSR (DSR threshold is higher)."""
        psr = probabilistic_sharpe_ratio(positive_returns, sharpe_threshold=0.0)
        dsr = deflated_sharpe_ratio(positive_returns, n_trials=100)
        assert dsr <= psr + 1e-9

    def test_dsr_equals_psr_when_n_trials_one(self, positive_returns):
        """When n_trials=1, DSR threshold = 0 → DSR = PSR(0)."""
        psr = probabilistic_sharpe_ratio(positive_returns, sharpe_threshold=0.0)
        dsr = deflated_sharpe_ratio(positive_returns, n_trials=1)
        assert dsr == pytest.approx(psr, abs=1e-6)

    def test_dsr_decreases_with_n_trials(self, positive_returns):
        """More trials → harder to achieve significance."""
        dsr_10 = deflated_sharpe_ratio(positive_returns, n_trials=10)
        dsr_1000 = deflated_sharpe_ratio(positive_returns, n_trials=1000)
        assert dsr_1000 <= dsr_10

    def test_dsr_clamps_n_trials_below_one(self, positive_returns):
        """n_trials < 1 is treated as 1."""
        dsr0 = deflated_sharpe_ratio(positive_returns, n_trials=0)
        dsr1 = deflated_sharpe_ratio(positive_returns, n_trials=1)
        assert dsr0 == pytest.approx(dsr1, abs=1e-6)


class TestSharpeInference:
    """Full sharpe_inference one-shot wrapper."""

    def test_returns_none_for_insufficient_data(self, short_returns):
        """Insufficient data → None."""
        # 3 points is insufficient for stable kurtosis but inference should
        # still degrade gracefully
        result = sharpe_inference(np.array([0.0]))
        assert result is None

    def test_full_result_fields(self, positive_returns):
        """Full result has all expected fields."""
        result = sharpe_inference(positive_returns, n_trials=50, freq=252)
        assert result is not None
        assert result.n_observations == 2000
        assert result.sharpe_observed != 0
        assert result.sharpe_annualized == pytest.approx(
            result.sharpe_observed * math.sqrt(252), rel=1e-6
        )
        assert 0 <= result.psr <= 1
        assert 0 <= result.dsr <= 1
        # CI should bracket the observed annualized Sharpe
        if not math.isnan(result.ci_lower) and not math.isnan(result.ci_upper):
            assert result.ci_lower <= result.sharpe_annualized <= result.ci_upper

    def test_significance_flags(self, positive_returns):
        """Boolean significance flags reflect 0.95 cutoff."""
        result = sharpe_inference(positive_returns, n_trials=1)
        if result.psr > 0.95:
            assert result.psr_significant is True
        else:
            assert result.psr_significant is False

    def test_to_dict_serializable(self, positive_returns):
        """to_dict produces JSON-friendly output."""
        result = sharpe_inference(positive_returns, n_trials=50)
        d = result.to_dict()
        assert "psr" in d
        assert "dsr" in d
        assert "ci_lower" in d
        assert "ci_upper" in d
        assert "n_trials" in d
        # No NaN/Inf in serialized dict
        for k, v in d.items():
            if isinstance(v, float):
                assert not math.isnan(v)
                assert not math.isinf(v)
