"""Backtest API endpoints.

Provides:
- ``POST /backtests``                  — submit a new backtest (async, returns run_id)
- ``POST /backtests/{run_id}/rerun``   — rerun an existing backtest by id
- ``GET /backtests/{run_id}``          — get backtest status/result
- ``GET /backtests/{run_id}/equity``   — get equity curve
- ``GET /backtests/{run_id}/trades``   — get trade history
- ``GET /backtests/{run_id}/attribution`` — get attribution results
- ``WS /backtests/{run_id}/progress``  — WebSocket progress subscription

Backtest tasks are dispatched to Celery for async execution.
Progress is written to Redis and pushed via WebSocket.

Requirements: 7.3, 7.4
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.strategies import STRATEGY_PARAM_SCHEMAS
from app.core.config import Settings, get_settings
from app.data.models.backtests import BacktestEquity, BacktestRun, BacktestTrade
from app.data.session import get_session
from app.domain.backtest.result import BacktestQuality

router = APIRouter(prefix="/backtests", tags=["backtests"])


def _build_param_space(strategy_type: str | None, params: dict[str, Any]):
    """按策略类型显式定义 Walk-Forward 参数空间。"""
    from app.services.optimization import ParamDimension, ParamSpace, ParamType

    if not strategy_type:
        return ParamSpace(dimensions=[])

    current = params or {}

    def _discrete(name: str, low: int, high: int, step: int = 1):
        return ParamDimension(name=name, param_type=ParamType.DISCRETE, low=low, high=high, step=step)

    def _categorical(name: str, choices: list[Any]):
        return ParamDimension(name=name, param_type=ParamType.CATEGORICAL, choices=choices)

    def _continuous(name: str, low: float, high: float, step: float):
        return ParamDimension(name=name, param_type=ParamType.CONTINUOUS, low=low, high=high, step=step)

    dimensions: list[ParamDimension] = []

    if strategy_type == "momentum":
        lookback = int(current.get("lookback_days") or current.get("lookback_months", 6) * 20)
        top_n = int(current.get("top_n", 3))
        dims = [
            _discrete("lookback_days", max(20, lookback - 40), max(lookback + 40, 60), 20),
            _discrete("top_n", max(1, top_n - 1), max(2, top_n + 1), 1),
            _categorical("rebalance_freq", ["weekly", "monthly"]),
            _categorical("score_method", ["return", "sharpe"]),
        ]
        dimensions.extend(dims)
    elif strategy_type == "dca":
        amount = float(current.get("amount", 1000))
        dims = [
            _continuous("amount", max(100.0, amount * 0.5), max(amount * 1.5, 1000.0), max(amount * 0.25, 100.0)),
            _categorical("frequency", ["weekly", "biweekly", "monthly"]),
        ]
        dca_type = current.get("dca_type")
        if dca_type in {"smart", "value_averaging"}:
            dims.append(_categorical("dca_type", ["fixed", "value_averaging", "smart"]))
        if "ma_window" in current:
            ma_window = int(current.get("ma_window", 20))
            dims.append(_discrete("ma_window", max(5, ma_window - 10), max(ma_window + 10, 30), 5))
        dimensions.extend(dims)
    elif strategy_type == "risk_parity":
        lookback = int(current.get("lookback_days", 60))
        dimensions.extend([
            _discrete("lookback_days", max(20, lookback - 40), max(lookback + 40, 80), 20),
            _categorical("rebalance_freq", ["weekly", "monthly", "quarterly"]),
            _categorical("cov_method", ["sample", "ewm", "shrinkage"]),
        ])
    elif strategy_type == "mean_variance":
        lookback = int(current.get("lookback_days", 60))
        rf = float(current.get("risk_free_rate", 0.02))
        target_return = float(current.get("target_return") or current.get("target_annual_return", 0.08))
        dimensions.extend([
            _discrete("lookback_days", max(20, lookback - 40), max(lookback + 40, 80), 20),
            _categorical("rebalance_freq", ["weekly", "monthly", "quarterly"]),
            _categorical("objective", ["max_sharpe", "min_variance", "target_return"]),
            _continuous("risk_free_rate", max(0.0, rf - 0.02), max(rf + 0.02, 0.04), 0.01),
            _continuous("target_annual_return", max(0.02, target_return - 0.04), max(target_return + 0.04, 0.12), 0.02),
        ])
    elif strategy_type == "timing":
        method = current.get("method", "dual_ma")
        dimensions.append(_categorical("method", ["dual_ma", "macd", "valuation"]))
        if method == "dual_ma":
            short_window = int(current.get("short_window") or current.get("fast_window", 5))
            long_window = int(current.get("long_window") or current.get("slow_window", 20))
            dimensions.extend([
                _discrete("short_window", max(3, short_window - 4), max(short_window + 4, 10), 1),
                _discrete("long_window", max(10, long_window - 10), max(long_window + 10, 30), 5),
            ])
        elif method == "macd":
            fast_period = int(current.get("fast_period") or current.get("fast_window", 12))
            slow_period = int(current.get("slow_period") or current.get("slow_window", 26))
            signal_period = int(current.get("signal_period", 9))
            dimensions.extend([
                _discrete("fast_period", max(6, fast_period - 4), max(fast_period + 4, 16), 2),
                _discrete("slow_period", max(14, slow_period - 8), max(slow_period + 8, 34), 2),
                _discrete("signal_period", max(5, signal_period - 2), max(signal_period + 2, 12), 1),
            ])
        else:
            lookback_days = int(current.get("lookback_days", 252))
            low_threshold = float(current.get("low_threshold", 0.3))
            high_threshold = float(current.get("high_threshold", 0.7))
            dimensions.extend([
                _discrete("lookback_days", max(60, lookback_days - 120), max(lookback_days + 120, 252), 20),
                _continuous("low_threshold", max(0.05, low_threshold - 0.1), min(0.45, low_threshold + 0.05), 0.05),
                _continuous("high_threshold", max(0.55, high_threshold - 0.05), min(0.95, high_threshold + 0.1), 0.05),
            ])
    elif strategy_type == "fof":
        lookback = int(current.get("lookback_days", 60))
        top_n = int(current.get("top_n", 5))
        dimensions.extend([
            _discrete("lookback_days", max(20, lookback - 40), max(lookback + 40, 80), 20),
            _discrete("top_n", max(1, top_n - 2), max(top_n + 2, 3), 1),
            _categorical("rebalance_freq", ["weekly", "monthly", "quarterly"]),
            _categorical("weight_method", ["equal", "inverse_vol", "score_weighted", "risk_parity"]),
        ])
    else:
        # 未知类型保守回退：不做参数搜索，仅验证固定参数。
        return ParamSpace(dimensions=[])

    deduped: dict[str, ParamDimension] = {}
    for dim in dimensions:
        deduped[dim.name] = dim
    return ParamSpace(dimensions=list(deduped.values()))


def _is_legacy_drawdown_suspect(metrics: dict[str, Any] | None) -> bool:
    """Detect persisted results likely generated by the old frozen-cash bug."""
    if not isinstance(metrics, dict):
        return False
    try:
        return float(metrics.get("max_drawdown")) <= -0.9999
    except (TypeError, ValueError):
        return False


def _quality_from_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """从持久化 metrics 中提取或构建回测质量标签。"""
    if isinstance(metrics, dict) and isinstance(metrics.get("quality"), dict):
        quality = dict(metrics["quality"])
        warnings = list(quality.get("warnings") or [])
        if _is_legacy_drawdown_suspect(metrics):
            warning = "历史回测疑似由旧版权益曲线生成，最大回撤可能失真，建议重新运行回测"
            if warning not in warnings:
                warnings.append(warning)
        quality["warnings"] = warnings
        return quality
    pit_quality = "missing"
    warnings: list[str] = []
    if isinstance(metrics, dict):
        if metrics.get("pit_data_quality") in {"strict", "fallback", "missing"}:
            pit_quality = str(metrics["pit_data_quality"])
        if metrics.get("nav_data_stale"):
            warnings.append("NAV 数据可能已变更，建议刷新回测")
        if metrics.get("nav_quality_warning"):
            warnings.append("NAV 数据质量存在警告，请先核对数据源口径")
        if _is_legacy_drawdown_suspect(metrics):
            warnings.append("历史回测疑似由旧版权益曲线生成，最大回撤可能失真，建议重新运行回测")
    return BacktestQuality(pit_data_quality=pit_quality, warnings=warnings).to_dict()


def _pending_worker_message(run: BacktestRun, status: str | None, progress: float | None) -> str | None:
    """Return a user-facing hint when a queued backtest has not been picked up."""
    if status not in {"pending", "running"}:
        return None
    if run.started_at is not None:
        return None
    created_at = run.created_at
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    elapsed = datetime.now(timezone.utc) - created_at
    if elapsed >= timedelta(minutes=2):
        return "回测任务已提交但尚未被 worker 接收，请确认 Celery worker 和 Redis 正常运行"
    if status == "pending" and (progress is None or progress <= 0):
        return "回测任务已进入队列，等待 worker 接收"
    return None


def _dispatch_backtest_task(run_id: int):
    """Dispatch a Celery backtest task and surface broker errors explicitly."""
    from app.tasks.backtest import run_backtest

    try:
        return run_backtest.delay(run_id)
    except Exception as exc:
        # Caller owns commit/rollback around the ORM object; this helper only
        # normalizes the dispatch exception type.
        raise RuntimeError(f"回测任务派发失败，请确认 Redis/Celery 可用: {exc}") from exc


@dataclass
class _AnalysisContext:
    run: Any
    strategy_row: Any
    universe_codes: list[str]
    nav_data: dict[str, dict[date, Decimal]]
    fund_meta: dict[str, Any]
    dividends: list[Any]


async def _load_analysis_context(run_id: int, db: AsyncSession) -> _AnalysisContext:
    from app.data.models.fund_dividends import FundDividend
    from app.data.models.fund_fees import FundFee
    from app.data.models.fund_nav import FundNav
    from app.data.models.funds import Fund
    from app.data.models.strategies import Strategy
    from app.domain.backtest.engine_event import DividendInfo, FundMeta
    from app.domain.backtest.fees import FeeTier

    result = await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"回测 {run_id} 不存在")
    if run.status != "done":
        raise HTTPException(status_code=400, detail=f"回测尚未完成，当前状态: {run.status}")
    if not run.strategy_id:
        raise HTTPException(status_code=400, detail="该回测缺少 strategy_id，无法执行稳健性诊断")

    strat_result = await db.execute(select(Strategy).where(Strategy.id == run.strategy_id))
    strategy_row = strat_result.scalar_one_or_none()
    if strategy_row is None:
        raise HTTPException(status_code=404, detail=f"策略 {run.strategy_id} 不存在")

    universe_codes = strategy_row.universe
    if isinstance(universe_codes, dict):
        universe_codes = universe_codes.get("fund_codes", [])
    if not universe_codes:
        raise HTTPException(status_code=400, detail="策略基金池为空，无法执行稳健性诊断")

    nav_data: dict[str, dict[date, Decimal]] = {}
    for code in universe_codes:
        nav_stmt = (
            select(FundNav)
            .where(
                FundNav.fund_code == code,
                FundNav.trade_date >= run.start_date,
                FundNav.trade_date <= run.end_date,
            )
            .order_by(FundNav.trade_date)
        )
        nav_rows = (await db.execute(nav_stmt)).scalars().all()
        nav_series = {
            row.trade_date: (row.adj_nav if row.adj_nav is not None else row.unit_nav)
            for row in nav_rows
            if row.adj_nav is not None or row.unit_nav is not None
        }
        if nav_series:
            nav_data[code] = nav_series

    if not nav_data:
        raise HTTPException(status_code=400, detail="回测期间无可用净值数据，无法执行稳健性诊断")

    meta_result = await db.execute(
        select(
            Fund.code,
            Fund.fund_type,
            Fund.delisting_date,
            Fund.is_purchasable,
            Fund.purchase_limit,
        ).where(Fund.code.in_(universe_codes))
    )
    fund_rows = meta_result.all()

    fee_result = await db.execute(
        select(FundFee)
        .where(FundFee.fund_code.in_(universe_codes))
        .order_by(FundFee.fund_code, FundFee.fee_type, FundFee.min_amount, FundFee.min_holding_days)
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

    fund_meta: dict[str, FundMeta] = {}
    for row in fund_rows:
        fee_bucket = fee_tiers_by_code.get(row.code, {})
        fund_meta[row.code] = FundMeta(
            code=row.code,
            fund_type=row.fund_type or "stock",
            subscribe_fee_tiers=list(fee_bucket.get("subscribe", [])),
            redeem_fee_tiers=list(fee_bucket.get("redeem", [])),
            is_purchasable=bool(row.is_purchasable),
            purchase_limit=row.purchase_limit,
            delisting_date=row.delisting_date,
        )

    dividend_rows = (
        await db.execute(
            select(FundDividend)
            .where(
                FundDividend.fund_code.in_(universe_codes),
                FundDividend.ex_date >= run.start_date,
                FundDividend.ex_date <= run.end_date,
            )
            .order_by(FundDividend.fund_code, FundDividend.ex_date)
        )
    ).scalars().all()
    dividends = [
        DividendInfo(
            fund_code=row.fund_code,
            ex_date=row.ex_date,
            dividend_per_share=row.dividend_per_share,
            split_ratio=row.split_ratio,
            reinvest=True,
        )
        for row in dividend_rows
    ]

    return _AnalysisContext(
        run=run,
        strategy_row=strategy_row,
        universe_codes=list(universe_codes),
        nav_data=nav_data,
        fund_meta=fund_meta,
        dividends=dividends,
    )


def _merge_ranges(ranges: list[tuple[date, date]]) -> tuple[date, date] | None:
    if not ranges:
        return None
    return min(start for start, _ in ranges), max(end for _, end in ranges)


async def _run_true_cpcv(
    run_id: int,
    n_splits: int,
    n_test_splits: int,
    purge_days: int,
    embargo_days: int,
    max_paths: int | None,
    db: AsyncSession,
) -> CPCVResponse:
    from app.domain.backtest.cpcv import CPCVConfig, run_cpcv
    from app.domain.strategy.base import create_strategy_from_config
    from app.services.walk_forward import WalkForwardAnalyzer, WalkForwardConfig

    ctx = await _load_analysis_context(run_id, db)
    run = ctx.run
    strategy_row = ctx.strategy_row
    all_dates = sorted({d for series in ctx.nav_data.values() for d in series.keys() if run.start_date <= d <= run.end_date})
    if len(all_dates) < max(n_splits * 10, 60):
        return CPCVResponse(
            run_id=run_id,
            pbo=0.0,
            avg_oos_sharpe=0.0,
            std_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            n_paths=0,
            is_overfit=False,
            n_splits=n_splits,
            n_test_splits=n_test_splits,
            note="样本不足，无法执行 CPCV/PBO 诊断",
            paths=[],
        )

    param_space = _build_param_space(strategy_row.strategy_type, strategy_row.params or {})

    def strategy_factory(window_params: dict[str, Any]):
        merged_params = dict(strategy_row.params or {})
        merged_params.update(window_params)
        return create_strategy_from_config(
            strategy_type=strategy_row.strategy_type,
            params=merged_params,
            universe=strategy_row.universe,
        )

    def backtest_fn(
        train_ranges: list[tuple[date, date]],
        test_ranges: list[tuple[date, date]],
    ) -> tuple[float, float, float, float]:
        train_span = _merge_ranges(train_ranges)
        test_span = _merge_ranges(test_ranges)
        if train_span is None or test_span is None:
            return 0.0, 0.0, 0.0, 0.0

        train_trade_days = sum(len([d for d in all_dates if start <= d <= end]) for start, end in train_ranges)
        test_trade_days = sum(len([d for d in all_dates if start <= d <= end]) for start, end in test_ranges)
        if train_trade_days < 20 or test_trade_days < 20:
            return 0.0, 0.0, 0.0, 0.0

        analyzer = WalkForwardAnalyzer(
            param_space=param_space,
            strategy_factory=strategy_factory,
            nav_data=ctx.nav_data,
            initial_capital=run.initial_capital or Decimal("100000"),
            fund_meta=ctx.fund_meta or None,
            dividends=ctx.dividends,
            config=WalkForwardConfig(
                train_days=max(train_trade_days, 20),
                test_days=max(test_trade_days, 20),
                step_days=max(test_trade_days, 20),
                objective="multi_objective_score",
                maximize=True,
                method="grid",
            ),
        )
        train_metrics = analyzer._optimize_on_window(train_span[0], train_span[1])
        test_metrics = analyzer._backtest_on_window(train_metrics.best_params, test_span[0], test_span[1])
        best_train_metrics = train_metrics.trials[0].metrics if train_metrics.trials else {}
        return (
            float(best_train_metrics.get("sharpe", train_metrics.best_metric)),
            float(test_metrics.get("sharpe", 0.0)),
            float(best_train_metrics.get("total_return", 0.0)),
            float(test_metrics.get("total_return", 0.0)),
        )

    try:
        result = run_cpcv(
            all_dates=all_dates,
            backtest_fn=backtest_fn,
            config=CPCVConfig(
                n_splits=n_splits,
                n_test_splits=n_test_splits,
                purge_days=purge_days,
                embargo_days=embargo_days,
            ),
            max_paths=max_paths,
        )
    except ValueError as exc:
        return CPCVResponse(
            run_id=run_id,
            pbo=0.0,
            avg_oos_sharpe=0.0,
            std_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            n_paths=0,
            is_overfit=False,
            n_splits=n_splits,
            n_test_splits=n_test_splits,
            note=str(exc),
            paths=[],
        )

    return CPCVResponse(
        run_id=run_id,
        pbo=round(result.pbo, 4),
        avg_oos_sharpe=round(result.avg_oos_sharpe, 4),
        std_oos_sharpe=round(result.std_oos_sharpe, 4),
        avg_is_sharpe=round(result.avg_is_sharpe, 4),
        n_paths=result.n_paths,
        is_overfit=result.is_overfit,
        n_splits=result.config.n_splits,
        n_test_splits=result.config.n_test_splits,
        note=(
            "PBO > 0.5 表示存在明显过拟合风险"
            if result.is_overfit else "PBO 未显示明显过拟合"
        ),
        paths=[
            CPCVPathResponse(
                test_groups=list(path.test_groups),
                train_groups=list(path.train_groups),
                is_sharpe=round(path.is_sharpe, 4),
                oos_sharpe=round(path.oos_sharpe, 4),
                is_return=round(path.is_return, 6),
                oos_return=round(path.oos_return, 6),
            )
            for path in result.paths
        ],
    )


async def _run_true_walk_forward(
    run_id: int,
    train_months: int,
    test_months: int,
    step_months: int,
    db: AsyncSession,
    max_trials: int = 200,
) -> "WalkForwardResponse":
    from app.data.models.strategies import Strategy
    from app.domain.strategy.base import create_strategy_from_config
    from app.services.walk_forward import WalkForwardAnalyzer, WalkForwardConfig

    # Fast preflight: avoid loading full NAV/fee/dividend context for requests
    # that are too large for synchronous API execution.
    preflight_stmt = (
        select(BacktestRun, Strategy)
        .join(Strategy, BacktestRun.strategy_id == Strategy.id)
        .where(BacktestRun.id == run_id)
    )
    preflight_row = (await db.execute(preflight_stmt)).one_or_none()
    if preflight_row is None:
        raise HTTPException(status_code=404, detail=f"回测 {run_id} 不存在或缺少关联策略")
    run = preflight_row.BacktestRun
    strategy_row = preflight_row.Strategy
    if run.status != "done":
        raise HTTPException(status_code=400, detail=f"回测尚未完成，当前状态: {run.status}")

    train_days = max(train_months * 21, 20)
    test_days = max(test_months * 21, 20)
    step_days = max(step_months * 21, 20)

    param_space = _build_param_space(strategy_row.strategy_type, strategy_row.params or {})
    try:
        estimated_trials = len(param_space.grid_points())
    except Exception:
        estimated_trials = max_trials + 1
    if estimated_trials > max_trials:
        return WalkForwardResponse(
            run_id=run_id,
            wfe=0.0,
            avg_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            avg_oos_return=0.0,
            oos_win_rate=0.0,
            total_oos_return=0.0,
            is_robust=False,
            windows=[],
            note=(
                f"Walk-Forward 参数组合约 {estimated_trials} 个，超过当前同步上限 {max_trials}；"
                "请缩小参数空间或改用后台任务执行"
            ),
        )

    ctx = await _load_analysis_context(run_id, db)
    run = ctx.run
    strategy_row = ctx.strategy_row

    def strategy_factory(window_params: dict[str, Any]):
        merged_params = dict(strategy_row.params or {})
        merged_params.update(window_params)
        return create_strategy_from_config(
            strategy_type=strategy_row.strategy_type,
            params=merged_params,
            universe=strategy_row.universe,
        )

    analyzer = WalkForwardAnalyzer(
        param_space=param_space,
        strategy_factory=strategy_factory,
        nav_data=ctx.nav_data,
        initial_capital=run.initial_capital or Decimal("100000"),
        fund_meta=ctx.fund_meta or None,
        dividends=ctx.dividends,
        config=WalkForwardConfig(
            train_days=train_days,
            test_days=test_days,
            step_days=step_days,
            objective="sharpe",
            maximize=True,
            method="grid",
        ),
    )

    try:
        wf_result = analyzer.run(run.start_date, run.end_date)
    except ValueError as exc:
        return WalkForwardResponse(
            run_id=run_id,
            wfe=0.0,
            avg_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            avg_oos_return=0.0,
            oos_win_rate=0.0,
            total_oos_return=0.0,
            is_robust=False,
            windows=[],
            note=str(exc),
        )

    avg_is_sharpe = float(sum(w.train_metric for w in wf_result.windows) / len(wf_result.windows)) if wf_result.windows else 0.0
    avg_oos_sharpe = float(wf_result.aggregated_metrics.get("sharpe", 0.0))
    avg_oos_return = float(wf_result.aggregated_metrics.get("total_return", 0.0))
    positive_windows = sum(1 for w in wf_result.windows if w.test_metrics.get("total_return", 0.0) > 0)
    oos_win_rate = positive_windows / len(wf_result.windows) if wf_result.windows else 0.0

    total_oos = 1.0
    for w in wf_result.windows:
        total_oos *= (1 + float(w.test_metrics.get("total_return", 0.0)))
    total_oos_return = total_oos - 1.0 if wf_result.windows else 0.0
    wfe = avg_oos_sharpe / avg_is_sharpe if abs(avg_is_sharpe) > 1e-8 else 0.0
    is_robust = wfe > 0.5 and oos_win_rate > 0.5

    return WalkForwardResponse(
        run_id=run_id,
        wfe=round(wfe, 4),
        avg_oos_sharpe=round(avg_oos_sharpe, 4),
        avg_is_sharpe=round(avg_is_sharpe, 4),
        avg_oos_return=round(avg_oos_return, 6),
        oos_win_rate=round(oos_win_rate, 4),
        total_oos_return=round(total_oos_return, 6),
        is_robust=is_robust,
        windows=[
            WalkForwardWindowResponse(
                window_id=w.window_index + 1,
                train_start=w.train_start,
                train_end=w.train_end,
                test_start=w.test_start,
                test_end=w.test_end,
                is_sharpe=round(float(w.train_metrics.get("sharpe", w.train_metric)), 4),
                oos_sharpe=round(float(w.test_metrics.get("sharpe", 0.0)), 4),
                is_return=round(float(w.train_metrics.get("total_return", 0.0)), 6),
                oos_return=round(float(w.test_metrics.get("total_return", 0.0)), 6),
                is_max_drawdown=round(float(w.train_metrics.get("max_drawdown", 0.0)), 6),
                oos_max_drawdown=round(float(w.test_metrics.get("max_drawdown", 0.0)), 6),
            )
            for w in wf_result.windows
        ],
        note=(
            f"真实 Walk-Forward 已执行 {len(wf_result.windows)} 个窗口"
            if wf_result.windows else "真实 Walk-Forward 未生成有效窗口"
        ),
    )


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class BacktestSubmit(BaseModel):
    """Request body for submitting a backtest."""

    strategy_id: int = Field(..., description="策略 ID")
    start_date: date = Field(..., description="回测起始日期")
    end_date: date = Field(..., description="回测结束日期")
    initial_capital: Decimal = Field(
        default=Decimal("100000"),
        gt=0,
        description="初始资金",
    )


class BacktestSubmitResponse(BaseModel):
    """Response for backtest submission (202 Accepted)."""

    run_id: int = Field(..., description="回测运行 ID")
    status: str = Field(default="pending", description="初始状态")
    message: str = Field(default="回测任务已提交", description="提示信息")


class BacktestStatusResponse(BaseModel):
    """Response for backtest status query."""

    id: int = Field(..., description="回测运行 ID")
    strategy_id: int | None = Field(None, description="策略 ID")
    strategy_name: str | None = Field(None, description="策略名称")
    start_date: date | None = Field(None, description="回测起始日期")
    end_date: date | None = Field(None, description="回测结束日期")
    initial_capital: str | None = Field(None, description="初始资金")
    status: str | None = Field(None, description="状态: pending/running/done/failed")
    progress: float | None = Field(None, description="进度百分比 0-100")
    progress_message: str | None = Field(None, description="当前阶段进度说明")
    metrics: dict[str, Any] | None = Field(None, description="绩效指标摘要")
    nav_data_stale: dict[str, Any] | None = Field(None, description="NAV 复权口径变更导致结果可能过期的提示")
    nav_quality_warning: dict[str, Any] | None = Field(None, description="NAV 数据源口径混用或质量问题提示")
    quality: dict[str, Any] | None = Field(None, description="回测可信度/决策级别质量标签")
    error_msg: str | None = Field(None, description="错误信息")
    started_at: datetime | None = Field(None, description="开始时间")
    finished_at: datetime | None = Field(None, description="结束时间")


class EquityPoint(BaseModel):
    """Single point in the equity curve."""

    trade_date: date
    equity: float
    cash: float | None = None
    position_value: float | None = None
    benchmark_value: float | None = None


class EquityCurveResponse(BaseModel):
    """Response for equity curve query."""

    run_id: int
    records: list[EquityPoint]


class TradeRecord(BaseModel):
    """Single trade record."""

    trade_id: int
    order_date: date | None = None
    confirm_date: date | None = None
    fund_code: str | None = None
    direction: str | None = None
    amount: float | None = None
    shares: float | None = None
    nav: float | None = None
    fee: float | None = None


class TradesResponse(BaseModel):
    """Response for trades query."""

    run_id: int
    items: list[TradeRecord]
    total: int
    page: int
    page_size: int
    pages: int


class BrinsonAttribution(BaseModel):
    """Brinson attribution result."""

    allocation_effect: float
    selection_effect: float
    interaction_effect: float
    total_excess: float


class FamaFrenchAttribution(BaseModel):
    """Fama-French attribution result."""

    alpha: float
    beta_mkt: float
    beta_smb: float
    beta_hml: float
    beta_rmw: float | None = None
    beta_cma: float | None = None
    r_squared: float


class AttributionResponse(BaseModel):
    """Response for attribution query."""

    run_id: int
    fama_french: FamaFrenchAttribution | None = None
    brinson: BrinsonAttribution | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _clear_live_progress(run_id: int, settings: Settings) -> None:
    """在重新提交回测前清理旧的 Redis 进度缓存。"""
    try:
        import redis

        redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            redis_client.delete(f"backtest:progress:{run_id}")
        finally:
            redis_client.close()
    except Exception:
        pass



def _merge_live_progress(
    run: BacktestRun,
    settings: Settings,
) -> tuple[str | None, float | None, str | None, str | None]:
    """用 Redis 中的实时进度覆盖数据库中的陈旧状态。"""
    status = run.status
    progress = float(run.progress) if run.progress is not None else None
    progress_message: str | None = None
    error_msg = run.error_msg

    if status not in {"pending", "running"}:
        return status, progress, progress_message, error_msg

    try:
        import redis

        redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            payload = redis_client.get(f"backtest:progress:{run.id}")
        finally:
            redis_client.close()
    except Exception:
        pending_message = _pending_worker_message(run, status, progress)
        return status, progress, pending_message or progress_message, error_msg

    if not payload:
        pending_message = _pending_worker_message(run, status, progress)
        return status, progress, pending_message or progress_message, error_msg

    try:
        live = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return status, progress, progress_message, error_msg

    live_status = live.get("status")
    live_progress = live.get("progress")
    live_message = live.get("message")

    if isinstance(live_status, str):
        status = live_status
    if isinstance(live_progress, (int, float)):
        progress = float(live_progress)
    if isinstance(live_message, str) and live_message:
        progress_message = live_message
        if status == "failed":
            error_msg = live_message

    return status, progress, progress_message, error_msg


def _reset_backtest_run(run: BacktestRun) -> None:
    """重置回测主记录，供重跑前复用。"""
    run.status = "pending"
    run.progress = Decimal("0")
    run.metrics = None
    run.error_msg = None
    run.started_at = None
    run.finished_at = None


@router.get(
    "",
    response_model=list[BacktestStatusResponse],
    summary="列出所有回测",
)
async def list_backtests(
    session: AsyncSession = Depends(get_session),
) -> list[BacktestStatusResponse]:
    """获取所有回测记录列表。"""
    from app.data.models.strategies import Strategy

    settings = get_settings()
    stmt = (
        select(BacktestRun, Strategy.name.label("strategy_name"))
        .outerjoin(Strategy, BacktestRun.strategy_id == Strategy.id)
        .order_by(BacktestRun.id.desc())
        .limit(50)
    )
    result = await session.execute(stmt)
    rows = result.all()
    items: list[BacktestStatusResponse] = []
    for r in rows:
        status, progress, progress_message, error_msg = _merge_live_progress(
            r.BacktestRun,
            settings,
        )
        items.append(
            BacktestStatusResponse(
                id=r.BacktestRun.id,
                strategy_id=r.BacktestRun.strategy_id,
                strategy_name=r.strategy_name,
                start_date=r.BacktestRun.start_date,
                end_date=r.BacktestRun.end_date,
                initial_capital=str(r.BacktestRun.initial_capital) if r.BacktestRun.initial_capital is not None else None,
                status=status,
                progress=progress,
                progress_message=progress_message,
                metrics=r.BacktestRun.metrics,
                nav_data_stale=(r.BacktestRun.metrics or {}).get("nav_data_stale") if isinstance(r.BacktestRun.metrics, dict) else None,
                nav_quality_warning=(r.BacktestRun.metrics or {}).get("nav_quality_warning") if isinstance(r.BacktestRun.metrics, dict) else None,
                quality=_quality_from_metrics(r.BacktestRun.metrics),
                error_msg=error_msg,
                started_at=r.BacktestRun.started_at,
                finished_at=r.BacktestRun.finished_at,
            )
        )
    return items


@router.post(
    "",
    response_model=BacktestSubmitResponse,
    status_code=202,
    summary="发起回测",
    description="提交回测任务，异步执行。返回 run_id 用于后续查询状态和结果。",
)
async def submit_backtest(
    body: BacktestSubmit,
    db: AsyncSession = Depends(get_session),
) -> BacktestSubmitResponse:
    """Submit a new backtest and execute synchronously."""
    settings = get_settings()

    # Validate date range
    if body.end_date <= body.start_date:
        raise HTTPException(
            status_code=422,
            detail="end_date 必须晚于 start_date",
        )

    # Verify strategy exists
    from app.data.models.strategies import Strategy

    strat_result = await db.execute(
        select(Strategy).where(Strategy.id == body.strategy_id)
    )
    strategy = strat_result.scalar_one_or_none()
    if strategy is None:
        raise HTTPException(
            status_code=404,
            detail=f"策略 {body.strategy_id} 不存在",
        )

    # Validate: backtest start_date must not be earlier than fund inception dates
    from app.data.models.funds import Fund

    universe_codes = strategy.universe
    if isinstance(universe_codes, dict):
        universe_codes = universe_codes.get("fund_codes", [])

    if universe_codes:
        fund_result = await db.execute(
            select(Fund.code, Fund.name, Fund.inception_date).where(
                Fund.code.in_(universe_codes)
            )
        )
        fund_rows = fund_result.all()

        # 对于没有 inception_date 的基金，用 NAV 数据中最早日期作为替代
        from app.data.models.fund_nav import FundNav
        from sqlalchemy import and_, func as sa_func

        violations: list[str] = []
        for row in fund_rows:
            effective_start = row.inception_date

            if effective_start is None:
                # 查询该基金最早的 NAV 记录日期作为替代
                earliest_nav_result = await db.execute(
                    select(sa_func.min(FundNav.trade_date)).where(
                        FundNav.fund_code == row.code
                    )
                )
                earliest_nav_date = earliest_nav_result.scalar_one_or_none()
                if earliest_nav_date is not None:
                    effective_start = earliest_nav_date

            if effective_start and effective_start > body.start_date:
                violations.append(
                    f"{row.code}({row.name}) 最早数据始于 {effective_start}"
                )

        if violations:
            detail = (
                f"回测起始日期 {body.start_date} 早于以下基金的可用数据起始日期，"
                f"回测结果将不可靠：{'；'.join(violations)}。"
                f"请将起始日期调整为不早于所有基金的数据起始日期。"
            )
            raise HTTPException(status_code=422, detail=detail)

    # 查找是否已有相同参数的回测记录（同策略、同日期范围、同初始资金）
    from sqlalchemy import and_, delete as sql_delete

    existing_result = await db.execute(
        select(BacktestRun)
        .where(
            and_(
                BacktestRun.strategy_id == body.strategy_id,
                BacktestRun.start_date == body.start_date,
                BacktestRun.end_date == body.end_date,
                BacktestRun.initial_capital == body.initial_capital,
            )
        )
        .order_by(BacktestRun.id.asc())
    )
    existing_runs = existing_result.scalars().all()

    if existing_runs:
        # 保留第一条（ID 最小的），删除其余重复记录
        keep_run = existing_runs[0]
        duplicates = existing_runs[1:]

        for dup in duplicates:
            await db.execute(
                sql_delete(BacktestEquity).where(BacktestEquity.run_id == dup.id)
            )
            await db.execute(
                sql_delete(BacktestTrade).where(BacktestTrade.run_id == dup.id)
            )
            await db.delete(dup)

        # 覆盖保留记录：清除旧的关联数据，重置状态
        await db.execute(
            sql_delete(BacktestEquity).where(BacktestEquity.run_id == keep_run.id)
        )
        await db.execute(
            sql_delete(BacktestTrade).where(BacktestTrade.run_id == keep_run.id)
        )
        _reset_backtest_run(keep_run)
        await db.commit()
        _clear_live_progress(keep_run.id, settings)
        await db.refresh(keep_run)
        run = keep_run
    else:
        # 新建回测记录
        run = BacktestRun(
            strategy_id=body.strategy_id,
            start_date=body.start_date,
            end_date=body.end_date,
            initial_capital=body.initial_capital,
            status="pending",
            progress=Decimal("0"),
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)

    # Dispatch Celery task. Keep the DB state honest: a run is only marked
    # running by the worker after it actually starts.
    try:
        _dispatch_backtest_task(run.id)
    except RuntimeError as exc:
        run.status = "failed"
        run.progress = Decimal("0")
        run.error_msg = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return BacktestSubmitResponse(
        run_id=run.id,
        status="pending",
        message="回测任务已提交，正在异步执行",
    )


@router.post(
    "/{run_id}/rerun",
    response_model=BacktestSubmitResponse,
    status_code=202,
    summary="重新运行回测",
    description="按回测 ID 重新运行指定记录，并覆盖该记录原有结果。",
)
async def rerun_backtest(
    run_id: int,
    db: AsyncSession = Depends(get_session),
) -> BacktestSubmitResponse:
    """Rerun an existing backtest in place by run id."""
    from sqlalchemy import delete as sql_delete

    settings = get_settings()

    result = await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"回测 {run_id} 不存在")

    if run.status in {"pending", "running"}:
        raise HTTPException(
            status_code=409,
            detail=f"回测 {run_id} 当前状态为 {run.status}，不可重复启动",
        )

    await db.execute(
        sql_delete(BacktestEquity).where(BacktestEquity.run_id == run_id)
    )
    await db.execute(
        sql_delete(BacktestTrade).where(BacktestTrade.run_id == run_id)
    )
    _reset_backtest_run(run)
    await db.commit()
    _clear_live_progress(run_id, settings)
    await db.refresh(run)

    try:
        _dispatch_backtest_task(run.id)
    except RuntimeError as exc:
        run.status = "failed"
        run.progress = Decimal("0")
        run.error_msg = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return BacktestSubmitResponse(
        run_id=run.id,
        status="pending",
        message="回测任务已重新提交，正在异步执行",
    )


@router.get(
    "/{run_id}",
    response_model=BacktestStatusResponse,
    summary="回测状态",
    description="查询回测运行的当前状态和结果摘要。",
)
async def get_backtest_status(
    run_id: int,
    db: AsyncSession = Depends(get_session),
) -> BacktestStatusResponse:
    """Get backtest run status and summary."""
    from app.data.models.strategies import Strategy

    settings = get_settings()
    stmt = (
        select(BacktestRun, Strategy.name.label("strategy_name"))
        .outerjoin(Strategy, BacktestRun.strategy_id == Strategy.id)
        .where(BacktestRun.id == run_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail=f"回测 {run_id} 不存在")

    run = row.BacktestRun
    status, progress, progress_message, error_msg = _merge_live_progress(run, settings)
    return BacktestStatusResponse(
        id=run.id,
        strategy_id=run.strategy_id,
        strategy_name=row.strategy_name,
        start_date=run.start_date,
        end_date=run.end_date,
        initial_capital=str(run.initial_capital) if run.initial_capital else None,
        status=status,
        progress=progress,
        progress_message=progress_message,
        metrics=run.metrics,
        nav_data_stale=(run.metrics or {}).get("nav_data_stale") if isinstance(run.metrics, dict) else None,
        nav_quality_warning=(run.metrics or {}).get("nav_quality_warning") if isinstance(run.metrics, dict) else None,
        quality=_quality_from_metrics(run.metrics),
        error_msg=error_msg,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


@router.get(
    "/{run_id}/equity",
    response_model=EquityCurveResponse,
    summary="资金曲线",
    description="获取回测的每日资金曲线数据。",
)
async def get_backtest_equity(
    run_id: int,
    db: AsyncSession = Depends(get_session),
) -> EquityCurveResponse:
    """Get equity curve for a backtest run."""
    # Verify run exists
    run_result = await db.execute(
        select(BacktestRun).where(BacktestRun.id == run_id)
    )
    run = run_result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"回测 {run_id} 不存在")

    # Query equity data
    result = await db.execute(
        select(BacktestEquity)
        .where(BacktestEquity.run_id == run_id)
        .order_by(BacktestEquity.trade_date)
    )
    rows = result.scalars().all()

    records = [
        EquityPoint(
            trade_date=row.trade_date,
            equity=float(row.equity) if row.equity is not None else 0.0,
            cash=float(row.cash) if row.cash is not None else None,
            position_value=float(row.position_value) if row.position_value is not None else None,
            benchmark_value=float(row.benchmark_value) if row.benchmark_value is not None else None,
        )
        for row in rows
    ]

    return EquityCurveResponse(run_id=run_id, records=records)


@router.get(
    "/{run_id}/trades",
    response_model=TradesResponse,
    summary="交易记录",
    description="获取回测的交易流水记录。",
)
async def get_backtest_trades(
    run_id: int,
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=50, ge=1, le=500, description="每页条数"),
    db: AsyncSession = Depends(get_session),
) -> TradesResponse:
    """Get trade history for a backtest run."""
    # Verify run exists
    run_result = await db.execute(
        select(BacktestRun).where(BacktestRun.id == run_id)
    )
    run = run_result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"回测 {run_id} 不存在")

    # Query trades with pagination
    from sqlalchemy import func

    count_result = await db.execute(
        select(func.count())
        .select_from(BacktestTrade)
        .where(BacktestTrade.run_id == run_id)
    )
    total = count_result.scalar_one()

    offset = (page - 1) * page_size
    result = await db.execute(
        select(BacktestTrade)
        .where(BacktestTrade.run_id == run_id)
        .order_by(BacktestTrade.trade_id)
        .offset(offset)
        .limit(page_size)
    )
    rows = result.scalars().all()

    pages = (total + page_size - 1) // page_size if total > 0 else 0

    items = [
        TradeRecord(
            trade_id=row.trade_id,
            order_date=row.order_date,
            confirm_date=row.confirm_date,
            fund_code=row.fund_code,
            direction=row.direction,
            amount=float(row.amount) if row.amount is not None else None,
            shares=float(row.shares) if row.shares is not None else None,
            nav=float(row.nav) if row.nav is not None else None,
            fee=float(row.fee) if row.fee is not None else None,
        )
        for row in rows
    ]

    return TradesResponse(
        run_id=run_id,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.delete(
    "/{run_id}",
    status_code=204,
    summary="删除回测",
    description="删除指定回测及其关联的权益曲线和交易记录。",
)
async def delete_backtest(
    run_id: int,
    db: AsyncSession = Depends(get_session),
) -> None:
    """Delete a backtest run and all associated data."""
    result = await db.execute(
        select(BacktestRun).where(BacktestRun.id == run_id)
    )
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail=f"回测 {run_id} 不存在")

    # Delete associated equity and trade records first
    from sqlalchemy import delete as sql_delete

    await db.execute(
        sql_delete(BacktestEquity).where(BacktestEquity.run_id == run_id)
    )
    await db.execute(
        sql_delete(BacktestTrade).where(BacktestTrade.run_id == run_id)
    )
    await db.delete(run)
    await db.commit()


@router.get(
    "/{run_id}/attribution",
    response_model=AttributionResponse,
    summary="归因分析",
    description="获取回测的归因分析结果。",
)
async def get_backtest_attribution(
    run_id: int,
    db: AsyncSession = Depends(get_session),
) -> AttributionResponse:
    """Get attribution results for a backtest run.

    Attribution data is stored in the metrics JSON field of the run.
    Full Brinson/Fama-French attribution is computed during the backtest
    and stored alongside the summary metrics.
    """
    result = await db.execute(
        select(BacktestRun).where(BacktestRun.id == run_id)
    )
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail=f"回测 {run_id} 不存在")

    if run.status != "done":
        raise HTTPException(
            status_code=400,
            detail=f"回测尚未完成，当前状态: {run.status}",
        )

    # Extract attribution from metrics if available
    metrics = run.metrics or {}

    # Parse Brinson attribution
    brinson_data = metrics.get("brinson")
    brinson = None
    if isinstance(brinson_data, dict):
        try:
            brinson = BrinsonAttribution(
                allocation_effect=float(brinson_data.get("allocation_effect", 0)),
                selection_effect=float(brinson_data.get("selection_effect", 0)),
                interaction_effect=float(brinson_data.get("interaction_effect", 0)),
                total_excess=float(brinson_data.get("total_excess", 0)),
            )
        except (ValueError, TypeError):
            brinson = None

    # Parse Fama-French attribution
    ff_data = metrics.get("fama_french")
    fama_french = None
    if isinstance(ff_data, dict):
        try:
            fama_french = FamaFrenchAttribution(
                alpha=float(ff_data.get("alpha", 0)),
                beta_mkt=float(ff_data.get("beta_mkt", 0)),
                beta_smb=float(ff_data.get("beta_smb", 0)),
                beta_hml=float(ff_data.get("beta_hml", 0)),
                beta_rmw=float(ff_data["beta_rmw"]) if ff_data.get("beta_rmw") is not None else None,
                beta_cma=float(ff_data["beta_cma"]) if ff_data.get("beta_cma") is not None else None,
                r_squared=float(ff_data.get("r_squared", 0)),
            )
        except (ValueError, TypeError):
            fama_french = None

    # If no pre-computed attribution, try to compute on-the-fly from equity data
    if fama_french is None and brinson is None:
        try:
            import pandas as pd

            from app.services.performance_service import PerformanceService

            # Load equity curve for this run
            equity_result = await db.execute(
                select(BacktestEquity)
                .where(BacktestEquity.run_id == run_id)
                .order_by(BacktestEquity.trade_date)
            )
            equity_rows = equity_result.scalars().all()

            if len(equity_rows) >= 2:
                equity_values = [float(r.equity) for r in equity_rows if r.equity]
                equity_dates = [r.trade_date for r in equity_rows if r.equity]

                if equity_values and equity_values[0] > 0:
                    initial = equity_values[0]
                    nav_series = pd.Series(
                        [v / initial for v in equity_values],
                        index=pd.DatetimeIndex(equity_dates),
                    )

                    # Build Brinson data from trades
                    brinson_input = None
                    trade_result = await db.execute(
                        select(BacktestTrade)
                        .where(BacktestTrade.run_id == run_id)
                        .order_by(BacktestTrade.trade_id)
                    )
                    trade_rows = trade_result.scalars().all()

                    if trade_rows:
                        # Calculate per-fund invested amounts for weights
                        fund_amounts: dict[str, float] = {}
                        fund_codes_set: set[str] = set()
                        for t in trade_rows:
                            if t.fund_code:
                                fund_codes_set.add(t.fund_code)
                            if t.direction == "subscribe" and t.amount and t.fund_code:
                                fund_amounts[t.fund_code] = (
                                    fund_amounts.get(t.fund_code, 0) + float(t.amount)
                                )

                        if fund_codes_set:
                            # Load NAV data for fund returns
                            from app.data.models.fund_nav import FundNav
                            from sqlalchemy import and_

                            portfolio_returns: dict[str, float] = {}
                            for code in fund_codes_set:
                                nav_stmt = (
                                    select(FundNav)
                                    .where(
                                        and_(
                                            FundNav.fund_code == code,
                                            FundNav.trade_date >= run.start_date,
                                            FundNav.trade_date <= run.end_date,
                                        )
                                    )
                                    .order_by(FundNav.trade_date)
                                )
                                nav_result = await db.execute(nav_stmt)
                                nav_rows_list = nav_result.scalars().all()
                                if len(nav_rows_list) >= 2:
                                    first_value = nav_rows_list[0].adj_nav if nav_rows_list[0].adj_nav is not None else nav_rows_list[0].unit_nav
                                    last_value = nav_rows_list[-1].adj_nav if nav_rows_list[-1].adj_nav is not None else nav_rows_list[-1].unit_nav
                                    first_nav = float(first_value) if first_value is not None else 0
                                    last_nav = float(last_value) if last_value is not None else 0
                                    if first_nav > 0:
                                        portfolio_returns[code] = (last_nav - first_nav) / first_nav

                            if portfolio_returns:
                                n_funds = len(portfolio_returns)
                                benchmark_weights = {
                                    code: 1.0 / n_funds for code in portfolio_returns
                                }
                                total_invested = sum(fund_amounts.values()) if fund_amounts else 0
                                if total_invested > 0:
                                    portfolio_weights = {
                                        code: fund_amounts.get(code, 0) / total_invested
                                        for code in portfolio_returns
                                    }
                                else:
                                    portfolio_weights = benchmark_weights.copy()

                                brinson_input = {
                                    "portfolio_weights": portfolio_weights,
                                    "benchmark_weights": benchmark_weights,
                                    "portfolio_returns": portfolio_returns,
                                    "benchmark_returns": portfolio_returns,
                                }

                    perf_service = PerformanceService()
                    perf_report = perf_service.analyze(
                        nav=nav_series,
                        brinson_data=brinson_input,
                    )

                    attr_dict = perf_report.attribution.to_dict()
                    if attr_dict.get("fama_french") is not None:
                        ff = attr_dict["fama_french"]
                        fama_french = FamaFrenchAttribution(
                            alpha=float(ff.get("alpha", 0)),
                            beta_mkt=float(ff.get("betas", {}).get("MKT", 0)),
                            beta_smb=float(ff.get("betas", {}).get("SMB", 0)),
                            beta_hml=float(ff.get("betas", {}).get("HML", 0)),
                            beta_rmw=float(ff["betas"]["RMW"]) if ff.get("betas", {}).get("RMW") is not None else None,
                            beta_cma=float(ff["betas"]["CMA"]) if ff.get("betas", {}).get("CMA") is not None else None,
                            r_squared=float(ff.get("r_squared", 0)),
                        )
                    if attr_dict.get("brinson") is not None:
                        br = attr_dict["brinson"]
                        brinson = BrinsonAttribution(
                            allocation_effect=float(br.get("allocation_effect", {}).get("total", 0)),
                            selection_effect=float(br.get("selection_effect", {}).get("total", 0)),
                            interaction_effect=float(br.get("interaction_effect", {}).get("total", 0)),
                            total_excess=float(br.get("total_excess_return", 0)),
                        )

                    # Cache the computed attribution back to metrics
                    if brinson is not None or fama_french is not None:
                        updated_metrics = metrics.copy()
                        if brinson is not None:
                            updated_metrics["brinson"] = {
                                "allocation_effect": brinson.allocation_effect,
                                "selection_effect": brinson.selection_effect,
                                "interaction_effect": brinson.interaction_effect,
                                "total_excess": brinson.total_excess,
                            }
                        if fama_french is not None:
                            updated_metrics["fama_french"] = {
                                "alpha": fama_french.alpha,
                                "beta_mkt": fama_french.beta_mkt,
                                "beta_smb": fama_french.beta_smb,
                                "beta_hml": fama_french.beta_hml,
                                "beta_rmw": fama_french.beta_rmw,
                                "beta_cma": fama_french.beta_cma,
                                "r_squared": fama_french.r_squared,
                            }
                        run.metrics = updated_metrics
                        await db.commit()

        except (ImportError, Exception):
            pass

    return AttributionResponse(
        run_id=run_id,
        fama_french=fama_french,
        brinson=brinson,
    )


# ---------------------------------------------------------------------------
# Rolling metrics endpoint
# ---------------------------------------------------------------------------


class RollingMetricsResponse(BaseModel):
    """滚动指标响应。"""

    run_id: int
    dates: list[str] = Field(default_factory=list, description="日期序列")
    rolling_return: list[float] = Field(default_factory=list, description="20日滚动收益率")
    rolling_sharpe: list[float] = Field(default_factory=list, description="60日滚动Sharpe")
    rolling_drawdown: list[float] = Field(default_factory=list, description="当前回撤序列")
    rolling_volatility: list[float] = Field(default_factory=list, description="20日滚动波动率")
    monthly_returns: dict[str, float] = Field(default_factory=dict, description="月度收益率")
    yearly_returns: dict[str, float] = Field(default_factory=dict, description="年度收益率")


@router.get(
    "/{run_id}/rolling",
    response_model=RollingMetricsResponse,
    summary="滚动指标",
    description="获取回测的滚动收益、滚动 Sharpe、滚动波动率、回撤序列及月度/年度收益率。",
)
async def get_backtest_rolling(
    run_id: int,
    db: AsyncSession = Depends(get_session),
) -> RollingMetricsResponse:
    """Get rolling metrics for a backtest run."""
    # Verify run exists and is done
    result = await db.execute(
        select(BacktestRun).where(BacktestRun.id == run_id)
    )
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail=f"回测 {run_id} 不存在")

    if run.status != "done":
        raise HTTPException(
            status_code=400,
            detail=f"回测尚未完成，当前状态: {run.status}",
        )

    # Load equity curve
    equity_result = await db.execute(
        select(BacktestEquity)
        .where(BacktestEquity.run_id == run_id)
        .order_by(BacktestEquity.trade_date)
    )
    equity_rows = equity_result.scalars().all()

    if len(equity_rows) < 21:
        return RollingMetricsResponse(run_id=run_id)

    # Build EquitySnapshot list for compute_rolling_metrics
    from app.domain.backtest.engine_event import EquitySnapshot as EngineSnapshot
    from app.domain.backtest.result import compute_rolling_metrics

    snapshots = [
        EngineSnapshot(
            trade_date=row.trade_date,
            equity=row.equity or Decimal("0"),
            cash=row.cash or Decimal("0"),
            position_value=row.position_value or Decimal("0"),
        )
        for row in equity_rows
    ]

    rolling = compute_rolling_metrics(snapshots)
    if rolling is None:
        return RollingMetricsResponse(run_id=run_id)

    rolling_dict = rolling.to_dict()
    return RollingMetricsResponse(
        run_id=run_id,
        dates=rolling_dict["dates"],
        rolling_return=rolling_dict["rolling_return"],
        rolling_sharpe=rolling_dict["rolling_sharpe"],
        rolling_drawdown=rolling_dict["rolling_drawdown"],
        rolling_volatility=rolling_dict["rolling_volatility"],
        monthly_returns=rolling_dict["monthly_returns"],
        yearly_returns=rolling_dict["yearly_returns"],
    )


# ---------------------------------------------------------------------------
# Benchmark metrics endpoint
# ---------------------------------------------------------------------------


class BenchmarkMetricsResponse(BaseModel):
    """基准相对指标响应。"""

    run_id: int
    benchmark_code: str | None = None
    alpha: float | None = None
    beta: float | None = None
    information_ratio: float | None = None
    tracking_error: float | None = None
    treynor_ratio: float | None = None
    excess_return: float | None = None
    excess_annualized: float | None = None
    var_95: float | None = None
    cvar_95: float | None = None


@router.get(
    "/{run_id}/benchmark",
    response_model=BenchmarkMetricsResponse,
    summary="基准对比指标",
    description="获取回测相对于基准的 Alpha、Beta、信息比率等指标。",
)
async def get_backtest_benchmark(
    run_id: int,
    db: AsyncSession = Depends(get_session),
) -> BenchmarkMetricsResponse:
    """Get benchmark-relative metrics for a backtest run."""
    result = await db.execute(
        select(BacktestRun).where(BacktestRun.id == run_id)
    )
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail=f"回测 {run_id} 不存在")

    if run.status != "done":
        raise HTTPException(
            status_code=400,
            detail=f"回测尚未完成，当前状态: {run.status}",
        )

    # Check if benchmark metrics are already in metrics JSON
    metrics = run.metrics or {}
    bm_data = metrics.get("benchmark")

    if isinstance(bm_data, dict):
        return BenchmarkMetricsResponse(
            run_id=run_id,
            benchmark_code=bm_data.get("code"),
            alpha=bm_data.get("alpha"),
            beta=bm_data.get("beta"),
            information_ratio=bm_data.get("information_ratio"),
            tracking_error=bm_data.get("tracking_error"),
            treynor_ratio=bm_data.get("treynor_ratio"),
            excess_return=bm_data.get("excess_return"),
            excess_annualized=bm_data.get("excess_annualized"),
            var_95=bm_data.get("var_95"),
            cvar_95=bm_data.get("cvar_95"),
        )

    # Not pre-computed, return empty
    return BenchmarkMetricsResponse(run_id=run_id)


# ---------------------------------------------------------------------------
# Data quality check endpoint
# ---------------------------------------------------------------------------


class FundQualityItem(BaseModel):
    """单只基金数据质量。"""

    fund_code: str
    coverage_ratio: float
    total_trading_days: int
    available_days: int
    max_gap_days: int
    spike_count: int
    status: str


class DataQualityResponse(BaseModel):
    """数据质量检查响应。"""

    overall_status: str = Field(..., description="整体状态: good/warning/poor")
    can_proceed: bool = Field(..., description="是否可以继续回测")
    warnings: list[str] = Field(default_factory=list, description="警告信息")
    funds: list[FundQualityItem] = Field(default_factory=list, description="各基金质量")


@router.post(
    "/check-quality",
    response_model=DataQualityResponse,
    summary="数据质量检查",
    description="在提交回测前检查基金池数据质量，返回覆盖率、缺失、跳变等信息。",
)
async def check_data_quality(
    body: BacktestSubmit,
    db: AsyncSession = Depends(get_session),
) -> DataQualityResponse:
    """Pre-flight data quality check before submitting a backtest."""
    from sqlalchemy import and_

    from app.data.models.fund_nav import FundNav
    from app.data.models.strategies import Strategy
    from app.domain.backtest.calendar import trading_days as get_trading_days
    from app.domain.backtest.data_quality import check_backtest_data_quality

    # Load strategy
    strat_result = await db.execute(
        select(Strategy).where(Strategy.id == body.strategy_id)
    )
    strategy = strat_result.scalar_one_or_none()
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"策略 {body.strategy_id} 不存在")

    universe_codes = strategy.universe
    if isinstance(universe_codes, dict):
        universe_codes = universe_codes.get("fund_codes", [])

    if not universe_codes:
        return DataQualityResponse(
            overall_status="poor",
            can_proceed=False,
            warnings=["策略基金池为空"],
        )

    # Load NAV data
    nav_data: dict[str, dict] = {}
    for code in universe_codes:
        nav_stmt = select(FundNav).where(
            and_(
                FundNav.fund_code == code,
                FundNav.trade_date >= body.start_date,
                FundNav.trade_date <= body.end_date,
            )
        ).order_by(FundNav.trade_date)
        nav_result = await db.execute(nav_stmt)
        nav_rows = nav_result.scalars().all()
        nav_data[code] = {
            row.trade_date: (row.adj_nav if row.adj_nav is not None else row.unit_nav)
            for row in nav_rows
            if row.adj_nav is not None or row.unit_nav is not None
        }

    # Get trading days
    trade_days = get_trading_days(body.start_date, body.end_date)

    # Run quality check
    report = check_backtest_data_quality(nav_data, trade_days)

    return DataQualityResponse(
        overall_status=report.overall_status,
        can_proceed=report.can_proceed,
        warnings=report.warnings,
        funds=[
            FundQualityItem(
                fund_code=fq.fund_code,
                coverage_ratio=round(fq.coverage_ratio, 4),
                total_trading_days=fq.total_trading_days,
                available_days=fq.available_days,
                max_gap_days=fq.max_gap_days,
                spike_count=fq.spike_count,
                status=fq.status,
            )
            for fq in report.fund_reports
        ],
    )


# ---------------------------------------------------------------------------
# Strategy comparison endpoint
# ---------------------------------------------------------------------------


class CompareRequest(BaseModel):
    """策略对比请求。"""

    run_ids: list[int] = Field(..., min_length=2, max_length=10, description="回测 ID 列表（2-10个）")


class CompareItemResponse(BaseModel):
    """单个回测的对比数据。"""

    run_id: int
    strategy_name: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    total_return: float = 0.0
    annualized_return: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    volatility: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    win_rate: float = 0.0
    var_95: float = 0.0
    alpha: float | None = None
    beta: float | None = None
    information_ratio: float | None = None
    normalized_equity: list[list[Any]] = Field(default_factory=list, description="归一化净值 [[date, value], ...]")


class CompareResponse(BaseModel):
    """策略对比响应。"""

    items: list[CompareItemResponse]
    rankings: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    best_sharpe_run_id: int | None = None
    best_return_run_id: int | None = None
    lowest_drawdown_run_id: int | None = None


@router.post(
    "/compare",
    response_model=CompareResponse,
    summary="策略对比",
    description="对比多个回测结果的绩效指标和净值曲线。",
)
async def compare_backtests_endpoint(
    body: CompareRequest,
    db: AsyncSession = Depends(get_session),
) -> CompareResponse:
    """Compare multiple backtest results side by side."""
    from app.data.models.strategies import Strategy
    from app.services.backtest_compare import BacktestCompareItem, compare_backtests

    items: list[BacktestCompareItem] = []

    for run_id in body.run_ids:
        # Load run with strategy name
        stmt = (
            select(BacktestRun, Strategy.name.label("strategy_name"))
            .outerjoin(Strategy, BacktestRun.strategy_id == Strategy.id)
            .where(BacktestRun.id == run_id)
        )
        result = await db.execute(stmt)
        row = result.one_or_none()

        if row is None:
            continue

        run = row.BacktestRun
        if run.status != "done":
            continue

        metrics = run.metrics or {}
        benchmark = metrics.get("benchmark", {})

        # Load equity curve for normalized chart
        equity_result = await db.execute(
            select(BacktestEquity)
            .where(BacktestEquity.run_id == run_id)
            .order_by(BacktestEquity.trade_date)
        )
        equity_rows = equity_result.scalars().all()

        # Normalize equity (start = 1.0)
        normalized: list[tuple[str, float]] = []
        if equity_rows:
            initial = float(equity_rows[0].equity) if equity_rows[0].equity else 1
            if initial > 0:
                normalized = [
                    (r.trade_date.isoformat(), float(r.equity or 0) / initial)
                    for r in equity_rows
                ]

        item = BacktestCompareItem(
            run_id=run_id,
            strategy_name=row.strategy_name,
            start_date=run.start_date,
            end_date=run.end_date,
            total_return=metrics.get("total_return", 0) or 0,
            annualized_return=metrics.get("annualized_return", 0) or 0,
            sharpe=metrics.get("sharpe", 0) or 0,
            max_drawdown=metrics.get("max_drawdown", 0) or 0,
            volatility=metrics.get("volatility", 0) or 0,
            sortino=metrics.get("sortino", 0) or 0,
            calmar=metrics.get("calmar", 0) or 0,
            win_rate=metrics.get("win_rate", 0) or 0,
            var_95=metrics.get("var_95", 0) or 0,
            alpha=benchmark.get("alpha") if isinstance(benchmark, dict) else None,
            beta=benchmark.get("beta") if isinstance(benchmark, dict) else None,
            information_ratio=benchmark.get("information_ratio") if isinstance(benchmark, dict) else None,
            normalized_equity=normalized,
        )
        items.append(item)

    if len(items) < 2:
        raise HTTPException(
            status_code=400,
            detail="至少需要 2 个已完成的回测才能对比",
        )

    compare_result = compare_backtests(items)

    return CompareResponse(
        items=[
            CompareItemResponse(
                run_id=item.run_id,
                strategy_name=item.strategy_name,
                start_date=item.start_date.isoformat() if item.start_date else None,
                end_date=item.end_date.isoformat() if item.end_date else None,
                total_return=round(item.total_return, 6),
                annualized_return=round(item.annualized_return, 6),
                sharpe=round(item.sharpe, 4),
                max_drawdown=round(item.max_drawdown, 6),
                volatility=round(item.volatility, 6),
                sortino=round(item.sortino, 4),
                calmar=round(item.calmar, 4),
                win_rate=round(item.win_rate, 4),
                var_95=round(item.var_95, 6),
                alpha=round(item.alpha, 4) if item.alpha is not None else None,
                beta=round(item.beta, 4) if item.beta is not None else None,
                information_ratio=round(item.information_ratio, 4) if item.information_ratio is not None else None,
                normalized_equity=item.normalized_equity,
            )
            for item in compare_result.items
        ],
        rankings=compare_result.rankings,
        best_sharpe_run_id=compare_result.best_sharpe_run_id,
        best_return_run_id=compare_result.best_return_run_id,
        lowest_drawdown_run_id=compare_result.lowest_drawdown_run_id,
    )



# ---------------------------------------------------------------------------
# Sharpe inference endpoint (PSR / DSR / 95% CI)
# ---------------------------------------------------------------------------


class CPCVPathResponse(BaseModel):
    test_groups: list[int]
    train_groups: list[int]
    is_sharpe: float
    oos_sharpe: float
    is_return: float
    oos_return: float


class CPCVResponse(BaseModel):
    run_id: int
    pbo: float = Field(description="Probability of Backtest Overfitting")
    avg_oos_sharpe: float = 0.0
    std_oos_sharpe: float = 0.0
    avg_is_sharpe: float = 0.0
    n_paths: int = 0
    is_overfit: bool = False
    n_splits: int = 0
    n_test_splits: int = 0
    note: str | None = None
    paths: list[CPCVPathResponse] = Field(default_factory=list)


class SharpeInferenceResponse(BaseModel):
    """Sharpe statistical inference response.

    Attributes follow ``SharpeInferenceResult.to_dict()``.
    """

    run_id: int
    sharpe_observed: float | None = None
    sharpe_annualized: float | None = None
    n_observations: int = 0
    skewness: float | None = None
    excess_kurtosis: float | None = None
    psr: float | None = Field(
        None,
        description="Probabilistic Sharpe Ratio: probability that the true "
        "Sharpe exceeds 0",
    )
    dsr: float | None = Field(
        None,
        description="Deflated Sharpe Ratio: PSR adjusted for n_trials "
        "(multiple-testing correction)",
    )
    n_trials: int = 1
    psr_significant: bool = False
    dsr_significant: bool = False
    ci_lower: float | None = Field(None, description="95% lower bound of annualized Sharpe")
    ci_upper: float | None = Field(None, description="95% upper bound of annualized Sharpe")
    note: str | None = Field(None, description="Optional notes (e.g. fallback reasons)")


@router.get(
    "/{run_id}/cpcv",
    response_model=CPCVResponse,
    summary="CPCV / PBO 过拟合诊断",
    description=(
        "对已完成回测执行 Combinatorial Purged Cross-Validation，"
        "并输出 PBO（Probability of Backtest Overfitting）等稳健性诊断指标。"
    ),
)
async def get_backtest_cpcv(
    run_id: int,
    n_splits: int = Query(6, ge=3, le=10, description="时间分组数 N"),
    n_test_splits: int = Query(2, ge=1, le=5, description="每条路径的测试分组数 k"),
    purge_days: int = Query(0, ge=0, le=60, description="purge 天数"),
    embargo_days: int = Query(5, ge=0, le=60, description="embargo 天数"),
    max_paths: int | None = Query(20, ge=1, le=200, description="最多评估的组合数"),
    db: AsyncSession = Depends(get_session),
) -> CPCVResponse:
    if n_test_splits >= n_splits:
        raise HTTPException(status_code=422, detail="n_test_splits 必须小于 n_splits")
    return await _run_true_cpcv(
        run_id=run_id,
        n_splits=n_splits,
        n_test_splits=n_test_splits,
        purge_days=purge_days,
        embargo_days=embargo_days,
        max_paths=max_paths,
        db=db,
    )


@router.get(
    "/{run_id}/inference",
    response_model=SharpeInferenceResponse,
    summary="Sharpe 显著性推断 (PSR / DSR / 置信区间)",
    description=(
        "对回测结果的 Sharpe ratio 进行统计推断。返回：\n"
        "- **PSR (Probabilistic Sharpe Ratio)**: 真实 Sharpe > 0 的概率，"
        "考虑了样本量、收益分布偏度峰度。\n"
        "- **DSR (Deflated Sharpe Ratio)**: 校正了 ``n_trials`` 多重检验偏差的 PSR。"
        "如果你的策略是从 N 个参数试验中挑出来的，传入 N 让我们去除选择偏差。\n"
        "- **95% 置信区间**: Lo (2002) 闭式标准误。"
    ),
)
async def get_backtest_inference(
    run_id: int,
    n_trials: int = Query(1, ge=1, le=10000, description="多重检验试验数"),
    db: AsyncSession = Depends(get_session),
) -> SharpeInferenceResponse:
    """Compute PSR / DSR / CI for a completed backtest's Sharpe ratio."""
    result = await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"Backtest run {run_id} not found")
    if run.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"回测未完成 (status={run.status})，无法计算显著性",
        )

    # Pull equity curve to compute daily returns
    equity_result = await db.execute(
        select(BacktestEquity)
        .where(BacktestEquity.run_id == run_id)
        .order_by(BacktestEquity.trade_date)
    )
    equity_rows = equity_result.scalars().all()
    if len(equity_rows) < 30:
        return SharpeInferenceResponse(
            run_id=run_id,
            note="样本不足 (< 30 个观测), 无法做显著性推断",
        )

    equities = [float(row.equity) for row in equity_rows]
    daily_returns = [
        (equities[i] - equities[i - 1]) / equities[i - 1]
        for i in range(1, len(equities))
        if equities[i - 1] > 0
    ]
    if len(daily_returns) < 30:
        return SharpeInferenceResponse(
            run_id=run_id,
            note="有效日收益不足 (< 30), 无法做显著性推断",
        )

    try:
        import numpy as np

        from app.domain.performance.sharpe_inference import sharpe_inference

        inference = sharpe_inference(
            returns=np.asarray(daily_returns, dtype=float),
            n_trials=n_trials,
            variance_of_trials=None,  # 默认 1.0
            freq=252,
        )
        if inference is None:
            return SharpeInferenceResponse(
                run_id=run_id,
                note="无法计算 Sharpe 推断 (返回波动率为零)",
            )

        d = inference.to_dict()
        return SharpeInferenceResponse(
            run_id=run_id,
            sharpe_observed=d.get("sharpe_observed"),
            sharpe_annualized=d.get("sharpe_annualized"),
            n_observations=d.get("n_observations", 0),
            skewness=d.get("skewness"),
            excess_kurtosis=d.get("excess_kurtosis"),
            psr=d.get("psr"),
            dsr=d.get("dsr"),
            n_trials=d.get("n_trials", n_trials),
            psr_significant=d.get("psr_significant", False),
            dsr_significant=d.get("dsr_significant", False),
            ci_lower=d.get("ci_lower"),
            ci_upper=d.get("ci_upper"),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Sharpe 推断计算失败: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Walk-Forward 验证端点
# ---------------------------------------------------------------------------


class WalkForwardWindowResponse(BaseModel):
    """单个 Walk-Forward 窗口结果。"""

    window_id: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    is_sharpe: float
    oos_sharpe: float
    is_return: float
    oos_return: float
    is_max_drawdown: float
    oos_max_drawdown: float


class WalkForwardResponse(BaseModel):
    """Walk-Forward 验证响应。"""

    run_id: int
    wfe: float = Field(description="Walk-Forward Efficiency (OOS Sharpe / IS Sharpe)")
    avg_oos_sharpe: float = Field(description="平均样本外 Sharpe")
    avg_is_sharpe: float = Field(description="平均样本内 Sharpe")
    avg_oos_return: float = Field(description="平均样本外收益率")
    oos_win_rate: float = Field(description="样本外胜率（收益为正的窗口比例）")
    total_oos_return: float = Field(description="累计样本外收益率")
    is_robust: bool = Field(description="策略是否稳健 (WFE > 0.5 且 OOS 胜率 > 50%)")
    windows: list[WalkForwardWindowResponse] = Field(default_factory=list)
    note: str | None = None


@router.get(
    "/{run_id}/walk-forward",
    response_model=WalkForwardResponse,
    summary="Walk-Forward 验证状态/保护提示",
    description="兼容 GET 的轻量入口，避免误触发重型同步计算；请用 POST 执行实际验证。",
)
async def get_walk_forward_hint(run_id: int) -> WalkForwardResponse:
    return WalkForwardResponse(
        run_id=run_id,
        wfe=0.0,
        avg_oos_sharpe=0.0,
        avg_is_sharpe=0.0,
        avg_oos_return=0.0,
        oos_win_rate=0.0,
        total_oos_return=0.0,
        is_robust=False,
        windows=[],
        note="GET 仅返回轻量提示，不触发 Walk-Forward 重型计算；请使用 POST 并设置合适的 max_trials 执行验证",
    )


@router.post(
    "/{run_id}/walk-forward",
    response_model=WalkForwardResponse,
    summary="Walk-Forward 验证",
    description=(
        "对已完成的回测执行 Walk-Forward 前推验证，评估策略的样本外表现和过拟合风险。"
        "将回测期分为多个滚动窗口（训练期 + 验证期），分别计算 IS/OOS 指标，"
        "最终输出 WFE（Walk-Forward Efficiency）。WFE > 0.5 表示策略具有一定稳健性。"
    ),
)
async def run_walk_forward_validation(
    run_id: int,
    train_months: int = Query(default=12, ge=3, le=60, description="训练窗口月数"),
    test_months: int = Query(default=3, ge=1, le=12, description="验证窗口月数"),
    step_months: int = Query(default=3, ge=1, le=12, description="步进月数"),
    max_trials: int = Query(default=200, ge=1, le=5000, description="同步执行允许的最大参数组合数"),
    db: AsyncSession = Depends(get_session),
) -> WalkForwardResponse:
    """Run Walk-Forward validation on a completed backtest.

    This endpoint takes the equity curve from a completed backtest and
    splits it into rolling train/test windows to evaluate out-of-sample
    performance and overfitting risk.
    """
    return await _run_true_walk_forward(
        run_id=run_id,
        train_months=train_months,
        test_months=test_months,
        step_months=step_months,
        db=db,
        max_trials=max_trials,
    )
