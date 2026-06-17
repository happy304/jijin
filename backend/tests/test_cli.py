"""Tests for the CLI module (app/cli.py).

Tests verify that CLI commands correctly dispatch Celery tasks
and handle the --sync flag. We mock Celery task.delay() calls
to avoid needing a running broker.

Requirements: 8.8
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_task_result():
    """Create a mock AsyncResult returned by task.delay()."""
    result = MagicMock()
    result.id = "test-task-id-12345"
    result.get.return_value = {"success": 1, "failed": 0, "total": 1}
    return result


# ---------------------------------------------------------------------------
# Version & Status commands
# ---------------------------------------------------------------------------


class TestVersionCommand:
    def test_version_output(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "Fund Quant Platform" in result.output
        assert "0.1.0" in result.output


class TestStatusCommand:
    @patch("app.tasks.celery_app.celery_app")
    def test_status_with_workers(self, mock_celery):
        mock_celery.control.ping.return_value = [{"worker1@host": {"ok": "pong"}}]
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "Celery workers 在线" in result.output

    @patch("app.tasks.celery_app.celery_app")
    def test_status_no_workers(self, mock_celery):
        mock_celery.control.ping.return_value = []
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "未发现在线 Celery worker" in result.output

    @patch("app.tasks.celery_app.celery_app")
    def test_status_connection_error(self, mock_celery):
        mock_celery.control.ping.side_effect = ConnectionError("refused")
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "连接失败" in result.output


# ---------------------------------------------------------------------------
# Ingest commands
# ---------------------------------------------------------------------------


class TestIngestNav:
    @patch("app.tasks.ingest.update_daily_nav.delay")
    def test_ingest_nav_async(self, mock_delay, mock_task_result):
        mock_delay.return_value = mock_task_result
        result = runner.invoke(app, ["ingest", "nav", "--fund", "000001"])
        assert result.exit_code == 0
        assert "任务已提交" in result.output
        assert "test-task-id-12345" in result.output
        mock_delay.assert_called_once_with(fund_code="000001")

    @patch("app.tasks.ingest.update_daily_nav.delay")
    def test_ingest_nav_sync(self, mock_delay, mock_task_result):
        mock_delay.return_value = mock_task_result
        result = runner.invoke(app, ["ingest", "nav", "--fund", "000001", "--sync"])
        assert result.exit_code == 0
        assert "任务完成" in result.output
        mock_task_result.get.assert_called_once()
        mock_delay.assert_called_once_with(fund_code="000001")

    def test_ingest_nav_missing_fund(self):
        result = runner.invoke(app, ["ingest", "nav"])
        assert result.exit_code != 0


class TestIngestMeta:
    @patch("app.tasks.ingest.update_fund_meta.delay")
    def test_ingest_meta_async(self, mock_delay, mock_task_result):
        mock_delay.return_value = mock_task_result
        result = runner.invoke(app, ["ingest", "meta", "--fund", "110011"])
        assert result.exit_code == 0
        assert "任务已提交" in result.output
        mock_delay.assert_called_once_with(fund_code="110011")

    @patch("app.tasks.ingest.update_fund_meta")
    def test_ingest_meta_sync(self, mock_update_fund_meta):
        mock_update_fund_meta.return_value = {"success": 1, "failed": 0, "total": 1}
        result = runner.invoke(app, ["ingest", "meta", "--fund", "110011", "--sync"])
        assert result.exit_code == 0
        assert "任务完成" in result.output
        assert "success" in result.output
        mock_update_fund_meta.assert_called_once_with(fund_code="110011")


class TestIngestHoldings:
    @patch("app.tasks.ingest.update_holdings.delay")
    def test_ingest_holdings_async(self, mock_delay, mock_task_result):
        mock_delay.return_value = mock_task_result
        result = runner.invoke(app, ["ingest", "holdings", "--fund", "000001"])
        assert result.exit_code == 0
        assert "任务已提交" in result.output
        mock_delay.assert_called_once_with(fund_code="000001", quarter=None)

    @patch("app.tasks.ingest.update_holdings.delay")
    def test_ingest_holdings_with_quarter(self, mock_delay, mock_task_result):
        mock_delay.return_value = mock_task_result
        result = runner.invoke(
            app, ["ingest", "holdings", "--fund", "000001", "--quarter", "2024-Q1"]
        )
        assert result.exit_code == 0
        mock_delay.assert_called_once_with(fund_code="000001", quarter="2024-Q1")


class TestIngestDividends:
    @patch("app.tasks.ingest.update_dividends.delay")
    def test_ingest_dividends_async(self, mock_delay, mock_task_result):
        mock_delay.return_value = mock_task_result
        result = runner.invoke(app, ["ingest", "dividends", "--fund", "000001"])
        assert result.exit_code == 0
        assert "任务已提交" in result.output
        mock_delay.assert_called_once_with(fund_code="000001")


class TestIngestAll:
    @patch("app.tasks.ingest.update_dividends.delay")
    @patch("app.tasks.ingest.update_daily_nav.delay")
    @patch("app.tasks.ingest.update_fund_meta.delay")
    def test_ingest_all_async(self, mock_meta, mock_nav, mock_div, mock_task_result):
        mock_meta.return_value = mock_task_result
        mock_nav.return_value = mock_task_result
        mock_div.return_value = mock_task_result
        result = runner.invoke(app, ["ingest", "all"])
        assert result.exit_code == 0
        assert "全量数据更新" in result.output
        mock_meta.assert_called_once_with()
        mock_nav.assert_called_once_with()
        mock_div.assert_called_once_with()


# ---------------------------------------------------------------------------
# Backtest commands
# ---------------------------------------------------------------------------


class TestBacktestRun:
    @patch("app.cli._create_backtest_run")
    @patch("app.tasks.backtest.run_backtest.delay")
    def test_backtest_run_async(self, mock_delay, mock_create, mock_task_result):
        mock_create.return_value = 42
        mock_delay.return_value = mock_task_result
        result = runner.invoke(app, ["backtest", "run", "--strategy", "1"])
        assert result.exit_code == 0
        assert "任务已提交" in result.output
        assert "run_id=42" in result.output
        mock_delay.assert_called_once_with(run_id=42)

    @patch("app.cli._create_backtest_run")
    @patch("app.tasks.backtest.run_backtest.delay")
    def test_backtest_run_with_dates(self, mock_delay, mock_create, mock_task_result):
        mock_create.return_value = 99
        mock_delay.return_value = mock_task_result
        result = runner.invoke(
            app,
            [
                "backtest", "run",
                "--strategy", "5",
                "--start", "2020-01-01",
                "--end", "2023-12-31",
                "--capital", "500000",
            ],
        )
        assert result.exit_code == 0
        assert "run_id=99" in result.output
        # Verify _create_backtest_run was called with correct args
        from datetime import date

        mock_create.assert_called_once()
        args = mock_create.call_args
        assert args[0][0] == 5  # strategy_id
        assert args[0][1] == date(2020, 1, 1)  # start_date
        assert args[0][2] == date(2023, 12, 31)  # end_date
        assert args[0][3] == 500000.0  # capital

    @patch("app.cli._create_backtest_run")
    def test_backtest_run_db_failure(self, mock_create):
        mock_create.return_value = None
        result = runner.invoke(app, ["backtest", "run", "--strategy", "1"])
        assert result.exit_code == 1

    def test_backtest_run_missing_strategy(self):
        result = runner.invoke(app, ["backtest", "run"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Signal commands
# ---------------------------------------------------------------------------


class TestSignalGenerate:
    @patch("app.tasks.signals.generate_strategy_signals.delay")
    def test_signal_generate_all(self, mock_delay, mock_task_result):
        mock_delay.return_value = mock_task_result
        result = runner.invoke(app, ["signal", "generate"])
        assert result.exit_code == 0
        assert "任务已提交" in result.output
        assert "所有活跃策略" in result.output
        mock_delay.assert_called_once()

    @patch("app.tasks.signals.generate_strategy_signals.delay")
    def test_signal_generate_specific_strategy(self, mock_delay, mock_task_result):
        mock_delay.return_value = mock_task_result
        result = runner.invoke(app, ["signal", "generate", "--strategy", "3"])
        assert result.exit_code == 0
        assert "strategy_id=3" in result.output
        mock_delay.assert_called_once()

    @patch("app.tasks.signals.generate_strategy_signals.delay")
    def test_signal_generate_sync(self, mock_delay, mock_task_result):
        mock_delay.return_value = mock_task_result
        result = runner.invoke(app, ["signal", "generate", "--sync"])
        assert result.exit_code == 0
        assert "任务完成" in result.output
        mock_task_result.get.assert_called_once()


# ---------------------------------------------------------------------------
# Help text tests
# ---------------------------------------------------------------------------


class TestHelpOutput:
    def test_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "ingest" in result.output
        assert "backtest" in result.output
        assert "signal" in result.output

    def test_ingest_help(self):
        result = runner.invoke(app, ["ingest", "--help"])
        assert result.exit_code == 0
        assert "nav" in result.output
        assert "meta" in result.output
        assert "all" in result.output

    def test_backtest_help(self):
        result = runner.invoke(app, ["backtest", "--help"])
        assert result.exit_code == 0
        assert "run" in result.output

    def test_signal_help(self):
        result = runner.invoke(app, ["signal", "--help"])
        assert result.exit_code == 0
        assert "generate" in result.output
