"""Celery task for running backtests asynchronously.

The task:
1. Updates backtest_runs status to 'running'
2. Publishes progress to Redis (key: backtest:progress:{run_id})
3. Runs the event-driven backtest engine
4. Persists results (equity curve, trades, metrics) to the database
5. Updates status to 'done' or 'failed'

Progress is published to Redis as JSON so the WebSocket endpoint can
push real-time updates to connected clients.

Requirements: 7.3, 7.4
"""

from __future__ import annotations

import asyncio
import json
import traceback
from collections import Counter
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

log = get_logger(__name__)

# Redis key pattern for backtest progress
PROGRESS_KEY_PREFIX = "backtest:progress:"
# Redis channel pattern for pub/sub progress notifications
PROGRESS_CHANNEL_PREFIX = "backtest:channel:"


@celery_app.task(
    name="app.tasks.backtest.run_backtest",
    queue="backtest",
    bind=True,
    max_retries=0,
    time_limit=60 * 60,  # 1 hour hard limit
    soft_time_limit=55 * 60,  # 55 min soft limit
)
def run_backtest(self, run_id: int) -> dict:
    """Execute a backtest run asynchronously.

    This task is dispatched by the POST /api/v1/backtests endpoint.
    Progress updates are written to Redis for WebSocket consumption.

    Args:
        run_id: The backtest_runs.id to execute.

    Returns:
        Dict with status and summary metrics.
    """
    from app.tasks.async_utils import run_async

    return run_async(_run_backtest_async(run_id))


