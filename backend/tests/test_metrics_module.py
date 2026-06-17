"""Unit tests for :mod:`app.observability.metrics`.

Validates that every required domain metric exists with the correct
Prometheus type and label names, and that helper functions correctly
record metric observations.

Requirement 8.5 depends on the shape of these series (the Grafana
dashboard queries them verbatim) so the test locks the contract in place.
"""

from __future__ import annotations

import pytest
from prometheus_client import Counter, Gauge, Histogram

from app.observability import metrics as m


# The contract: metric name → (factory class, label names).
_EXPECTED: dict[str, tuple[type, tuple[str, ...]]] = {
    "ingest_requests_total": (Counter, ("provider", "endpoint", "status")),
    "ingest_latency_seconds": (Histogram, ("provider", "endpoint")),
    "data_completeness_ratio": (Gauge, ("fund_code",)),
    "backtest_runs_total": (Counter, ("strategy_type", "status")),
    "backtest_duration_seconds": (Histogram, ("strategy_type",)),
    "llm_calls_total": (Counter, ("provider", "model", "use_case", "status")),
    "llm_cost_usd_total": (Counter, ("provider", "model")),
    "llm_tokens_total": (Counter, ("provider", "model", "direction")),
    "task_queue_depth": (Gauge, ("queue_name",)),
}


@pytest.mark.parametrize("name, spec", list(_EXPECTED.items()))
def test_metric_exists_with_correct_type_and_labels(
    name: str, spec: tuple[type, tuple[str, ...]]
) -> None:
    factory, expected_labels = spec
    assert name in m.ALL_METRICS, f"metric {name!r} missing from ALL_METRICS"
    metric = m.ALL_METRICS[name]
    assert isinstance(metric, factory), (
        f"metric {name!r} expected {factory.__name__}, got {type(metric).__name__}"
    )
    # `_labelnames` is the only way to introspect label shape.
    assert tuple(metric._labelnames) == expected_labels, (  # noqa: SLF001
        f"metric {name!r} labels: expected {expected_labels}, "
        f"got {tuple(metric._labelnames)}"  # noqa: SLF001
    )


def test_all_metrics_are_exported() -> None:
    """Spot-check that metric constants are importable by name."""
    for name in _EXPECTED:
        assert name.upper() in dir(m)


def test_counter_and_histogram_samples_appear_on_observation() -> None:
    """Incrementing a counter and observing a histogram must produce
    the canonical Prometheus exposition shape for our series."""
    m.INGEST_REQUESTS_TOTAL.labels(
        provider="eastmoney", endpoint="nav_history", status="success"
    ).inc()
    m.INGEST_LATENCY_SECONDS.labels(
        provider="eastmoney", endpoint="nav_history"
    ).observe(0.123)

    samples = {
        sample.name: sample
        for sample in m.INGEST_REQUESTS_TOTAL.collect()[0].samples
    }
    assert "ingest_requests_total" in samples

    hist_samples = m.INGEST_LATENCY_SECONDS.collect()[0].samples
    bucket_names = {s.name for s in hist_samples}
    assert "ingest_latency_seconds_bucket" in bucket_names
    assert "ingest_latency_seconds_sum" in bucket_names
    assert "ingest_latency_seconds_count" in bucket_names


# =====================================================================
# Helper function tests
# =====================================================================


class TestRecordIngestRequest:
    """Tests for record_ingest_request helper."""

    def test_increments_counter(self) -> None:
        before = m.INGEST_REQUESTS_TOTAL.labels(
            provider="akshare", endpoint="fund_meta", status="success"
        )._value.get()
        m.record_ingest_request("akshare", "fund_meta", "success")
        after = m.INGEST_REQUESTS_TOTAL.labels(
            provider="akshare", endpoint="fund_meta", status="success"
        )._value.get()
        assert after == before + 1

    def test_records_latency_when_provided(self) -> None:
        before_count = m.INGEST_LATENCY_SECONDS.labels(
            provider="eastmoney", endpoint="holdings"
        )._sum.get()
        m.record_ingest_request(
            "eastmoney", "holdings", "success", latency_seconds=1.5
        )
        after_count = m.INGEST_LATENCY_SECONDS.labels(
            provider="eastmoney", endpoint="holdings"
        )._sum.get()
        assert after_count >= before_count + 1.5

    def test_no_latency_when_not_provided(self) -> None:
        before_sum = m.INGEST_LATENCY_SECONDS.labels(
            provider="eastmoney", endpoint="dividends"
        )._sum.get()
        m.record_ingest_request("eastmoney", "dividends", "error")
        after_sum = m.INGEST_LATENCY_SECONDS.labels(
            provider="eastmoney", endpoint="dividends"
        )._sum.get()
        # Sum should not change since no latency was provided
        assert after_sum == before_sum


class TestTrackIngestLatency:
    """Tests for track_ingest_latency context manager."""

    def test_records_elapsed_time(self) -> None:
        before_sum = m.INGEST_LATENCY_SECONDS.labels(
            provider="eastmoney", endpoint="nav_realtime"
        )._sum.get()
        with m.track_ingest_latency("eastmoney", "nav_realtime"):
            pass  # Simulate fast operation
        after_sum = m.INGEST_LATENCY_SECONDS.labels(
            provider="eastmoney", endpoint="nav_realtime"
        )._sum.get()
        # Sum should increase (even if by a tiny amount)
        assert after_sum > before_sum

    def test_records_even_on_exception(self) -> None:
        before_sum = m.INGEST_LATENCY_SECONDS.labels(
            provider="eastmoney", endpoint="announcements"
        )._sum.get()
        with pytest.raises(ValueError):
            with m.track_ingest_latency("eastmoney", "announcements"):
                raise ValueError("simulated error")
        after_sum = m.INGEST_LATENCY_SECONDS.labels(
            provider="eastmoney", endpoint="announcements"
        )._sum.get()
        assert after_sum > before_sum


