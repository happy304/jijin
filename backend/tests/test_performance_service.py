"""Unit tests for PerformanceService.

Covers:
- Single strategy analysis with all metric categories
- Multi-strategy comparison
- Edge cases: empty NAV, no benchmark, insufficient data
- Attribution (Fama-French and Brinson) integration
- JSON serialization correctness
- _safe_float utility

Satisfies requirements 6.4, 6.5, 6.6.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.performance_service import (
    AttributionResult,
    BenchmarkMetrics,
    ComparisonReport,
    PerformanceReport,
    PerformanceService,
    ReturnMetrics,
    RiskAdjustedMetrics,
    RiskMetrics,
    _safe_float,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def daily_dates() -> pd.DatetimeIndex:
    """Generate 2 years of business day dates."""
    return pd.bdate_range("2020-01-02", periods=504)


@pytest.fixture
def growing_nav(daily_dates: pd.DatetimeIndex) -> pd.Series:
    """A steadily growing NAV series (10% annual return approx)."""
    # Simulate ~10% annual return with some noise
    np.random.seed(42)
    daily_return = 0.10 / 252
    noise = np.random.normal(0, 0.01, len(daily_dates))
    cumulative = np.cumprod(1 + daily_return + noise)
    return pd.Series(cumulative, index=daily_dates, name="nav")


@pytest.fixture
def benchmark_nav(daily_dates: pd.DatetimeIndex) -> pd.Series:
    """A benchmark NAV series (8% annual return approx)."""
    np.random.seed(123)
    daily_return = 0.08 / 252
    noise = np.random.normal(0, 0.008, len(daily_dates))
    cumulative = np.cumprod(1 + daily_return + noise)
    return pd.Series(cumulative, index=daily_dates, name="benchmark")


@pytest.fixture
def factor_returns_df(daily_dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Fama-French factor returns DataFrame."""
    np.random.seed(99)
    n = len(daily_dates)
    return pd.DataFrame(
        {
            "MKT": np.random.normal(0.0003, 0.01, n),
            "SMB": np.random.normal(0.0001, 0.005, n),
            "HML": np.random.normal(0.0001, 0.005, n),
        },
        index=daily_dates,
    )


@pytest.fixture
def service() -> PerformanceService:
    """Default PerformanceService instance."""
    return PerformanceService(risk_free_rate=0.0)


# ---------------------------------------------------------------------------
# Tests: _safe_float utility
# ---------------------------------------------------------------------------


class TestSafeFloat:
    """Tests for the _safe_float utility function."""

    def test_normal_float(self):
        assert _safe_float(1.23456789) == 1.234568

    def test_nan_returns_none(self):
        assert _safe_float(np.nan) is None

    def test_inf_returns_none(self):
        assert _safe_float(np.inf) is None
        assert _safe_float(-np.inf) is None

    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_integer(self):
        assert _safe_float(5) == 5.0

    def test_zero(self):
        assert _safe_float(0.0) == 0.0

    def test_non_numeric_returns_none(self):
        assert _safe_float("not a number") is None


# ---------------------------------------------------------------------------
# Tests: Single strategy analysis
# ---------------------------------------------------------------------------


