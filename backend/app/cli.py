"""Command line interface for Fund Quant Platform.

Provides CLI commands to manually trigger Celery tasks:
- ``ingest``: Data ingestion (NAV, metadata, holdings, dividends, announcements)
- ``backtest``: Run backtests for strategies
- ``signal``: Generate trading signals

Based on Typer. Entry point: ``fundquant`` (configured in pyproject.toml).

Requirements: 8.8
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from app import __version__

# Windows 上 psycopg async 需要 SelectorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

console = Console()

app = typer.Typer(
    name="fundquant",
    help="基金量化平台 CLI - 手动触发数据采集、回测、信号生成等任务。",
    no_args_is_help=True,
    add_completion=False,
)

# ---------------------------------------------------------------------------
# Ingest command group
# ---------------------------------------------------------------------------

ingest_app = typer.Typer(
    name="ingest",
    help="数据采集命令组：触发基金数据更新任务。",
    no_args_is_help=True,
)
app.add_typer(ingest_app, name="ingest")


def _print_ingest_chain_hint() -> None:
    """Print the unified ingest chain hint for operators."""
    console.print("[dim]当前使用统一采集链：共享任务逻辑 + 多源 Provider 降级（天天基金 → AkShare → 巨潮）[/dim]")


def _print_task_result(result) -> None:
    """Pretty-print a task result payload."""
    if isinstance(result, dict):
        table = Table(title="任务结果")
        table.add_column("字段", style="cyan")
        table.add_column("值", style="white")
        for k, v in result.items():
            table.add_row(str(k), str(v))
        console.print(table)
    else:
        console.print(result)


def _wait_for_task(task_result, task_name: str) -> None:
    """Wait for a Celery task to complete and print the result."""
    console.print(f"[yellow]等待任务 {task_name} 完成...[/yellow]")
    start = time.time()
    try:
        result = task_result.get(timeout=600)  # 10 min timeout
        elapsed = time.time() - start
        console.print(f"[green]✓ 任务完成[/green] (耗时 {elapsed:.1f}s)")
        _print_task_result(result)
    except Exception as e:
        elapsed = time.time() - start
        console.print(f"[red]✗ 任务失败[/red] (耗时 {elapsed:.1f}s): {e}")
        raise typer.Exit(code=1)


@ingest_app.command("nav")
def ingest_nav(
    fund: str = typer.Option(..., "--fund", "-f", help="基金代码"),
    sync: bool = typer.Option(False, "--sync", "-s", help="同步等待任务完成"),
) -> None:
    """触发指定基金的净值数据更新（全量采集，从成立日期开始）。"""
    from app.tasks.ingest import update_daily_nav

    console.print(f"[bold]采集净值数据:[/bold] {fund}")
    _print_ingest_chain_hint()

    if sync:
        try:
            result = update_daily_nav(fund_code=fund, full=True)
            console.print("[green]✓ 任务完成[/green]")
            _print_task_result(result)
        except Exception as e:
            console.print(f"[red]✗ 采集失败: {e}[/red]")
            raise typer.Exit(code=1)
        return

    result = update_daily_nav.delay(fund_code=fund, full=True)
    console.print(f"[green]任务已提交[/green] task_id={result.id}")


@ingest_app.command("meta")
def ingest_meta(
    fund: str = typer.Option(..., "--fund", "-f", help="基金代码"),
    sync: bool = typer.Option(False, "--sync", "-s", help="同步等待任务完成"),
) -> None:
    """触发指定基金的元数据更新。"""
    from app.tasks.ingest import update_fund_meta

    console.print(f"[bold]采集元数据:[/bold] {fund}")
    _print_ingest_chain_hint()

    if sync:
        try:
            result = update_fund_meta(fund_code=fund)
            console.print("[green]✓ 任务完成[/green]")
            _print_task_result(result)
        except Exception as e:
            console.print(f"[red]✗ 采集失败: {e}[/red]")
            raise typer.Exit(code=1)
        return

    result = update_fund_meta.delay(fund_code=fund)
    console.print(f"[green]任务已提交[/green] task_id={result.id}")


@ingest_app.command("holdings")
def ingest_holdings(
    fund: str = typer.Option(..., "--fund", "-f", help="基金代码"),
    quarter: Optional[str] = typer.Option(
        None, "--quarter", "-q", help="季度 (格式: YYYY-QN, 如 2024-Q1)"
    ),
    sync: bool = typer.Option(False, "--sync", "-s", help="同步等待任务完成"),
) -> None:
    """触发指定基金的持仓数据更新。"""
    from app.tasks.ingest import update_holdings

    console.print(f"[bold]触发持仓更新:[/bold] {fund}" + (f" 季度={quarter}" if quarter else ""))
    result = update_holdings.delay(fund_code=fund, quarter=quarter)
    console.print(f"[green]任务已提交[/green] task_id={result.id}")

    if sync:
        _wait_for_task(result, "update_holdings")


@ingest_app.command("dividends")
def ingest_dividends(
    fund: str = typer.Option(..., "--fund", "-f", help="基金代码"),
    sync: bool = typer.Option(False, "--sync", "-s", help="同步等待任务完成"),
) -> None:
    """触发指定基金的分红数据更新。"""
    from app.tasks.ingest import update_dividends

    console.print(f"[bold]触发分红数据更新:[/bold] {fund}")
    result = update_dividends.delay(fund_code=fund)
    console.print(f"[green]任务已提交[/green] task_id={result.id}")

    if sync:
        _wait_for_task(result, "update_dividends")


@ingest_app.command("all")
def ingest_all(
    sync: bool = typer.Option(False, "--sync", "-s", help="同步等待任务完成"),
) -> None:
    """触发全量数据更新（所有活跃基金的元数据+净值+分红）。"""
    from app.tasks.ingest import update_daily_nav, update_dividends, update_fund_meta

    console.print("[bold]触发全量数据更新[/bold]")

    results = []
    for task_fn, name in [
        (update_fund_meta, "元数据"),
        (update_daily_nav, "净值"),
        (update_dividends, "分红"),
    ]:
        result = task_fn.delay()
        console.print(f"  [green]✓[/green] {name} 任务已提交 task_id={result.id}")
        results.append((result, name))

    if sync:
        for result, name in results:
            _wait_for_task(result, name)


# ---------------------------------------------------------------------------
# Backtest command group
# ---------------------------------------------------------------------------

backtest_app = typer.Typer(
    name="backtest",
    help="回测命令组：触发策略回测任务。",
    no_args_is_help=True,
)
app.add_typer(backtest_app, name="backtest")


@backtest_app.command("run")
def backtest_run(
    strategy: int = typer.Option(..., "--strategy", "-S", help="策略 ID"),
    start: Optional[str] = typer.Option(None, "--start", help="回测开始日期 (YYYY-MM-DD)"),
    end: Optional[str] = typer.Option(None, "--end", help="回测结束日期 (YYYY-MM-DD)"),
    capital: float = typer.Option(100000.0, "--capital", "-c", help="初始资金"),
    sync: bool = typer.Option(False, "--sync", "-s", help="同步等待任务完成"),
) -> None:
    """发起策略回测任务。

    先在数据库中创建 backtest_run 记录，然后提交 Celery 任务执行回测。
    """
    from datetime import date, timedelta

    from app.tasks.backtest import run_backtest

    # Parse dates
    start_date = date.fromisoformat(start) if start else date.today() - timedelta(days=365 * 3)
    end_date = date.fromisoformat(end) if end else date.today()

    console.print(
        f"[bold]发起回测:[/bold] strategy_id={strategy}, "
        f"期间={start_date}~{end_date}, 初始资金={capital:,.0f}"
    )

    # Create backtest_run record in DB
    run_id = _create_backtest_run(strategy, start_date, end_date, capital)
    if run_id is None:
        console.print("[red]✗ 创建回测记录失败[/red]")
        raise typer.Exit(code=1)

    console.print(f"  回测记录 run_id={run_id}")

    # Dispatch Celery task
    result = run_backtest.delay(run_id=run_id)
    console.print(f"[green]任务已提交[/green] task_id={result.id}")

    if sync:
        _wait_for_task(result, "run_backtest")


def _create_backtest_run(
    strategy_id: int,
    start_date,
    end_date,
    initial_capital: float,
) -> Optional[int]:
    """Create a backtest_run record in the database and return its ID."""
    try:
        from decimal import Decimal

        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from app.core.config import get_settings
        from app.data.models.backtests import BacktestRun

        settings = get_settings()
        engine = create_engine(settings.database_sync_url)

        with Session(engine) as session:
            run = BacktestRun(
                strategy_id=strategy_id,
                start_date=start_date,
                end_date=end_date,
                initial_capital=Decimal(str(initial_capital)),
                status="pending",
                progress=0,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id

        engine.dispose()
        return run_id
    except Exception as e:
        console.print(f"[red]数据库错误: {e}[/red]")
        return None


# ---------------------------------------------------------------------------
# Signal command group
# ---------------------------------------------------------------------------

signal_app = typer.Typer(
    name="signal",
    help="信号生成命令组：触发策略信号生成任务。",
    no_args_is_help=True,
)
app.add_typer(signal_app, name="signal")


@signal_app.command("generate")
def signal_generate(
    strategy: Optional[int] = typer.Option(
        None, "--strategy", "-S", help="策略 ID（不指定则为所有策略生成信号）"
    ),
    sync: bool = typer.Option(False, "--sync", "-s", help="同步等待任务完成"),
) -> None:
    """触发策略信号生成。

    不指定 --strategy 时为所有活跃策略生成信号。
    """
    from app.tasks.signals import generate_strategy_signals

    if strategy:
        console.print(f"[bold]触发信号生成:[/bold] strategy_id={strategy}")
    else:
        console.print("[bold]触发信号生成:[/bold] 所有活跃策略")

    # generate_strategy_signals currently processes all strategies;
    # if a specific strategy is requested, we still dispatch the task
    # (future enhancement: pass strategy_id filter to the task)
    result = generate_strategy_signals.delay()
    console.print(f"[green]任务已提交[/green] task_id={result.id}")

    if sync:
        _wait_for_task(result, "generate_strategy_signals")


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------


@app.command("version")
def version() -> None:
    """打印后端版本号。"""
    console.print(f"Fund Quant Platform v{__version__}")


@app.command("status")
def status() -> None:
    """检查 Celery worker 和 Redis 连接状态。"""
    from app.tasks.celery_app import celery_app

    console.print("[bold]检查服务状态...[/bold]")

    # Check Redis / Celery broker
    try:
        ping = celery_app.control.ping(timeout=3)
        if ping:
            console.print(f"[green]✓ Celery workers 在线:[/green] {len(ping)} 个")
            for worker_resp in ping:
                for name in worker_resp:
                    console.print(f"    {name}")
        else:
            console.print("[yellow]⚠ 未发现在线 Celery worker[/yellow]")
    except Exception as e:
        console.print(f"[red]✗ Celery 连接失败: {e}[/red]")


if __name__ == "__main__":  # pragma: no cover
    app()