class TestSetDataCompleteness:
    """Tests for set_data_completeness helper."""

    def test_sets_gauge_value(self) -> None:
        m.set_data_completeness("000001", 0.95)
        value = m.DATA_COMPLETENESS_RATIO.labels(fund_code="000001")._value.get()
        assert value == 0.95

    def test_overwrites_previous_value(self) -> None:
        m.set_data_completeness("110011", 0.8)
        m.set_data_completeness("110011", 0.99)
        value = m.DATA_COMPLETENESS_RATIO.labels(fund_code="110011")._value.get()
        assert value == 0.99


class TestRecordBacktestRun:
    """Tests for record_backtest_run helper."""

    def test_increments_counter(self) -> None:
        before = m.BACKTEST_RUNS_TOTAL.labels(
            strategy_type="momentum", status="success"
        )._value.get()
        m.record_backtest_run("momentum", "success")
        after = m.BACKTEST_RUNS_TOTAL.labels(
            strategy_type="momentum", status="success"
        )._value.get()
        assert after == before + 1

    def test_records_duration_when_provided(self) -> None:
        before_sum = m.BACKTEST_DURATION_SECONDS.labels(
            strategy_type="dca"
        )._sum.get()
        m.record_backtest_run("dca", "success", duration_seconds=12.5)
        after_sum = m.BACKTEST_DURATION_SECONDS.labels(
            strategy_type="dca"
        )._sum.get()
        assert after_sum >= before_sum + 12.5

    def test_no_duration_when_not_provided(self) -> None:
        before_sum = m.BACKTEST_DURATION_SECONDS.labels(
            strategy_type="risk_parity"
        )._sum.get()
        m.record_backtest_run("risk_parity", "failed")
        after_sum = m.BACKTEST_DURATION_SECONDS.labels(
            strategy_type="risk_parity"
        )._sum.get()
        assert after_sum == before_sum


class TestTrackBacktestDuration:
    """Tests for track_backtest_duration context manager."""

    def test_records_elapsed_time(self) -> None:
        before_sum = m.BACKTEST_DURATION_SECONDS.labels(
            strategy_type="timing"
        )._sum.get()
        with m.track_backtest_duration("timing"):
            pass
        after_sum = m.BACKTEST_DURATION_SECONDS.labels(
            strategy_type="timing"
        )._sum.get()
        assert after_sum > before_sum


class TestRecordLlmCall:
    """Tests for record_llm_call helper."""

    def test_increments_call_counter(self) -> None:
        before = m.LLM_CALLS_TOTAL.labels(
            provider="openai", model="gpt-4o",
            use_case="nl_query", status="success"
        )._value.get()
        m.record_llm_call(
            provider="openai", model="gpt-4o",
            use_case="nl_query", status="success",
            prompt_tokens=100, completion_tokens=50, cost_usd=0.01,
        )
        after = m.LLM_CALLS_TOTAL.labels(
            provider="openai", model="gpt-4o",
            use_case="nl_query", status="success"
        )._value.get()
        assert after == before + 1

    def test_records_cost(self) -> None:
        before = m.LLM_COST_USD_TOTAL.labels(
            provider="deepseek", model="deepseek-chat"
        )._value.get()
        m.record_llm_call(
            provider="deepseek", model="deepseek-chat",
            use_case="announcement_parse", status="success",
            cost_usd=0.005,
        )
        after = m.LLM_COST_USD_TOTAL.labels(
            provider="deepseek", model="deepseek-chat"
        )._value.get()
        assert after >= before + 0.005

    def test_records_tokens(self) -> None:
        before_prompt = m.LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="claude-3", direction="prompt"
        )._value.get()
        before_completion = m.LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="claude-3", direction="completion"
        )._value.get()
        m.record_llm_call(
            provider="anthropic", model="claude-3",
            use_case="attribution_report", status="success",
            prompt_tokens=200, completion_tokens=300,
        )
        after_prompt = m.LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="claude-3", direction="prompt"
        )._value.get()
        after_completion = m.LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="claude-3", direction="completion"
        )._value.get()
        assert after_prompt == before_prompt + 200
        assert after_completion == before_completion + 300

    def test_zero_cost_not_recorded(self) -> None:
        before = m.LLM_COST_USD_TOTAL.labels(
            provider="openai", model="gpt-4o-mini"
        )._value.get()
        m.record_llm_call(
            provider="openai", model="gpt-4o-mini",
            use_case="test", status="error",
            cost_usd=0.0,
        )
        after = m.LLM_COST_USD_TOTAL.labels(
            provider="openai", model="gpt-4o-mini"
        )._value.get()
        assert after == before


class TestSetTaskQueueDepth:
    """Tests for set_task_queue_depth helper."""

    def test_sets_gauge_value(self) -> None:
        m.set_task_queue_depth("ingest", 42)
        value = m.TASK_QUEUE_DEPTH.labels(queue_name="ingest")._value.get()
        assert value == 42

    def test_overwrites_previous_value(self) -> None:
        m.set_task_queue_depth("backtest", 10)
        m.set_task_queue_depth("backtest", 3)
        value = m.TASK_QUEUE_DEPTH.labels(queue_name="backtest")._value.get()
        assert value == 3

    def test_multiple_queues_independent(self) -> None:
        m.set_task_queue_depth("ai", 5)
        m.set_task_queue_depth("notify", 2)
        assert m.TASK_QUEUE_DEPTH.labels(queue_name="ai")._value.get() == 5
        assert m.TASK_QUEUE_DEPTH.labels(queue_name="notify")._value.get() == 2
