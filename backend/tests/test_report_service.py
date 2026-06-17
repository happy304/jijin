"""Unit tests for ReportService.

Covers:
- HTML report generation with all chart sections
- Report with and without benchmark
- Metrics table generation (from dict and computed)
- Chart generation helpers (equity curve, heatmap, rolling sharpe/beta, drawdown)
- Drawdown duration computation
- Edge cases: empty NAV, short NAV, constant NAV
- File save functionality

Satisfies requirement 6.6.
"""

from __future__ import annotations

import os
import tempfile

import numpy as np
import pandas as pd
import pytest

from app.services.report_service import (
    ReportConfig,
    ReportResult,
    ReportService,
    _compute_drawdown_durations,
    _fig_to_base64,
    _generate_drawdown_duration_distribution,
    _generate_equity_curve,
    _generate_monthly_heatmap,
    _generate_rolling_beta,
    _generate_rolling_sharpe,
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
    """A steadily growing NAV series (~10% annual return)."""
    np.random.seed(42)
    daily_return = 0.10 / 252
    noise = np.random.normal(0, 0.01, len(daily_dates))
    cumulative = np.cumprod(1 + daily_return + noise)
    return pd.Series(cumulative, index=daily_dates, name="nav")


@pytest.fixture
def benchmark_nav(daily_dates: pd.DatetimeIndex) -> pd.Series:
    """A benchmark NAV series (~8% annual return)."""
    np.random.seed(123)
    daily_return = 0.08 / 252
    noise = np.random.normal(0, 0.008, len(daily_dates))
    cumulative = np.cumprod(1 + daily_return + noise)
    return pd.Series(cumulative, index=daily_dates, name="benchmark")


@pytest.fixture
def service() -> ReportService:
    """Default ReportService instance."""
    return ReportService()


# ---------------------------------------------------------------------------
# Tests: Drawdown duration computation
# ---------------------------------------------------------------------------


class TestDrawdownDurations:
    """Tests for _compute_drawdown_durations helper."""

    def test_no_drawdown(self):
        """Monotonically increasing NAV should have no drawdowns."""
        nav = pd.Series(
            np.linspace(1.0, 2.0, 100),
            index=pd.bdate_range("2020-01-02", periods=100),
        )
        durations = _compute_drawdown_durations(nav)
        assert durations == []

    def test_single_drawdown(self):
        """A single dip and recovery should produce one duration."""
        values = [1.0, 1.1, 1.2, 1.1, 1.0, 1.1, 1.2, 1.3]
        nav = pd.Series(values, index=pd.bdate_range("2020-01-02", periods=8))
        durations = _compute_drawdown_durations(nav)
        # After peak at 1.2 (index 2), drops to 1.1, 1.0, 1.1, 1.2 (recovery)
        # Duration = 3 days (indices 3, 4, 5) then recovers at index 6
        assert len(durations) >= 1
        assert all(d > 0 for d in durations)

    def test_ongoing_drawdown(self):
        """Drawdown at end of series should still be counted."""
        values = [1.0, 1.2, 1.1, 1.0, 0.9]
        nav = pd.Series(values, index=pd.bdate_range("2020-01-02", periods=5))
        durations = _compute_drawdown_durations(nav)
        assert len(durations) >= 1
        # Ongoing drawdown from index 2 to end = 3 days
        assert durations[-1] == 3

    def test_empty_nav(self):
        """Empty NAV should return empty list."""
        nav = pd.Series([], dtype=float)
        assert _compute_drawdown_durations(nav) == []

    def test_single_point(self):
        """Single point NAV should return empty list."""
        nav = pd.Series([1.0], index=pd.DatetimeIndex(["2020-01-02"]))
        assert _compute_drawdown_durations(nav) == []

    def test_multiple_drawdowns(self):
        """Multiple drawdowns should all be captured."""
        # Peak, dip, recover, peak, dip, recover
        values = [1.0, 1.2, 1.0, 1.2, 1.3, 1.1, 1.3, 1.4]
        nav = pd.Series(values, index=pd.bdate_range("2020-01-02", periods=8))
        durations = _compute_drawdown_durations(nav)
        assert len(durations) >= 2


# ---------------------------------------------------------------------------
# Tests: Chart generation helpers
# ---------------------------------------------------------------------------


class TestChartHelpers:
    """Tests for individual chart generation functions."""

    def test_fig_to_base64_returns_string(self):
        """_fig_to_base64 should return a non-empty base64 string."""
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots()
        ax.plot([1, 2, 3], [1, 2, 3])
        result = _fig_to_base64(fig)
        assert isinstance(result, str)
        assert len(result) > 100  # Should be a substantial base64 string

    def test_equity_curve_returns_base64(self, growing_nav: pd.Series):
        """Equity curve chart should return valid base64."""
        result = _generate_equity_curve(growing_nav)
        assert isinstance(result, str)
        assert len(result) > 100

    def test_equity_curve_with_benchmark(
        self, growing_nav: pd.Series, benchmark_nav: pd.Series
    ):
        """Equity curve with benchmark should return valid base64."""
        result = _generate_equity_curve(growing_nav, benchmark_nav)
        assert isinstance(result, str)
        assert len(result) > 100

    def test_monthly_heatmap_returns_base64(self, growing_nav: pd.Series):
        """Monthly heatmap should return valid base64."""
        result = _generate_monthly_heatmap(growing_nav)
        assert isinstance(result, str)
        assert len(result) > 100

    def test_monthly_heatmap_empty_nav(self):
        """Monthly heatmap with empty NAV should still return base64 (placeholder)."""
        nav = pd.Series([], dtype=float)
        result = _generate_monthly_heatmap(nav)
        assert isinstance(result, str)
        assert len(result) > 50

    def test_rolling_sharpe_returns_base64(self, growing_nav: pd.Series):
        """Rolling Sharpe chart should return valid base64."""
        result = _generate_rolling_sharpe(growing_nav, window=60)
        assert isinstance(result, str)
        assert len(result) > 100

    def test_rolling_sharpe_short_nav(self):
        """Rolling Sharpe with short NAV should return placeholder."""
        nav = pd.Series([1.0, 1.01, 1.02], index=pd.bdate_range("2020-01-02", periods=3))
        result = _generate_rolling_sharpe(nav, window=60)
        assert isinstance(result, str)
        assert len(result) > 50

    def test_rolling_beta_returns_base64(
        self, growing_nav: pd.Series, benchmark_nav: pd.Series
    ):
        """Rolling Beta chart should return valid base64."""
        result = _generate_rolling_beta(growing_nav, benchmark_nav, window=60)
        assert isinstance(result, str)
        assert len(result) > 100

    def test_drawdown_distribution_returns_base64(self, growing_nav: pd.Series):
        """Drawdown distribution chart should return valid base64."""
        result = _generate_drawdown_duration_distribution(growing_nav)
        assert isinstance(result, str)
        assert len(result) > 100

    def test_drawdown_distribution_no_drawdowns(self):
        """Drawdown distribution with no drawdowns should return placeholder."""
        nav = pd.Series(
            np.linspace(1.0, 2.0, 100),
            index=pd.bdate_range("2020-01-02", periods=100),
        )
        result = _generate_drawdown_duration_distribution(nav)
        assert isinstance(result, str)
        assert len(result) > 50


# ---------------------------------------------------------------------------
# Tests: Full report generation
# ---------------------------------------------------------------------------


class TestReportGeneration:
    """Tests for full HTML report generation."""

    def test_generate_report_returns_result(
        self, service: ReportService, growing_nav: pd.Series
    ):
        """generate_report should return a ReportResult."""
        result = service.generate_report(nav=growing_nav, strategy_name="test")
        assert isinstance(result, ReportResult)
        assert result.title == "绩效分析报告"
        assert len(result.generated_at) > 0

    def test_report_html_contains_structure(
        self, service: ReportService, growing_nav: pd.Series
    ):
        """Generated HTML should contain expected structural elements."""
        result = service.generate_report(nav=growing_nav, strategy_name="my_strategy")
        html = result.html

        assert "<!DOCTYPE html>" in html
        assert "绩效分析报告" in html
        assert "my_strategy" in html
        assert "摘要指标" in html
        assert "净值曲线" in html
        assert "月度收益热力图" in html
        assert "滚动 Sharpe" in html
        assert "滚动 Beta" in html
        assert "回撤持续时间分布" in html

    def test_report_html_contains_base64_images(
        self, service: ReportService, growing_nav: pd.Series
    ):
        """Generated HTML should contain base64-encoded images."""
        result = service.generate_report(nav=growing_nav)
        html = result.html

        # Should have multiple base64 image references
        assert html.count("data:image/png;base64,") >= 5

    def test_report_with_benchmark(
        self,
        service: ReportService,
        growing_nav: pd.Series,
        benchmark_nav: pd.Series,
    ):
        """Report with benchmark should include Beta chart and metrics."""
        result = service.generate_report(
            nav=growing_nav, benchmark_nav=benchmark_nav, strategy_name="with_bench"
        )
        html = result.html

        assert "Beta" in html
        assert "rolling_beta" in result.charts

    def test_report_without_benchmark(
        self, service: ReportService, growing_nav: pd.Series
    ):
        """Report without benchmark should still generate (with placeholder)."""
        result = service.generate_report(nav=growing_nav, benchmark_nav=None)
        html = result.html

        # Should still have the rolling beta section (with placeholder)
        assert "滚动 Beta" in html
        assert "rolling_beta" in result.charts

    def test_report_charts_dict(
        self, service: ReportService, growing_nav: pd.Series, benchmark_nav: pd.Series
    ):
        """Charts dict should contain all expected keys."""
        result = service.generate_report(nav=growing_nav, benchmark_nav=benchmark_nav)

        expected_keys = {
            "equity_curve",
            "monthly_heatmap",
            "rolling_sharpe",
            "rolling_beta",
            "drawdown_distribution",
        }
        assert set(result.charts.keys()) == expected_keys

    def test_report_with_metrics_dict(
        self, service: ReportService, growing_nav: pd.Series
    ):
        """Report with pre-computed metrics dict should use them."""
        metrics = {
            "returns": {"total_return": 0.15, "annualized_return": 0.10},
            "risk": {"volatility": 0.12, "max_drawdown": -0.08},
            "risk_adjusted": {"sharpe": 1.2, "sortino": 1.5},
            "benchmark": {"beta": 0.9},
        }
        result = service.generate_report(
            nav=growing_nav, strategy_name="test", metrics=metrics
        )
        html = result.html

        # Should contain formatted metric values
        assert "Sharpe" in html

    def test_report_with_flat_metrics_dict(
        self, service: ReportService, growing_nav: pd.Series
    ):
        """Report with flat metrics dict should work."""
        metrics = {
            "total_return": 0.15,
            "annualized_return": 0.10,
            "volatility": 0.12,
            "max_drawdown": -0.08,
            "sharpe": 1.2,
        }
        result = service.generate_report(
            nav=growing_nav, strategy_name="test", metrics=metrics
        )
        assert "Sharpe" in result.html


# ---------------------------------------------------------------------------
# Tests: File save
# ---------------------------------------------------------------------------


class TestFileSave:
    """Tests for saving report to file."""

    def test_save_html_creates_file(
        self, service: ReportService, growing_nav: pd.Series
    ):
        """save_html should create an HTML file."""
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            output_path = f.name

        try:
            result_path = service.save_html(
                nav=growing_nav,
                output_path=output_path,
                strategy_name="save_test",
            )
            assert os.path.exists(result_path)
            with open(result_path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "<!DOCTYPE html>" in content
            assert "save_test" in content
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_save_html_with_benchmark(
        self,
        service: ReportService,
        growing_nav: pd.Series,
        benchmark_nav: pd.Series,
    ):
        """save_html with benchmark should create a complete report file."""
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            output_path = f.name

        try:
            service.save_html(
                nav=growing_nav,
                output_path=output_path,
                benchmark_nav=benchmark_nav,
                strategy_name="bench_test",
            )
            assert os.path.exists(output_path)
            with open(output_path, "r", encoding="utf-8") as f:
                content = f.read()
            assert len(content) > 1000  # Should be a substantial file
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_nav(self, service: ReportService):
        """Empty NAV should produce a report without errors."""
        empty_nav = pd.Series([], dtype=float)
        result = service.generate_report(nav=empty_nav)
        assert isinstance(result, ReportResult)
        assert "<!DOCTYPE html>" in result.html

    def test_short_nav(self, service: ReportService):
        """Very short NAV should produce a report without errors."""
        short_nav = pd.Series(
            [1.0, 1.01, 1.02],
            index=pd.bdate_range("2020-01-02", periods=3),
        )
        result = service.generate_report(nav=short_nav)
        assert isinstance(result, ReportResult)
        assert "<!DOCTYPE html>" in result.html

    def test_constant_nav(self, service: ReportService):
        """Constant NAV should produce a report without errors."""
        dates = pd.bdate_range("2020-01-02", periods=100)
        constant_nav = pd.Series(1.0, index=dates)
        result = service.generate_report(nav=constant_nav)
        assert isinstance(result, ReportResult)
        assert "<!DOCTYPE html>" in result.html

    def test_custom_config(self, growing_nav: pd.Series):
        """Custom ReportConfig should be respected."""
        config = ReportConfig(
            title="自定义报告",
            rolling_window=30,
            figsize=(8, 3),
            dpi=72,
        )
        service = ReportService(config=config)
        result = service.generate_report(nav=growing_nav)
        assert "自定义报告" in result.html
        assert result.title == "自定义报告"


# ---------------------------------------------------------------------------
# Tests: Metric formatting
# ---------------------------------------------------------------------------


class TestMetricFormatting:
    """Tests for metric value formatting."""

    def test_format_positive_value(self):
        """Positive values should get positive class."""
        service = ReportService()
        formatted = service._format_metric_value(0.15)
        assert "positive" in formatted
        assert "15.00%" in formatted

    def test_format_negative_value(self):
        """Negative values should get negative class."""
        service = ReportService()
        formatted = service._format_metric_value(-0.08)
        assert "negative" in formatted

    def test_format_none_value(self):
        """None values should show N/A."""
        service = ReportService()
        formatted = service._format_metric_value(None)
        assert "N/A" in formatted

    def test_format_ratio_value(self):
        """Ratio values (>1) should be formatted as decimals."""
        service = ReportService()
        formatted = service._format_metric_value(1.5)
        assert "1.5" in formatted