class TestSingleStrategyAnalysis:
    """Tests for single strategy performance analysis."""

    def test_basic_analysis_returns_all_sections(
        self, service: PerformanceService, growing_nav: pd.Series
    ):
        """Verify that analyze() returns a report with all metric sections."""
        report = service.analyze(nav=growing_nav, strategy_name="test_strategy")

        assert isinstance(report, PerformanceReport)
        assert report.strategy_name == "test_strategy"
        assert isinstance(report.returns, ReturnMetrics)
        assert isinstance(report.risk, RiskMetrics)
        assert isinstance(report.risk_adjusted, RiskAdjustedMetrics)
        assert isinstance(report.benchmark, BenchmarkMetrics)
        assert isinstance(report.attribution, AttributionResult)

    def test_return_metrics_computed(
        self, service: PerformanceService, growing_nav: pd.Series
    ):
        """Return metrics should be non-NaN for valid NAV."""
        report = service.analyze(nav=growing_nav)

        assert report.returns.total_return is not None
        assert not np.isnan(report.returns.total_return)
        assert report.returns.total_return > 0  # Growing NAV

        assert report.returns.annualized_return is not None
        assert not np.isnan(report.returns.annualized_return)
        assert report.returns.annualized_return > 0

    def test_risk_metrics_computed(
        self, service: PerformanceService, growing_nav: pd.Series
    ):
        """Risk metrics should be non-NaN for valid NAV."""
        report = service.analyze(nav=growing_nav)

        assert not np.isnan(report.risk.volatility)
        assert report.risk.volatility > 0

        assert not np.isnan(report.risk.max_drawdown)
        assert report.risk.max_drawdown <= 0  # Drawdown is negative or zero

    def test_risk_adjusted_metrics_computed(
        self, service: PerformanceService, growing_nav: pd.Series
    ):
        """Risk-adjusted metrics should be non-NaN for valid NAV."""
        report = service.analyze(nav=growing_nav)

        assert not np.isnan(report.risk_adjusted.sharpe)
        assert not np.isnan(report.risk_adjusted.sortino)

    def test_benchmark_metrics_with_benchmark(
        self,
        service: PerformanceService,
        growing_nav: pd.Series,
        benchmark_nav: pd.Series,
    ):
        """Benchmark metrics should be computed when benchmark is provided."""
        report = service.analyze(nav=growing_nav, benchmark_nav=benchmark_nav)

        assert not np.isnan(report.benchmark.beta)
        assert not np.isnan(report.benchmark.tracking_error)
        assert not np.isnan(report.benchmark.r_squared)

    def test_benchmark_metrics_without_benchmark(
        self, service: PerformanceService, growing_nav: pd.Series
    ):
        """Benchmark metrics should be NaN when no benchmark is provided."""
        report = service.analyze(nav=growing_nav, benchmark_nav=None)

        assert np.isnan(report.benchmark.beta)
        assert np.isnan(report.benchmark.tracking_error)
        assert np.isnan(report.benchmark.r_squared)

    def test_excess_return_with_benchmark(
        self,
        service: PerformanceService,
        growing_nav: pd.Series,
        benchmark_nav: pd.Series,
    ):
        """Excess return should be computed when benchmark is provided."""
        report = service.analyze(nav=growing_nav, benchmark_nav=benchmark_nav)

        # Growing NAV has ~10% return, benchmark ~8%, so excess should be positive
        assert not np.isnan(report.returns.excess_return)


# ---------------------------------------------------------------------------
# Tests: Attribution
# ---------------------------------------------------------------------------


class TestAttribution:
    """Tests for attribution analysis (Fama-French and Brinson)."""

    def test_fama_french_3factor(
        self,
        service: PerformanceService,
        growing_nav: pd.Series,
        factor_returns_df: pd.DataFrame,
    ):
        """Fama-French 3-factor attribution should produce results."""
        report = service.analyze(
            nav=growing_nav,
            factor_returns=factor_returns_df,
            use_5factor=False,
        )

        assert report.attribution.fama_french is not None
        assert report.attribution.fama_french.model_type == "3-factor"
        assert "MKT" in report.attribution.fama_french.betas
        assert "SMB" in report.attribution.fama_french.betas
        assert "HML" in report.attribution.fama_french.betas

    def test_fama_french_5factor(
        self,
        service: PerformanceService,
        growing_nav: pd.Series,
        daily_dates: pd.DatetimeIndex,
    ):
        """Fama-French 5-factor attribution should produce results."""
        np.random.seed(77)
        n = len(daily_dates)
        factor_returns_5 = pd.DataFrame(
            {
                "MKT": np.random.normal(0.0003, 0.01, n),
                "SMB": np.random.normal(0.0001, 0.005, n),
                "HML": np.random.normal(0.0001, 0.005, n),
                "RMW": np.random.normal(0.0001, 0.004, n),
                "CMA": np.random.normal(0.0001, 0.004, n),
            },
            index=daily_dates,
        )

        report = service.analyze(
            nav=growing_nav,
            factor_returns=factor_returns_5,
            use_5factor=True,
        )

        assert report.attribution.fama_french is not None
        assert report.attribution.fama_french.model_type == "5-factor"
        assert "RMW" in report.attribution.fama_french.betas
        assert "CMA" in report.attribution.fama_french.betas

    def test_fama_french_without_factor_data(
        self, service: PerformanceService, growing_nav: pd.Series
    ):
        """Without factor data, Fama-French should be None."""
        report = service.analyze(nav=growing_nav, factor_returns=None)
        assert report.attribution.fama_french is None

    def test_brinson_attribution(self, service: PerformanceService):
        """Brinson attribution should decompose excess return."""
        nav = pd.Series(
            np.linspace(1.0, 1.1, 100),
            index=pd.bdate_range("2020-01-02", periods=100),
        )
        brinson_data = {
            "portfolio_weights": {"stocks": 0.6, "bonds": 0.4},
            "benchmark_weights": {"stocks": 0.5, "bonds": 0.5},
            "portfolio_returns": {"stocks": 0.10, "bonds": 0.02},
            "benchmark_returns": {"stocks": 0.08, "bonds": 0.03},
        }

        report = service.analyze(nav=nav, brinson_data=brinson_data)

        assert report.attribution.brinson is not None
        assert "stocks" in report.attribution.brinson.allocation_effect
        assert "bonds" in report.attribution.brinson.allocation_effect
        assert "total" in report.attribution.brinson.allocation_effect

    def test_brinson_without_data(
        self, service: PerformanceService, growing_nav: pd.Series
    ):
        """Without Brinson data, Brinson result should be None."""
        report = service.analyze(nav=growing_nav, brinson_data=None)
        assert report.attribution.brinson is None


