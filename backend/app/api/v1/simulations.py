"""Monte Carlo Simulation API endpoints.

Provides:
- ``POST /simulations``              — submit a new simulation
- ``GET /simulations``               — list simulation records
- ``GET /simulations/{id}``          — get simulation status/result
- ``GET /simulations/{id}/paths``    — get percentile paths for fan chart
- ``DELETE /simulations/{id}``       — delete a simulation record

Simulation tasks are dispatched to Celery for async execution.
Progress is written to Redis and pushed via WebSocket.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.data.models.simulations import SimulationRun
from app.data.session import get_session

router = APIRouter(prefix="/simulations", tags=["simulations"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class SimulationSubmit(BaseModel):
    """Request body for submitting a simulation."""

    strategy_id: int = Field(..., description="策略 ID")
    horizon_days: int = Field(
        default=252,
        ge=20,
        le=1260,
        description="预测期限（交易日），默认 252（约1年）",
    )
    num_simulations: int = Field(
        default=10000,
        ge=1000,
        le=100000,
        description="模拟路径数量",
    )
    method: str = Field(
        default="gbm",
        description="模拟方法: gbm（几何布朗运动）/ bootstrap（自助法）/ hybrid（混合）",
    )
    initial_capital: Decimal = Field(
        default=Decimal("100000"),
        gt=0,
        description="初始资金",
    )
    confidence_levels: list[float] = Field(
        default=[0.95, 0.99],
        description="VaR/CVaR 置信水平",
    )
    target_return: float | None = Field(
        default=None,
        description="目标收益率（如 0.15 表示 15%），用于计算达成概率",
    )
    lookback_days: int = Field(
        default=504,
        ge=60,
        le=2520,
        description="历史回看天数（用于参数估计），默认 504（约2年）",
    )


class SimulationSubmitResponse(BaseModel):
    """Response for simulation submission (202 Accepted)."""

    run_id: int = Field(..., description="模拟运行 ID")
    status: str = Field(default="pending", description="初始状态")
    message: str = Field(default="模拟任务已提交", description="提示信息")


class SimulationStatusResponse(BaseModel):
    """Response for simulation status query."""

    id: int = Field(..., description="模拟运行 ID")
    strategy_id: int | None = Field(None, description="策略 ID")
    strategy_name: str | None = Field(None, description="策略名称")
    horizon_days: int = Field(..., description="预测期限")
    num_simulations: int = Field(..., description="模拟路径数")
    method: str = Field(..., description="模拟方法")
    initial_capital: str | None = Field(None, description="初始资金")
    target_return: float | None = Field(None, description="目标收益率")
    status: str | None = Field(None, description="状态: pending/running/done/failed")
    progress: float | None = Field(None, description="进度百分比 0-100")
    progress_message: str | None = Field(None, description="当前阶段进度说明")
    metrics: dict[str, Any] | None = Field(None, description="模拟结果指标")
    nav_data_stale: dict[str, Any] | None = Field(None, description="NAV 复权口径变更导致结果可能过期的提示")
    nav_quality_warning: dict[str, Any] | None = Field(None, description="NAV 数据源口径混用或质量问题提示")
    error_msg: str | None = Field(None, description="错误信息")
    started_at: datetime | None = Field(None, description="开始时间")
    finished_at: datetime | None = Field(None, description="结束时间")
    created_at: datetime | None = Field(None, description="创建时间")


class PercentilePathsResponse(BaseModel):
    """Response for percentile paths (fan chart data)."""

    run_id: int
    horizon_days: int
    initial_capital: float
    paths: dict[str, list[float]] = Field(
        ..., description="分位数路径: p5/p10/p25/p50/p75/p90/p95"
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _clear_live_progress(run_id: int, settings: Settings) -> None:
    """清理模拟任务遗留的 Redis 实时进度缓存。"""
    try:
        import redis

        redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            redis_client.delete(f"simulation:progress:{run_id}")
        finally:
            redis_client.close()
    except Exception:
        pass



def _build_strategy_snapshot(strategy: Any) -> dict[str, Any]:
    """Capture the strategy configuration at simulation submission time."""
    return {
        "id": strategy.id,
        "name": strategy.name,
        "strategy_type": strategy.strategy_type,
        "params": strategy.params or {},
        "universe": strategy.universe,
        "benchmark": strategy.benchmark,
    }



def _resolve_strategy_name(run: SimulationRun, joined_name: str | None) -> str | None:
    """Prefer the persisted snapshot name over the live strategy relation."""
    snapshot = run.strategy_snapshot if isinstance(run.strategy_snapshot, dict) else None
    snapshot_name = snapshot.get("name") if snapshot else None
    if isinstance(snapshot_name, str) and snapshot_name.strip():
        return snapshot_name.strip()
    if isinstance(joined_name, str) and joined_name.strip():
        return joined_name.strip()

    metrics = run.metrics if isinstance(run.metrics, dict) else None
    metrics_name = metrics.get("strategy_name") if metrics else None
    if isinstance(metrics_name, str) and metrics_name.strip():
        return metrics_name.strip()

    if run.strategy_id is not None:
        return f"已删除策略 #{run.strategy_id}"
    return None



def _merge_live_progress(
    run: SimulationRun,
    settings: Settings,
) -> tuple[str | None, float | None, str | None, str | None]:
    """合并 Redis 实时状态与数据库中的持久状态。"""
    status = run.status or "pending"
    progress = float(run.progress) if run.progress is not None else 0.0
    progress_message: str | None = None
    error_msg = run.error_msg

    if status not in {"pending", "running"}:
        return status, progress, progress_message, error_msg

    try:
        import redis

        redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            progress_data = redis_client.get(f"simulation:progress:{run.id}")
        finally:
            redis_client.close()
    except Exception:
        return status, progress, progress_message, error_msg

    if not progress_data:
        return status, progress, progress_message, error_msg

    try:
        data = json.loads(progress_data)
    except (TypeError, ValueError, json.JSONDecodeError):
        return status, progress, progress_message, error_msg

    live_progress = data.get("progress")
    live_status = data.get("status")
    live_message = data.get("message")

    if isinstance(live_progress, (int, float)):
        progress = float(live_progress)
    if isinstance(live_status, str):
        status = live_status
    if isinstance(live_message, str) and live_message:
        progress_message = live_message
        if status == "failed":
            error_msg = live_message

    return status, progress, progress_message, error_msg



def _reset_simulation_run(run: SimulationRun) -> None:
    """重置模拟记录，供重复提交或重跑前复用。"""
    run.status = "pending"
    run.progress = Decimal("0")
    run.metrics = None
    run.percentile_paths = None
    run.error_msg = None
    run.started_at = None
    run.finished_at = None


@router.get(
    "",
    response_model=list[SimulationStatusResponse],
    summary="列出所有模拟",
)
async def list_simulations(
    strategy_id: int | None = Query(None, description="按策略 ID 筛选"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[SimulationStatusResponse]:
    """获取模拟记录列表。"""
    from app.data.models.strategies import Strategy

    settings = get_settings()
    stmt = (
        select(SimulationRun, Strategy.name.label("strategy_name"))
        .outerjoin(Strategy, SimulationRun.strategy_id == Strategy.id)
    )

    if strategy_id is not None:
        stmt = stmt.where(SimulationRun.strategy_id == strategy_id)

    stmt = stmt.order_by(SimulationRun.id.desc()).limit(limit)

    result = await session.execute(stmt)
    rows = result.all()

    items: list[SimulationStatusResponse] = []
    for r in rows:
        status, progress, progress_message, error_msg = _merge_live_progress(
            r.SimulationRun,
            settings,
        )
        items.append(
            SimulationStatusResponse(
                id=r.SimulationRun.id,
                strategy_id=r.SimulationRun.strategy_id,
                strategy_name=_resolve_strategy_name(r.SimulationRun, r.strategy_name),
                horizon_days=r.SimulationRun.horizon_days,
                num_simulations=r.SimulationRun.num_simulations,
                method=r.SimulationRun.method,
                initial_capital=(
                    str(r.SimulationRun.initial_capital)
                    if r.SimulationRun.initial_capital is not None
                    else None
                ),
                target_return=(
                    float(r.SimulationRun.target_return)
                    if r.SimulationRun.target_return is not None
                    else None
                ),
                status=status,
                progress=progress,
                progress_message=progress_message,
                metrics=r.SimulationRun.metrics,
                nav_data_stale=(r.SimulationRun.metrics or {}).get("nav_data_stale") if isinstance(r.SimulationRun.metrics, dict) else None,
                nav_quality_warning=(r.SimulationRun.metrics or {}).get("nav_quality_warning") if isinstance(r.SimulationRun.metrics, dict) else None,
                error_msg=error_msg,
                started_at=r.SimulationRun.started_at,
                finished_at=r.SimulationRun.finished_at,
                created_at=r.SimulationRun.created_at,
            )
        )
    return items


@router.post(
    "",
    response_model=SimulationSubmitResponse,
    status_code=202,
    summary="提交模拟预测",
    description="提交 Monte Carlo 模拟任务，异步执行。返回 run_id 用于后续查询状态和结果。",
)
async def submit_simulation(
    body: SimulationSubmit,
    db: AsyncSession = Depends(get_session),
) -> SimulationSubmitResponse:
    """Submit a new Monte Carlo simulation."""
    settings = get_settings()

    # Validate method
    valid_methods = {"gbm", "bootstrap", "hybrid"}
    if body.method not in valid_methods:
        raise HTTPException(
            status_code=422,
            detail=f"method 必须为 {valid_methods} 之一",
        )

    # Validate confidence levels
    for cl in body.confidence_levels:
        if not (0.5 < cl < 1.0):
            raise HTTPException(
                status_code=422,
                detail=f"confidence_level {cl} 必须在 (0.5, 1.0) 范围内",
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

    strategy_snapshot = _build_strategy_snapshot(strategy)

    # 查找是否已有相同参数的模拟记录（同策略、同方法、同期限、同资金）
    from sqlalchemy import and_

    existing_result = await db.execute(
        select(SimulationRun)
        .where(
            and_(
                SimulationRun.strategy_id == body.strategy_id,
                SimulationRun.method == body.method,
                SimulationRun.horizon_days == body.horizon_days,
                SimulationRun.initial_capital == body.initial_capital,
            )
        )
        .order_by(SimulationRun.id.asc())
    )
    existing_runs = existing_result.scalars().all()

    if existing_runs:
        # 保留第一条，删除其余重复记录
        sim_run = existing_runs[0]
        for dup in existing_runs[1:]:
            await db.delete(dup)

        # 覆盖保留记录：重置状态，更新参数
        sim_run.num_simulations = body.num_simulations
        sim_run.confidence_levels = body.confidence_levels
        sim_run.target_return = Decimal(str(body.target_return)) if body.target_return is not None else None
        sim_run.lookback_days = body.lookback_days
        sim_run.strategy_snapshot = strategy_snapshot
        _reset_simulation_run(sim_run)
        await db.commit()
        _clear_live_progress(sim_run.id, settings)
        await db.refresh(sim_run)
    else:
        # 新建模拟记录
        sim_run = SimulationRun(
            strategy_id=body.strategy_id,
            horizon_days=body.horizon_days,
            num_simulations=body.num_simulations,
            method=body.method,
            initial_capital=body.initial_capital,
            confidence_levels=body.confidence_levels,
            strategy_snapshot=strategy_snapshot,
            target_return=Decimal(str(body.target_return)) if body.target_return is not None else None,
            lookback_days=body.lookback_days,
            status="pending",
        )
        db.add(sim_run)
        await db.commit()
        await db.refresh(sim_run)

    # Dispatch Celery task
    from app.tasks.simulation import run_simulation

    run_simulation.delay(sim_run.id)

    return SimulationSubmitResponse(
        run_id=sim_run.id,
        status="pending",
        message="模拟任务已提交，正在异步执行",
    )


@router.post(
    "/{run_id}/rerun",
    response_model=SimulationSubmitResponse,
    status_code=202,
    summary="重新运行模拟",
    description="按模拟 ID 重新运行指定记录，并覆盖该记录原有结果。",
)
async def rerun_simulation(
    run_id: int,
    db: AsyncSession = Depends(get_session),
) -> SimulationSubmitResponse:
    """Rerun an existing simulation in place by run id."""
    settings = get_settings()

    result = await db.execute(select(SimulationRun).where(SimulationRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"模拟 {run_id} 不存在")

    if run.status in {"pending", "running"}:
        raise HTTPException(
            status_code=409,
            detail=f"模拟 {run_id} 当前状态为 {run.status}，不可重复启动",
        )

    _reset_simulation_run(run)
    await db.commit()
    _clear_live_progress(run_id, settings)
    await db.refresh(run)

    from app.tasks.simulation import run_simulation

    run_simulation.delay(run.id)

    return SimulationSubmitResponse(
        run_id=run.id,
        status="pending",
        message="模拟任务已重新提交，正在异步执行",
    )


@router.get(
    "/{run_id}",
    response_model=SimulationStatusResponse,
    summary="查询模拟状态/结果",
)
async def get_simulation(
    run_id: int,
    session: AsyncSession = Depends(get_session),
) -> SimulationStatusResponse:
    """获取指定模拟的状态和结果。"""
    from app.data.models.strategies import Strategy

    settings = get_settings()
    stmt = (
        select(SimulationRun, Strategy.name.label("strategy_name"))
        .outerjoin(Strategy, SimulationRun.strategy_id == Strategy.id)
        .where(SimulationRun.id == run_id)
    )
    result = await session.execute(stmt)
    row = result.one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail=f"模拟 {run_id} 不存在")

    r = row
    status, progress, progress_message, error_msg = _merge_live_progress(
        r.SimulationRun,
        settings,
    )

    return SimulationStatusResponse(
        id=r.SimulationRun.id,
        strategy_id=r.SimulationRun.strategy_id,
        strategy_name=_resolve_strategy_name(r.SimulationRun, r.strategy_name),
        horizon_days=r.SimulationRun.horizon_days,
        num_simulations=r.SimulationRun.num_simulations,
        method=r.SimulationRun.method,
        initial_capital=(
            str(r.SimulationRun.initial_capital)
            if r.SimulationRun.initial_capital is not None
            else None
        ),
        target_return=(
            float(r.SimulationRun.target_return)
            if r.SimulationRun.target_return is not None
            else None
        ),
        status=status,
        progress=progress,
        progress_message=progress_message,
        metrics=r.SimulationRun.metrics,
        nav_data_stale=(r.SimulationRun.metrics or {}).get("nav_data_stale") if isinstance(r.SimulationRun.metrics, dict) else None,
        nav_quality_warning=(r.SimulationRun.metrics or {}).get("nav_quality_warning") if isinstance(r.SimulationRun.metrics, dict) else None,
        error_msg=error_msg,
        started_at=r.SimulationRun.started_at,
        finished_at=r.SimulationRun.finished_at,
        created_at=r.SimulationRun.created_at,
    )


@router.get(
    "/{run_id}/paths",
    response_model=PercentilePathsResponse,
    summary="获取分位数路径（扇形图数据）",
)
async def get_simulation_paths(
    run_id: int,
    session: AsyncSession = Depends(get_session),
) -> PercentilePathsResponse:
    """获取模拟的分位数路径数据，用于前端扇形图可视化。"""
    stmt = select(SimulationRun).where(SimulationRun.id == run_id)
    result = await session.execute(stmt)
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail=f"模拟 {run_id} 不存在")

    settings = get_settings()
    status, _, _, _ = _merge_live_progress(run, settings)
    if status != "done":
        raise HTTPException(
            status_code=400,
            detail=f"模拟尚未完成，当前状态: {status}",
        )

    if not run.percentile_paths:
        raise HTTPException(
            status_code=404,
            detail="分位数路径数据不存在，请重新运行该模拟以生成扇形图数据",
        )

    return PercentilePathsResponse(
        run_id=run.id,
        horizon_days=run.horizon_days,
        initial_capital=float(run.initial_capital or 100000),
        paths=run.percentile_paths,
    )


@router.delete(
    "/{run_id}",
    status_code=204,
    summary="删除模拟记录",
)
async def delete_simulation(
    run_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """删除指定的模拟记录。"""
    settings = get_settings()
    stmt = select(SimulationRun).where(SimulationRun.id == run_id)
    result = await session.execute(stmt)
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail=f"模拟 {run_id} 不存在")

    await session.delete(run)
    await session.commit()
    _clear_live_progress(run_id, settings)
