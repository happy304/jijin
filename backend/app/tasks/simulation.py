"""Celery task for running Monte Carlo simulations asynchronously.

The task:
1. Updates simulation_runs status to 'running'
2. Publishes progress to Redis
3. Loads historical NAV data and computes returns
4. Runs the Monte Carlo engine
5. Persists results to the database
6. Updates status to 'done' or 'failed'

Progress is published to Redis as JSON so the WebSocket endpoint can
push real-time updates to connected clients.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

log = get_logger(__name__)

# Redis key pattern for simulation progress
PROGRESS_KEY_PREFIX = "simulation:progress:"
PROGRESS_CHANNEL_PREFIX = "simulation:channel:"


@celery_app.task(
    name="app.tasks.simulation.run_simulation",
    queue="backtest",
    bind=True,
    max_retries=0,
    time_limit=30 * 60,  # 30 min hard limit
    soft_time_limit=25 * 60,  # 25 min soft limit
)
def run_simulation(self, run_id: int) -> dict:
    """Execute a Monte Carlo simulation asynchronously.

    This task is dispatched by the POST /api/v1/simulations endpoint.
    Progress updates are written to Redis for WebSocket consumption.

    Args:
        run_id: The simulation_runs.id to execute.

    Returns:
        Dict with status and summary metrics.
    """
    from app.tasks.async_utils import run_async

    return run_async(_run_simulation_async(run_id))


async def _run_simulation_async(run_id: int) -> dict:
    """Async implementation of the simulation execution."""
    import redis
    import numpy as np
    import pandas as pd

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from app.core.config import get_settings
    from app.data.models.simulations import SimulationRun
    from app.data.session import create_async_engine_from_settings

    settings = get_settings()
    redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)

    progress_key = f"{PROGRESS_KEY_PREFIX}{run_id}"
    channel_key = f"{PROGRESS_CHANNEL_PREFIX}{run_id}"
    loop = asyncio.get_running_loop()
    last_progress_db: float | None = None
    progress_tasks: list[asyncio.Task[None]] = []

    async def persist_progress_to_db(progress: float) -> None:
        """Persist key progress milestones to the database for fallback reads."""
        nonlocal last_progress_db
        rounded = round(progress, 2)
        if last_progress_db == rounded:
            return

        update_session = session_factory()
        try:
            result = await update_session.execute(
                select(SimulationRun).where(SimulationRun.id == run_id)
            )
            live_run = result.scalar_one_or_none()
            if live_run is None:
                return
            live_run.status = "running"
            live_run.progress = rounded
            await update_session.commit()
            last_progress_db = rounded
        except Exception as db_err:
            await update_session.rollback()
            log.warning(
                "simulation.progress_persist_failed",
                run_id=run_id,
                progress=rounded,
                error=str(db_err),
            )
        finally:
            await update_session.close()

    def publish_progress(progress: float, message: str = "") -> None:
        """Write progress to Redis and publish notification."""
        rounded = round(progress, 2)
        payload = json.dumps({
            "run_id": run_id,
            "progress": rounded,
            "message": message,
            "status": "running",
        })
        redis_client.set(progress_key, payload, ex=3600)
        redis_client.publish(channel_key, payload)
        progress_tasks.append(loop.create_task(persist_progress_to_db(rounded)))

    # Create a fresh engine bound to the CURRENT event loop to avoid
    # "Future attached to a different loop" errors in Celery workers.
    db_engine = create_async_engine_from_settings(settings)
    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async with session_factory() as session:
        from sqlalchemy import select, and_

        # Load the simulation run
        result = await session.execute(
            select(SimulationRun).where(SimulationRun.id == run_id)
        )
        run = result.scalar_one_or_none()

        if run is None:
            log.error("simulation.not_found", run_id=run_id)
            return {"status": "failed", "error": f"Run {run_id} not found"}

        # Update status to running
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        run.progress = 0
        await session.commit()

        publish_progress(0, "模拟开始")

        try:
            # Load strategy configuration
            from app.data.models.strategies import Strategy

            strategy_row = None
            strategy_snapshot = run.strategy_snapshot if isinstance(run.strategy_snapshot, dict) else None
            if run.strategy_id:
                strat_result = await session.execute(
                    select(Strategy).where(Strategy.id == run.strategy_id)
                )
                strategy_row = strat_result.scalar_one_or_none()

            if strategy_snapshot is None and strategy_row is None:
                raise ValueError(f"策略 {run.strategy_id} 不存在")

            publish_progress(5, "加载策略配置")

            strategy_type = None
            strategy_params: dict = {}
            universe_data = None
            strategy_name = None

            if strategy_snapshot is not None:
                strategy_type = strategy_snapshot.get("strategy_type")
                strategy_params = strategy_snapshot.get("params") or {}
                universe_data = strategy_snapshot.get("universe")
                strategy_name = strategy_snapshot.get("name")

            if strategy_row is not None:
                strategy_type = strategy_type or strategy_row.strategy_type
                strategy_params = strategy_params or (strategy_row.params or {})
                universe_data = universe_data or strategy_row.universe
                strategy_name = strategy_name or strategy_row.name

            # Extract universe fund codes
            universe_codes = universe_data
            if isinstance(universe_codes, dict):
                universe_codes = universe_codes.get("fund_codes", [])

            if not universe_codes:
                raise ValueError("策略基金池为空")

            publish_progress(10, "加载历史净值数据")

            # Load historical NAV data for parameter estimation
            from app.data.models.fund_nav import FundNav
            from datetime import timedelta, date as date_type
            from decimal import Decimal

            lookback_days = run.lookback_days or 504
            # 使用数据库中实际最新数据日期，而非 date.today()
            # 原因：NAV 数据通常有 1-2 天延迟，用 today() 可能导致
            # 最近几天的数据缺失被忽略
            from sqlalchemy import func as sa_func
            latest_date_result = await session.execute(
                select(sa_func.max(FundNav.trade_date)).where(
                    FundNav.fund_code.in_(universe_codes)
                )
            )
            latest_data_date = latest_date_result.scalar_one_or_none()
            end_date = latest_data_date if latest_data_date else date_type.today()
            # Estimate start date for lookback (add buffer for non-trading days)
            start_date = end_date - timedelta(days=int(lookback_days * 1.5))

            # Load NAV data for all funds in universe
            from app.tasks.nav_quality import (
                build_nav_quality_warning,
                new_nav_source_stats,
                record_nav_source_usage,
            )

            nav_data: dict[str, pd.Series] = {}
            quality_nav_data: dict[str, dict[date_type, Decimal]] = {}
            nav_source_stats: dict[str, dict] = {}
            for code in universe_codes:
                nav_stmt = (
                    select(FundNav.trade_date, FundNav.adj_nav, FundNav.unit_nav)
                    .where(
                        and_(
                            FundNav.fund_code == code,
                            FundNav.trade_date >= start_date,
                            FundNav.trade_date <= end_date,
                        )
                    )
                    .order_by(FundNav.trade_date)
                )
                nav_result = await session.execute(nav_stmt)
                nav_rows = nav_result.all()

                fund_quality_nav: dict[date_type, Decimal] = {}
                if nav_rows:
                    valid_points = []
                    fund_stats = new_nav_source_stats()
                    for row in nav_rows:
                        if row.adj_nav is not None:
                            valid_points.append((row.trade_date, float(row.adj_nav)))
                            fund_quality_nav[row.trade_date] = row.adj_nav
                            record_nav_source_usage(
                                fund_stats,
                                row.trade_date,
                                used_adj_nav=True,
                            )
                        elif row.unit_nav is not None:
                            valid_points.append((row.trade_date, float(row.unit_nav)))
                            fund_quality_nav[row.trade_date] = row.unit_nav
                            record_nav_source_usage(
                                fund_stats,
                                row.trade_date,
                                used_adj_nav=False,
                            )
                    nav_source_stats[code] = fund_stats
                    if len(valid_points) >= 30:
                        dates = [trade_date for trade_date, _ in valid_points]
                        navs = [nav for _, nav in valid_points]
                        nav_series = pd.Series(
                            navs,
                            index=pd.DatetimeIndex(dates),
                        )
                        nav_data[code] = nav_series
                quality_nav_data[code] = fund_quality_nav

            if not nav_data:
                raise ValueError(
                    f"基金池 {universe_codes} 无足够的历史净值数据（至少需要 30 个交易日）"
                )

            from app.data.models.funds import Fund

            fund_type_result = await session.execute(
                select(Fund.code, Fund.fund_type).where(Fund.code.in_(universe_codes))
            )
            fund_types_by_code = {
                row.code: row.fund_type for row in fund_type_result.all() if getattr(row, "code", None)
            }
            quality_report = _build_simulation_data_quality_report(
                quality_nav_data,
                start_date,
                end_date,
                fund_types_by_code,
            )
            if quality_report.warnings:
                log.warning(
                    "simulation.data_quality_warnings",
                    run_id=run_id,
                    warnings=quality_report.warnings,
                )

            publish_progress(20, f"已加载 {len(nav_data)} 只基金的历史数据")

            # Compute daily returns
            returns_dict: dict[str, pd.Series] = {}
            for code, nav_series in nav_data.items():
                daily_returns = nav_series.pct_change().dropna()
                if len(daily_returns) >= 30:
                    # Limit to lookback_days most recent
                    returns_dict[code] = daily_returns.iloc[-lookback_days:]

            if not returns_dict:
                raise ValueError("计算收益率后无有效数据")

            publish_progress(30, "收益率计算完成")

            # Build returns DataFrame and weights
            if len(returns_dict) == 1:
                # Single fund
                code = list(returns_dict.keys())[0]
                historical_returns = returns_dict[code]
                weights = None
            else:
                # Multi-fund: align dates and build DataFrame
                returns_df = pd.DataFrame(returns_dict)
                # Drop rows with any NaN (align to common dates)
                returns_df = returns_df.dropna()

                if len(returns_df) < 30:
                    raise ValueError(
                        f"对齐后的共同交易日数据不足（{len(returns_df)} 天），"
                        "请确保基金池中的基金有足够的重叠历史数据"
                    )

                historical_returns = returns_df

                # Determine weights from strategy params
                weights = _extract_weights(strategy_params, list(returns_df.columns))

            publish_progress(35, "初始化 Monte Carlo 引擎")

            # Configure and run simulation
            from app.domain.simulation.monte_carlo import (
                MonteCarloEngine,
                SimulationConfig,
            )
            from app.domain.simulation.strategy_simulation import (
                StrategySimulationEngine,
                StrategySimConfig,
            )

            config = SimulationConfig(
                horizon_days=run.horizon_days,
                num_simulations=run.num_simulations,
                method=run.method or "gbm",
                confidence_levels=run.confidence_levels or [0.95, 0.99],
                initial_capital=float(run.initial_capital or 100000),
                target_return=float(run.target_return) if run.target_return else None,
            )

            def task_progress_callback(pct: float, msg: str) -> None:
                # Map engine progress (10-95) to task progress (35-85)
                mapped_pct = 35 + (pct / 100) * 50
                publish_progress(mapped_pct, msg)

            # Use strategy-aware simulation if strategy type is known
            if strategy_type and strategy_type in (
                "dca", "momentum", "risk_parity", "mean_variance", "timing", "fof"
            ):
                strategy_sim_config = StrategySimConfig(
                    strategy_type=strategy_type,
                    params=strategy_params,
                    universe_codes=universe_codes,
                )
                sim_engine = StrategySimulationEngine(config, strategy_sim_config)
            else:
                # Fallback to base Monte Carlo (buy-and-hold)
                sim_engine = MonteCarloEngine(config)

            sim_paths = sim_engine.simulate_paths(
                historical_returns=historical_returns,
                weights=weights,
                progress_callback=task_progress_callback,
            )
            sim_result = sim_engine._compute_results(sim_paths)

            publish_progress(85, "计算扩展风险指标")

            # Compute extended risk metrics from the same simulated paths
            from app.domain.simulation.risk_metrics import compute_extended_metrics

            extended_metrics = compute_extended_metrics(sim_paths)

            publish_progress(90, "保存模拟结果")

            from app.domain.performance.metrics import METRIC_VERSION

            # Build final metrics dict
            metrics_dict = sim_result.to_dict()
            metrics_dict["metric_version"] = METRIC_VERSION
            metrics_dict["data_quality_report"] = quality_report.to_dict()
            metrics_dict["extended"] = extended_metrics.to_dict()
            nav_quality_warning = build_nav_quality_warning(nav_source_stats)
            if nav_quality_warning is not None:
                metrics_dict["nav_quality_warning"] = nav_quality_warning
            metrics_dict["funds_used"] = list(returns_dict.keys())
            metrics_dict["data_points"] = (
                len(historical_returns)
                if isinstance(historical_returns, pd.Series)
                else len(historical_returns)
            )
            metrics_dict["strategy_type"] = strategy_type or "buy_and_hold"
            metrics_dict["strategy_name"] = strategy_name
            metrics_dict["strategy_params"] = strategy_params

            # Update run with results
            run.status = "done"
            run.progress = 100
            run.metrics = {k: v for k, v in metrics_dict.items() if k != "percentile_paths"}
            run.percentile_paths = metrics_dict.get("percentile_paths")
            run.finished_at = datetime.now(timezone.utc)
            run.error_msg = None
            await session.commit()
            if progress_tasks:
                await asyncio.gather(*progress_tasks, return_exceptions=True)
                progress_tasks.clear()
            last_progress_db = 100.0

            # Publish completion
            completion_payload = json.dumps({
                "run_id": run_id,
                "progress": 100,
                "message": "模拟完成",
                "status": "done",
            })
            redis_client.set(progress_key, completion_payload, ex=3600)
            redis_client.publish(channel_key, completion_payload)

            log.info("simulation.completed", run_id=run_id)
            return {"status": "done", "run_id": run_id, "metrics": run.metrics}

        except Exception as e:
            if progress_tasks:
                await asyncio.gather(*progress_tasks, return_exceptions=True)
                progress_tasks.clear()
            # Mark as failed
            await session.rollback()
            run.status = "failed"
            run.error_msg = str(e)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            await session.commit()
            last_progress_db = float(run.progress or 0)

            # Publish failure
            failure_payload = json.dumps({
                "run_id": run_id,
                "progress": float(run.progress or 0),
                "message": f"模拟失败: {str(e)[:200]}",
                "status": "failed",
            })
            redis_client.set(progress_key, failure_payload, ex=3600)
            redis_client.publish(channel_key, failure_payload)

            log.error(
                "simulation.failed",
                run_id=run_id,
                error=str(e),
                traceback=traceback.format_exc(),
            )
            return {"status": "failed", "run_id": run_id, "error": str(e)}
        finally:
            redis_client.close()
            await db_engine.dispose()


def _build_simulation_data_quality_report(
    nav_data: dict,
    start_date,
    end_date,
    fund_types: dict[str, str | None] | None = None,
):
    """Build an auditable NAV quality report for a simulation lookback window."""
    from app.domain.backtest.calendar import trading_days as get_trading_days
    from app.domain.backtest.data_quality import check_backtest_data_quality

    return check_backtest_data_quality(
        nav_data,
        get_trading_days(start_date, end_date),
        fund_types=fund_types,
    )


def _extract_weights(strategy_params: dict | None, fund_codes: list[str]) -> np.ndarray | None:
    """Extract portfolio weights from strategy configuration.

    Supports:
    - Equal weight (default)
    - Explicit weights in params
    - Strategy-type specific weight logic
    """
    import numpy as np

    params = strategy_params or {}
    n = len(fund_codes)

    # Check for explicit weights in params
    if "weights" in params:
        weights_config = params["weights"]
        if isinstance(weights_config, dict):
            # Map fund_code -> weight
            weights = np.array([
                weights_config.get(code, 1.0 / n) for code in fund_codes
            ])
            return weights / weights.sum()
        elif isinstance(weights_config, list) and len(weights_config) == n:
            weights = np.array(weights_config, dtype=np.float64)
            return weights / weights.sum()

    # Default: equal weight
    return np.ones(n) / n