# ---------------------------------------------------------------------------
# Tests: Multi-strategy comparison
# ---------------------------------------------------------------------------


class TestMultiStrategyComparison:
    """Tests for multi-strategy comparison."""

    def test_compare_two_strategies(
        self,
        service: PerformanceService,
        growing_nav: pd.Series,
        benchmark_nav: pd.Series,
    ):
        """Compare should produce reports for each strategy."""
        strategies = {
            "aggressive": growing_nav,
            "conservative": benchmark_nav,
        }

        comparison = service.compare(strategies=strategies)

        assert isinstance(comparison, ComparisonReport)
        assert len(comparison.strategies) == 2
        assert comparison.strategies[0].strategy_name == "aggressive"
        assert comparison.strategies[1].strategy_name == "conservative"

    def test_comparison_table_structure(
        self,
        service: PerformanceService,
        growing_nav: pd.Series,
        benchmark_nav: pd.Series,
    ):
        """Comparison table should contain key metrics for each strategy."""
        strategies = {
            "strategy_a": growing_nav,
            "strategy_b": benchmark_nav,
        }

        comparison = service.compare(strategies=strategies)
        table = comparison.to_dict()["comparison_table"]

        assert "strategy_a" in table
        assert "strategy_b" in table

        # Check key metrics are present
        for name in ["strategy_a", "strategy_b"]:
            assert "total_return" in table[name]
            assert "annualized_return" in table[name]
            assert "volatility" in table[name]
            assert "max_drawdown" in table[name]
            assert "sharpe" in table[name]

    def test_compare_with_benchmark(
        self,
        service: PerformanceService,
        growing_nav: pd.Series,
        benchmark_nav: pd.Series,
        daily_dates: pd.DatetimeIndex,
    ):
        """Comparison with shared benchmark should compute benchmark metrics."""
        np.random.seed(200)
        another_nav = pd.Series(
            np.cumprod(1 + np.random.normal(0.0002, 0.008, len(daily_dates))),
            index=daily_dates,
        )

        strategies = {"growth": growing_nav, "moderate": another_nav}
        comparison = service.compare(
            strategies=strategies, benchmark_nav=benchmark_nav
        )

        for report in comparison.strategies:
            assert not np.isnan(report.benchmark.beta)

    def test_compare_empty_strategies(self, service: PerformanceService):
        """Empty strategies dict should produce empty comparison."""
        comparison = service.compare(strategies={})
        assert len(comparison.strategies) == 0
        assert comparison.to_dict()["comparison_table"] == {}


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_nav_series(self, service: PerformanceService):
        """Empty NAV should produce NaN metrics without errors."""
        empty_nav = pd.Series([], dtype=float)
        report = service.analyze(nav=empty_nav)

        assert np.isnan(report.returns.total_return)
        assert np.isnan(report.risk.volatility)

    def test_single_point_nav(self, service: PerformanceService):
        """Single data point NAV should produce NaN metrics."""
        single_nav = pd.Series(
            [1.0], index=pd.DatetimeIndex(["2020-01-02"])
        )
        report = service.analyze(nav=single_nav)

        assert np.isnan(report.returns.total_return)

    def test_constant_nav(self, service: PerformanceService):
        """Constant NAV (zero return) should produce zero metrics."""
        dates = pd.bdate_range("2020-01-02", periods=100)
        constant_nav = pd.Series(1.0, index=dates)
        report = service.analyze(nav=constant_nav)

        assert report.returns.total_return == 0.0
        assert report.returns.annualized_return == 0.0

    def test_custom_risk_free_rate(self, growing_nav: pd.Series):
        """Custom risk-free rate should affect Sharpe calculation."""
        service_zero = PerformanceService(risk_free_rate=0.0)
        service_high = PerformanceService(risk_free_rate=0.05)

        report_zero = service_zero.analyze(nav=growing_nav)
        report_high = service_high.analyze(nav=growing_nav)

        # Higher risk-free rate should reduce Sharpe
        assert report_high.risk_adjusted.sharpe < report_zero.risk_adjusted.sharpe