async def _run_backtest_async(run_id: int) -> dict:
    """Async implementation of the backtest execution.

    Separated from the sync Celery task wrapper to allow async DB access.
    """
    import redis

    from app.core.config import get_settings
    from app.data.models.backtests import BacktestEquity, BacktestRun, BacktestTrade
    from app.data.session import get_engine, get_sessionmaker

    settings = get_settings()
    redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)

    progress_key = f"{PROGRESS_KEY_PREFIX}{run_id}"
    channel_key = f"{PROGRESS_CHANNEL_PREFIX}{run_id}"
    loop = asyncio.get_running_loop()
    last_persisted_progress: float | None = None
    progress_tasks: list[asyncio.Task[None]] = []

    async def persist_progress_to_db(progress: float) -> None:
        """Persist coarse progress milestones for polling fallback."""
        nonlocal last_persisted_progress
        rounded = round(progress, 2)
        if last_persisted_progress == rounded:
            return

        update_session = session_factory()
        try:
            result = await update_session.execute(
                select(BacktestRun).where(BacktestRun.id == run_id)
            )
            live_run = result.scalar_one_or_none()
            if live_run is None:
                return
            if live_run.status not in {"done", "failed"}:
                current_progress = float(live_run.progress) if live_run.progress is not None else 0.0
                if rounded >= current_progress:
                    live_run.status = "running"
                    live_run.progress = rounded
                    await update_session.commit()
                    last_persisted_progress = rounded
        except Exception as db_err:
            await update_session.rollback()
            log.warning(
                "backtest.progress_persist_failed",
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
        redis_client.set(progress_key, payload, ex=3600)  # 1h TTL
        redis_client.publish(channel_key, payload)
        progress_tasks.append(loop.create_task(persist_progress_to_db(rounded)))

    # Ensure engine is initialized
    get_engine()
    session_factory = get_sessionmaker()

    async with session_factory() as session:
        from sqlalchemy import select

        # Load the backtest run
        result = await session.execute(
            select(BacktestRun).where(BacktestRun.id == run_id)
        )
        run = result.scalar_one_or_none()

        if run is None:
            log.error("backtest.not_found", run_id=run_id)
            return {"status": "failed", "error": f"Run {run_id} not found"}

        # Update status to running
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        run.progress = 0
        await session.commit()

        publish_progress(0, "回测开始")

        try:
            # Load strategy configuration
            from app.data.models.strategies import Strategy

            strategy_row = None
            if run.strategy_id:
                strat_result = await session.execute(
                    select(Strategy).where(Strategy.id == run.strategy_id)
                )
                strategy_row = strat_result.scalar_one_or_none()

            if strategy_row is None:
                raise ValueError(f"Strategy {run.strategy_id} not found")

            publish_progress(5, "加载策略配置")

            # Build and run the backtest engine
            from app.domain.backtest.result import BacktestResult

            # For now, simulate a simple backtest execution with progress
            # In production, this would invoke the full EventDrivenEngine
            # with the strategy configuration from the database.
            publish_progress(10, "初始化回测引擎")

            # Import engine components
            from decimal import Decimal

            from app.domain.backtest.engine_event import (
                BacktestResult as EngineResult,
                DividendInfo,
                EquitySnapshot,
                EventDrivenEngine,
                FundMeta,
            )
            from app.domain.backtest.fees import FeeTier

            # Create a progress callback for the engine
            total_days_estimate = 252  # rough estimate

            def engine_progress_callback(current_day: int, total_days: int, trade_date=None) -> None:
                """Called by the engine on each trading day."""
                pct = 10 + (current_day / max(total_days, 1)) * 80
                publish_progress(pct, f"回测进行中 ({current_day}/{total_days})")

            publish_progress(15, "运行回测引擎")

            # Execute the backtest using the engine
            # Note: The actual engine integration depends on strategy type
            # and available data. For the API layer, we handle the result
            # persistence regardless of how the engine is invoked.
            engine = EventDrivenEngine()

            # Build strategy instance from config
            from app.domain.strategy.base import create_strategy_from_config

            strategy_instance = create_strategy_from_config(
                strategy_type=strategy_row.strategy_type,
                params=strategy_row.params,
                universe=strategy_row.universe,
            )

            # Extract universe fund codes
            universe_codes = strategy_row.universe
            if isinstance(universe_codes, dict):
                universe_codes = universe_codes.get("fund_codes", [])

            # Load NAV data from database for the universe
            from app.data.models.fund_nav import FundNav
            from sqlalchemy import and_

            from app.tasks.nav_quality import (
                build_nav_quality_warning,
                new_nav_source_stats,
                record_nav_source_usage,
            )

            nav_data: dict[str, dict] = {}
            nav_source_stats: dict[str, dict] = {}
            for code in universe_codes:
                nav_stmt = select(FundNav).where(
                    and_(
                        FundNav.fund_code == code,
                        FundNav.trade_date >= run.start_date,
                        FundNav.trade_date <= run.end_date,
                    )
                ).order_by(FundNav.trade_date)
                nav_result = await session.execute(nav_stmt)
                nav_rows = nav_result.scalars().all()
                fund_nav: dict = {}
                fund_stats = new_nav_source_stats()
                for row in nav_rows:
                    if row.adj_nav is not None:
                        fund_nav[row.trade_date] = row.adj_nav
                        record_nav_source_usage(
                            fund_stats,
                            row.trade_date,
                            used_adj_nav=True,
                        )
                    elif row.unit_nav is not None:
                        fund_nav[row.trade_date] = row.unit_nav
                        record_nav_source_usage(
                            fund_stats,
                            row.trade_date,
                            used_adj_nav=False,
                        )
                nav_data[code] = fund_nav
                nav_source_stats[code] = fund_stats

            if not any(nav_data.values()):
                raise ValueError(
                    f"基金池 {universe_codes} 在 {run.start_date} ~ {run.end_date} 期间无净值数据。"
                    "请先运行数据采集。"
                )

            # Validate: check fund inception dates vs backtest start date
            from app.data.models.funds import Fund

            fund_inception_result = await session.execute(
                select(Fund.code, Fund.name, Fund.inception_date, Fund.fund_type).where(
                    Fund.code.in_(universe_codes)
                )
            )
            fund_inception_rows = fund_inception_result.all()
            fund_types_by_code = {
                row.code: row.fund_type for row in fund_inception_rows if getattr(row, "code", None)
            }
            inception_violations = []
            for row in fund_inception_rows:
                effective_start = row.inception_date

                if effective_start is None:
                    # 用该基金 NAV 数据中最早日期作为替代
                    from sqlalchemy import func as sa_func
                    earliest_result = await session.execute(
                        select(sa_func.min(FundNav.trade_date)).where(
                            FundNav.fund_code == row.code
                        )
                    )
                    earliest_date = earliest_result.scalar_one_or_none()
                    if earliest_date is not None:
                        effective_start = earliest_date

                if effective_start and effective_start > run.start_date:
                    inception_violations.append(
                        f"{row.code}({row.name}) 最早数据始于 {effective_start}"
                    )

            if inception_violations:
                raise ValueError(
                    f"回测起始日期 {run.start_date} 早于以下基金的可用数据起始日期：{'；'.join(inception_violations)}。"
                    f"请将起始日期调整为不早于所有基金的数据起始日期。"
                )

            publish_progress(20, "净值数据加载完成，检查数据质量")

            # Data quality check before running backtest
            from app.domain.backtest.data_quality import check_backtest_data_quality
            from app.domain.backtest.calendar import trading_days as get_trading_days

            trade_days_list = get_trading_days(run.start_date, run.end_date)
            quality_report = check_backtest_data_quality(
                nav_data,
                trade_days_list,
                fund_types=fund_types_by_code,
            )

            if not quality_report.can_proceed:
                raise ValueError(
                    f"数据质量检查未通过：{'；'.join(quality_report.warnings)}"
                )

            # 将数据质量警告记录到 metrics 中
            if quality_report.warnings:
                log.warning(
                    "backtest.data_quality_warnings",
                    run_id=run_id,
                    warnings=quality_report.warnings,
                )

            publish_progress(25, "数据质量检查通过，开始回测")

            # 构建 FundMeta / DividendInfo，把真实交易约束接入引擎。
            fund_meta_dict: dict[str, FundMeta] = {}
            dividends_list: list[DividendInfo] = []
            fund_rows = []
            pit_sources: dict[str, str] = {}
            pit_warnings: list[str] = []
            try:
                from app.data.models.fund_dividends import FundDividend
                from app.data.models.fund_fees import FundFee

                from app.data.services.fund_pit import get_fund_meta_at_batch

                pit_meta = await get_fund_meta_at_batch(
                    session,
                    universe_codes,
                    run.start_date,
                    allow_live_fallback=False,
                )
                pit_sources = {code: meta.source for code, meta in pit_meta.items()}
                missing_pit_codes = [code for code, source in pit_sources.items() if source == "missing"]
                if missing_pit_codes:
                    pit_warnings.append(
                        "严格 PIT 元数据缺失：" + "、".join(sorted(missing_pit_codes))
                    )

                meta_result = await session.execute(
                    select(
                        Fund.code,
                        Fund.fund_type,
                        Fund.delisting_date,
                        Fund.is_purchasable,
                        Fund.purchase_limit,
                    ).where(Fund.code.in_(universe_codes))
                )
                fund_rows = meta_result.all()

                fee_result = await session.execute(
                    select(FundFee)
                    .where(FundFee.fund_code.in_(universe_codes))
                    .order_by(
                        FundFee.fund_code,
                        FundFee.fee_type,
                        FundFee.min_amount,
                        FundFee.min_holding_days,
                    )
                )
                fee_rows = fee_result.scalars().all()
                fee_tiers_by_code: dict[str, dict[str, list[FeeTier]]] = {}
                for fee_row in fee_rows:
                    fee_bucket = fee_tiers_by_code.setdefault(
                        fee_row.fund_code,
                        {"subscribe": [], "redeem": []},
                    )
                    tier = FeeTier(
                        min_amount=fee_row.min_amount,
                        max_amount=fee_row.max_amount,
                        min_holding_days=fee_row.min_holding_days,
                        max_holding_days=fee_row.max_holding_days,
                        rate=fee_row.rate,
                    )
                    if fee_row.fee_type == "subscribe":
                        fee_bucket["subscribe"].append(tier)
                    elif fee_row.fee_type == "redeem":
                        fee_bucket["redeem"].append(tier)

                dividend_result = await session.execute(
                    select(FundDividend)
                    .where(
                        and_(
                            FundDividend.fund_code.in_(universe_codes),
                            FundDividend.ex_date >= run.start_date,
                            FundDividend.ex_date <= run.end_date,
                        )
                    )
                    .order_by(FundDividend.fund_code, FundDividend.ex_date)
                )
                dividend_rows = dividend_result.scalars().all()
                dividends_list = [
                    DividendInfo(
                        fund_code=row.fund_code,
                        ex_date=row.ex_date,
                        dividend_per_share=row.dividend_per_share,
                        split_ratio=row.split_ratio,
                        reinvest=True,
                    )
                    for row in dividend_rows
                ]

                for row in fund_rows:
                    fee_bucket = fee_tiers_by_code.get(row.code, {})
                    pit = pit_meta.get(row.code)
                    is_purchasable = (
                        bool(pit.is_purchasable)
                        if pit is not None and pit.is_purchasable is not None and pit.source == "history"
                        else bool(row.is_purchasable)
                    )
                    purchase_limit = (
                        pit.purchase_limit
                        if pit is not None and pit.purchase_limit is not None and pit.source == "history"
                        else row.purchase_limit
                    )
                    status = pit.status if pit is not None and pit.source == "history" else None
                    if status is not None and status != "active":
                        is_purchasable = False
                    fund_meta_dict[row.code] = FundMeta(
                        code=row.code,
                        fund_type=row.fund_type or "stock",
                        subscribe_fee_tiers=list(fee_bucket.get("subscribe", [])),
                        redeem_fee_tiers=list(fee_bucket.get("redeem", [])),
                        is_purchasable=is_purchasable,
                        purchase_limit=purchase_limit,
                        delisting_date=row.delisting_date,
                    )
            except Exception as meta_err:
                # 元数据缺失不阻塞回测，引擎会按缺省值处理
                log.warning(
                    "backtest.fund_meta_load_failed",
                    run_id=run_id,
                    error=str(meta_err),
                )

            engine_result = engine.run(
                start=run.start_date,
                end=run.end_date,
                strategy=strategy_instance,
                nav_data=nav_data,
                initial_capital=run.initial_capital or Decimal("100000"),
                fund_meta=fund_meta_dict if fund_meta_dict else None,
                dividends=dividends_list if dividends_list else None,
                progress_callback=engine_progress_callback,
            )

            publish_progress(90, "构建回测结果指标")

            # Build enhanced result with metrics
            bt_result = BacktestResult.from_engine_result(engine_result)

            publish_progress(92, "清理旧回测结果")

            # 清理旧数据（防止重试时主键冲突）
            from sqlalchemy import delete as sql_delete
            await session.execute(
                sql_delete(BacktestEquity).where(BacktestEquity.run_id == run_id)
            )
            await session.execute(
                sql_delete(BacktestTrade).where(BacktestTrade.run_id == run_id)
            )

            publish_progress(94, "写入权益曲线")

            # Persist equity curve
            for snap in bt_result.equity_curve:
                equity_row = BacktestEquity(
                    run_id=run_id,
                    trade_date=snap.trade_date,
                    equity=snap.equity,
                    cash=snap.cash,
                    position_value=snap.position_value,
                    benchmark_value=None,
                )
                session.add(equity_row)

            publish_progress(96, "写入成交记录")

            # Persist trades
            for idx, trade in enumerate(bt_result.trades, start=1):
                trade_row = BacktestTrade(
                    run_id=run_id,
                    trade_id=idx,
                    order_date=trade.order_date,
                    confirm_date=trade.confirm_date,
                    fund_code=trade.fund_code,
                    direction=trade.direction,
                    amount=trade.amount,
                    shares=trade.shares,
                    nav=trade.nav,
                    fee=trade.fee,
                )
                session.add(trade_row)

            # 提交 equity/trades 数据，独立事务避免后续操作污染
            await session.commit()

            if progress_tasks:
                await asyncio.gather(*progress_tasks, return_exceptions=True)
                progress_tasks.clear()

            publish_progress(98, "写入最终回测指标")

            # Update run with final metrics + attribution
            run.status = "done"
            run.progress = 100
            from app.domain.backtest.result import BacktestQuality
            from app.domain.performance.metrics import METRIC_VERSION

            metrics_dict = bt_result.metrics.to_dict() if bt_result.metrics else {}
            metrics_dict["metric_version"] = METRIC_VERSION
            metrics_dict["data_quality_report"] = quality_report.to_dict()
            if not pit_sources:
                pit_data_quality = "missing"
            elif all(source == "history" for source in pit_sources.values()):
                pit_data_quality = "strict"
            elif any(source == "live_fallback" for source in pit_sources.values()):
                pit_data_quality = "fallback"
            else:
                pit_data_quality = "missing"
            survivorship_bias_control = (
                "partial"
                if all(getattr(row, "delisting_date", None) is not None for row in fund_rows) and fund_rows
                else "none"
            )
            quality_warnings = list(quality_report.warnings) + pit_warnings
            nav_quality_warning = build_nav_quality_warning(nav_source_stats)
            if nav_quality_warning is not None:
                quality_warnings.append("NAV 数据源口径或复权覆盖存在警告")
            backtest_quality = BacktestQuality(
                lookahead_guard=True,
                cash_arrival_delay_modelled=True,
                lot_level_fee_modelled=True,
                pit_data_quality=pit_data_quality,
                nav_publication_lag_modelled=True,
                survivorship_bias_control=survivorship_bias_control,
                vectorized_simplification=False,
                warnings=quality_warnings,
            )
            quality_payload = backtest_quality.to_dict()
            metrics_dict["quality"] = quality_payload
            metrics_dict["pit_data_quality"] = pit_data_quality
            metrics_dict["decision_grade"] = quality_payload["decision_grade"]
            metrics_dict["pit_sources"] = pit_sources
            nav_quality_warning = build_nav_quality_warning(nav_source_stats)
            if nav_quality_warning is not None:
                metrics_dict["nav_quality_warning"] = nav_quality_warning

            # Sharpe statistical inference (PSR / 95% CI). 没有多重检验信息，
            # 默认 n_trials=1 即仅做置信区间和"真实 Sharpe > 0"的概率推断；
            # 如需 DSR，调用方应通过 /backtests/{id}/inference?n_trials=N 重新计算。
            try:
                import numpy as np

                from app.domain.performance.sharpe_inference import sharpe_inference

                equities_for_inf = [float(s.equity) for s in bt_result.equity_curve]
                if len(equities_for_inf) >= 31:
                    daily_returns = [
                        (equities_for_inf[i] - equities_for_inf[i - 1])
                        / equities_for_inf[i - 1]
                        for i in range(1, len(equities_for_inf))
                        if equities_for_inf[i - 1] > 0
                    ]
                    if len(daily_returns) >= 30:
                        inf_result = sharpe_inference(
                            returns=np.asarray(daily_returns, dtype=float),
                            n_trials=1,
                            freq=252,
                        )
                        if inf_result is not None:
                            metrics_dict["sharpe_inference"] = inf_result.to_dict()
            except Exception as inf_err:
                log.warning(
                    "backtest.sharpe_inference_failed",
                    run_id=run_id,
                    error=str(inf_err),
                )

            # Compute attribution analysis (optional, failures won't block the backtest)
            try:
                import pandas as pd
                from app.services.performance_service import PerformanceService

                # Build NAV series from equity curve for attribution
                equity_dates = [snap.trade_date for snap in bt_result.equity_curve]
                equity_values = [float(snap.equity) for snap in bt_result.equity_curve]

                if len(equity_values) >= 2 and equity_values[0] > 0:
                    initial = equity_values[0]
                    nav_series = pd.Series(
                        [v / initial for v in equity_values],
                        index=pd.DatetimeIndex(equity_dates),
                    )

                    # Build Brinson data from trades and NAV data
                    # Calculate per-fund returns and approximate weights
                    # NOTE (修复): Brinson 归因要求 portfolio 和 benchmark 在**相同分组**
                    # 维度（行业/类型）上有不同的权重和不同的收益。如果直接把基金池当
                    # 作"分组"并把 portfolio_returns 当作 benchmark_returns，那么:
                    #   r_i - R_i = 0  → Selection 恒为 0
                    #   (w_p - w_b) × (r_p - r_b) = 0  → Interaction 恒为 0
                    # 整个 Brinson 退化成纯 Allocation 噪声，结果毫无意义。
                    #
                    # 当前实现没有：
                    #   1) 基金的行业/类型分类映射
                    #   2) 同一行业/类型在基准指数中的真实收益
                    # 因此暂不在此处计算 Brinson，等行业分类与基准行业收益数据接入后再启用。
                    # 留下结构占位以便后续替换。
                    brinson_input = None
                    # Brinson 归因暂未启用 — 详见上方注释
                    brinson_input = None

                    perf_service = PerformanceService()
                    perf_report = perf_service.analyze(
                        nav=nav_series,
                        brinson_data=brinson_input,
                    )

                    # Merge attribution into metrics
                    attribution_dict = perf_report.attribution.to_dict()
                    if attribution_dict.get("fama_french") is not None:
                        ff = attribution_dict["fama_french"]
                        metrics_dict["fama_french"] = {
                            "alpha": ff.get("alpha", 0),
                            "beta_mkt": ff.get("betas", {}).get("MKT", 0),
                            "beta_smb": ff.get("betas", {}).get("SMB", 0),
                            "beta_hml": ff.get("betas", {}).get("HML", 0),
                            "beta_rmw": ff.get("betas", {}).get("RMW"),
                            "beta_cma": ff.get("betas", {}).get("CMA"),
                            "r_squared": ff.get("r_squared", 0),
                        }
                    if attribution_dict.get("brinson") is not None:
                        br = attribution_dict["brinson"]
                        metrics_dict["brinson"] = {
                            "allocation_effect": br.get("allocation_effect", {}).get("total", 0),
                            "selection_effect": br.get("selection_effect", {}).get("total", 0),
                            "interaction_effect": br.get("interaction_effect", {}).get("total", 0),
                            "total_excess": br.get("total_excess_return", 0),
                        }

            except Exception as attr_err:
                log.warning(
                    "backtest.attribution_failed",
                    run_id=run_id,
                    error=str(attr_err),
                )

            # Compute benchmark relative metrics (optional, failures won't block the backtest)
            try:
                from app.data.models.benchmark import BenchmarkNav
                from app.domain.backtest.result import compute_benchmark_metrics

                def _default_benchmark_code() -> str | None:
                    fund_types = [
                        (row.fund_type or "").lower()
                        for row in fund_rows
                        if getattr(row, "fund_type", None)
                    ]
                    if not fund_types:
                        return "000300"
                    dominant_type = Counter(fund_types).most_common(1)[0][0]
                    mapping = {
                        "stock": "000300",
                        "index": "000300",
                        "mixed": "000300",
                        "bond": "H11001",
                        "money": "000012",
                    }
                    return mapping.get(dominant_type)

                # 确定基准代码：优先策略显式设置，否则按基金类型保守映射。
                benchmark_code = strategy_row.benchmark or _default_benchmark_code()

                if not benchmark_code:
                    raise ValueError("当前策略基金类型缺少可用默认基准，跳过基准相对指标计算")

                # 加载基准日收益率
                benchmark_stmt = select(BenchmarkNav).where(
                    and_(
                        BenchmarkNav.index_code == benchmark_code,
                        BenchmarkNav.trade_date >= run.start_date,
                        BenchmarkNav.trade_date <= run.end_date,
                    )
                ).order_by(BenchmarkNav.trade_date)
                bm_result = await session.execute(benchmark_stmt)
                bm_rows = bm_result.scalars().all()

                if bm_rows and len(bt_result.equity_curve) >= 2:
                    # 构建基准日收益率字典 {date: return}
                    bm_returns_map = {
                        row.trade_date: float(row.daily_return)
                        for row in bm_rows
                        if row.daily_return is not None
                    }

                    # 构建组合日收益率（与基准日期对齐）
                    equity_map = {
                        snap.trade_date: float(snap.equity)
                        for snap in bt_result.equity_curve
                    }
                    equity_dates_sorted = sorted(equity_map.keys())

                    portfolio_returns_aligned: list[float] = []
                    benchmark_returns_aligned: list[float] = []

                    for i in range(1, len(equity_dates_sorted)):
                        d = equity_dates_sorted[i]
                        prev_d = equity_dates_sorted[i - 1]
                        prev_eq = equity_map[prev_d]
                        curr_eq = equity_map[d]

                        if prev_eq > 0 and d in bm_returns_map:
                            portfolio_returns_aligned.append(
                                (curr_eq - prev_eq) / prev_eq
                            )
                            benchmark_returns_aligned.append(bm_returns_map[d])

                    if len(portfolio_returns_aligned) >= 10:
                        bm_metrics = compute_benchmark_metrics(
                            portfolio_returns_aligned,
                            benchmark_returns_aligned,
                        )
                        if bm_metrics:
                            metrics_dict["benchmark"] = {
                                "code": benchmark_code,
                                **bm_metrics.to_dict(),
                            }

                    # 同时保存基准净值到 equity 表的 benchmark_value 字段
                    # Use a savepoint so that if UPDATE fails, we can recover
                    if bm_rows:
                        bm_close_map = {
                            row.trade_date: row.close for row in bm_rows
                            if row.close is not None
                        }
                        # 归一化基准（以初始资金为基准）
                        first_bm_close = None
                        for snap in bt_result.equity_curve:
                            if snap.trade_date in bm_close_map:
                                first_bm_close = float(bm_close_map[snap.trade_date])
                                break

                        if first_bm_close and first_bm_close > 0:
                            initial_cap = float(run.initial_capital or 100000)
                            from sqlalchemy import update as sql_update
                            try:
                                for snap in bt_result.equity_curve:
                                    if snap.trade_date in bm_close_map:
                                        bm_val = (
                                            float(bm_close_map[snap.trade_date])
                                            / first_bm_close * initial_cap
                                        )
                                        await session.execute(
                                            sql_update(BacktestEquity)
                                            .where(
                                                BacktestEquity.run_id == run_id,
                                                BacktestEquity.trade_date == snap.trade_date,
                                            )
                                            .values(benchmark_value=Decimal(str(round(bm_val, 2))))
                                        )
                                await session.commit()
                            except Exception as nested_err:
                                # 更新失败，回滚以恢复事务状态
                                await session.rollback()
                                log.warning(
                                    "backtest.benchmark_value_update_failed",
                                    run_id=run_id,
                                    error=str(nested_err),
                                )

            except Exception as bm_err:
                log.warning(
                    "backtest.benchmark_metrics_failed",
                    run_id=run_id,
                    error=str(bm_err),
                )

            run.metrics = metrics_dict
            run.finished_at = datetime.now(timezone.utc)
            run.error_msg = None
            run.progress = 100
            last_persisted_progress = 100.0

            # 在最终 commit 前确保事务干净 — 如果之前的可选操作污染了事务，
            # 先 rollback 再重新 attach run 对象后更新
            try:
                await session.commit()
            except Exception as commit_err:
                log.warning(
                    "backtest.final_commit_recovery",
                    run_id=run_id,
                    error=str(commit_err),
                )
                await session.rollback()
                # 重新加载 run 并写入最终状态
                result2 = await session.execute(
                    select(BacktestRun).where(BacktestRun.id == run_id)
                )
                run2 = result2.scalar_one()
                run2.status = "done"
                run2.progress = 100
                run2.metrics = metrics_dict
                run2.finished_at = datetime.now(timezone.utc)
                run2.error_msg = None
                await session.commit()
                run = run2

            # Publish completion
            completion_payload = json.dumps({
                "run_id": run_id,
                "progress": 100,
                "message": "回测完成",
                "status": "done",
            })
            redis_client.set(progress_key, completion_payload, ex=3600)
            redis_client.publish(channel_key, completion_payload)

            log.info("backtest.completed", run_id=run_id)
            return {"status": "done", "run_id": run_id, "metrics": run.metrics}

        except Exception as e:
            # Mark as failed
            await session.rollback()
            run.status = "failed"
            run.error_msg = str(e)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            await session.commit()

            # Publish failure
            failure_payload = json.dumps({
                "run_id": run_id,
                "progress": run.progress or 0,
                "message": f"回测失败: {str(e)[:200]}",
                "status": "failed",
            })
            redis_client.set(progress_key, failure_payload, ex=3600)
            redis_client.publish(channel_key, failure_payload)

            log.error(
                "backtest.failed",
                run_id=run_id,
                error=str(e),
                traceback=traceback.format_exc(),
            )
            return {"status": "failed", "run_id": run_id, "error": str(e)}
        finally:
            redis_client.close()