# ---------------------------------------------------------------------------
# Tests: JSON serialization
# ---------------------------------------------------------------------------


class TestJsonSerialization:
    """Tests for JSON output structure."""

    def test_report_to_dict_structure(
        self,
        service: PerformanceService,
        growing_nav: pd.Series,
        benchmark_nav: pd.Series,
    ):
        """to_dict() should produce a well-structured JSON-serializable dict."""
        report = service.analyze(
            nav=growing_nav,
            benchmark_nav=benchmark_nav,
            strategy_name="test",
        )
        data = report.to_dict()

        assert data["strategy_name"] == "test"
        assert "returns" in data
        assert "risk" in data
        assert "risk_adjusted" in data
        assert "benchmark" in data
        assert "attribution" in data

    def test_report_to_dict_no_nan(
        self,
        service: PerformanceService,
        growing_nav: pd.Series,
        benchmark_nav: pd.Series,
    ):
        """Serialized dict should not contain NaN values (replaced by None)."""
        report = service.analyze(nav=growing_nav, benchmark_nav=benchmark_nav)
        data = report.to_dict()

        def check_no_nan(obj: object, path: str = "") -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    check_no_nan(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    check_no_nan(v, f"{path}[{i}]")
            elif isinstance(obj, float):
                assert not np.isnan(obj), f"NaN found at {path}"
                assert not np.isinf(obj), f"Inf found at {path}"

        check_no_nan(data)

    def test_comparison_to_dict_structure(
        self,
        service: PerformanceService,
        growing_nav: pd.Series,
        benchmark_nav: pd.Series,
    ):
        """ComparisonReport.to_dict() should have strategies and comparison_table."""
        strategies = {"a": growing_nav, "b": benchmark_nav}
        comparison = service.compare(strategies=strategies)
        data = comparison.to_dict()

        assert "strategies" in data
        assert "comparison_table" in data
        assert len(data["strategies"]) == 2

    def test_attribution_to_dict_with_fama_french(
        self,
        service: PerformanceService,
        growing_nav: pd.Series,
        factor_returns_df: pd.DataFrame,
    ):
        """Attribution section should serialize Fama-French results."""
        report = service.analyze(
            nav=growing_nav, factor_returns=factor_returns_df
        )
        data = report.to_dict()

        ff = data["attribution"]["fama_french"]
        assert ff is not None
        assert "alpha" in ff
        assert "betas" in ff
        assert "r_squared" in ff
        assert "model_type" in ff

    def test_attribution_to_dict_with_brinson(
        self, service: PerformanceService
    ):
        """Attribution section should serialize Brinson results."""
        nav = pd.Series(
            np.linspace(1.0, 1.1, 100),
            index=pd.bdate_range("2020-01-02", periods=100),
        )
        brinson_data = {
            "portfolio_weights": {"tech": 0.7, "finance": 0.3},
            "benchmark_weights": {"tech": 0.5, "finance": 0.5},
            "portfolio_returns": {"tech": 0.12, "finance": 0.04},
            "benchmark_returns": {"tech": 0.10, "finance": 0.05},
        }

        report = service.analyze(nav=nav, brinson_data=brinson_data)
        data = report.to_dict()

        brinson = data["attribution"]["brinson"]
        assert brinson is not None
        assert "allocation_effect" in brinson
        assert "selection_effect" in brinson
        assert "interaction_effect" in brinson
        assert "total_excess_return" in brinson
        assert "sectors" in brinson
