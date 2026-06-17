"""Meta endpoints exposed under the v1 prefix.

These are lightweight informational endpoints (version, configuration
echo for debugging, …). The top-level `/health` endpoint lives in
`app.main` because the Dockerfile healthcheck and the compose probes
hit it without the `/api/v1` prefix.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.core.config import Settings, get_settings
from app.data.session import get_session
from app.domain.performance.metrics import METRIC_VERSION

router = APIRouter(tags=["meta"])


class VersionResponse(BaseModel):
    """Build/version information surfaced under `/api/v1/version`."""

    name: str
    version: str
    environment: str


class MetricDefinition(BaseModel):
    """统一指标口径说明。"""

    key: str
    name: str
    formula: str
    annualization: str | None = None
    sign: str
    insufficient_data: str
    usage: str


class MetricDefinitionsResponse(BaseModel):
    """Metric definition catalog for frontend/help pages."""

    metric_version: str
    frequency: int
    definitions: list[MetricDefinition]


@router.get("/version", response_model=VersionResponse, summary="Build version")
async def version(settings: Settings = Depends(get_settings)) -> VersionResponse:
    """Return the backend build info.

    Useful for smoke tests and frontend "about" dialogs.
    """
    return VersionResponse(
        name=settings.app_name,
        version=__version__,
        environment=settings.app_env,
    )


@router.get(
    "/meta/metric-definitions",
    response_model=MetricDefinitionsResponse,
    summary="指标口径说明",
    description="返回核心收益、风险和风险调整指标的公式、符号和数据不足处理规则。",
)
async def metric_definitions() -> MetricDefinitionsResponse:
    """Return stable metric definitions for UI explanations and audits."""
    return MetricDefinitionsResponse(
        metric_version=METRIC_VERSION,
        frequency=252,
        definitions=[
            MetricDefinition(
                key="total_return",
                name="总收益率",
                formula="end_nav / start_nav - 1",
                annualization=None,
                sign="正数表示盈利，负数表示亏损",
                insufficient_data="领域计算返回 NaN；回测空结果展示为 0.0",
                usage="衡量完整区间累计收益",
            ),
            MetricDefinition(
                key="annualized_return",
                name="年化收益率",
                formula="(end_nav / start_nav) ** (252 / (n_points - 1)) - 1",
                annualization="使用 n_points - 1 个收益区间，而不是权益点数量",
                sign="正数表示年化盈利，负数表示年化亏损",
                insufficient_data="领域计算返回 NaN；回测空结果展示为 0.0",
                usage="跨不同持有期比较收益水平",
            ),
            MetricDefinition(
                key="volatility",
                name="年化波动率",
                formula="std(period_returns, ddof=1) * sqrt(252)",
                annualization="样本标准差乘以 sqrt(252)",
                sign="非负数",
                insufficient_data="少于 2 个收益点时领域计算返回 NaN；回测展示为 0.0",
                usage="衡量收益波动风险",
            ),
            MetricDefinition(
                key="max_drawdown",
                name="最大回撤",
                formula="min(nav / cummax(nav) - 1)",
                annualization=None,
                sign="因子/回测结果层为负数或 0；向量化研究回测保留正数回撤幅度",
                insufficient_data="领域计算返回 NaN；回测空结果展示为 0.0",
                usage="衡量从峰值到谷值的最大损失",
            ),
            MetricDefinition(
                key="sharpe",
                name="夏普比率",
                formula="mean(period_returns - rf_daily) / std(period_returns, ddof=1) * sqrt(252)",
                annualization="rf_daily = (1 + annual_rf) ** (1 / 252) - 1",
                sign="正数表示单位总波动获得正超额收益，负数相反",
                insufficient_data="领域计算返回 NaN；回测展示为 0.0",
                usage="比较总波动调整后的收益表现",
            ),
            MetricDefinition(
                key="sortino",
                name="索提诺比率",
                formula="mean(excess_returns) * 252 / downside_deviation_annualized",
                annualization="下行偏差使用全样本分母：sqrt(mean(min(r_i - target, 0)^2)) * sqrt(252)",
                sign="正数表示单位下行风险获得正超额收益，负数相反",
                insufficient_data="无下行风险或数据不足时领域计算返回 NaN；回测展示为 0.0",
                usage="比较仅按下行风险调整后的收益表现",
            ),
            MetricDefinition(
                key="calmar",
                name="卡尔玛比率",
                formula="annualized_return / abs(max_drawdown)",
                annualization="年化收益使用 n_points - 1 个收益区间",
                sign="正数表示正收益覆盖回撤，负数表示负收益伴随回撤",
                insufficient_data="无回撤或数据不足时领域计算返回 NaN；回测展示为 0.0",
                usage="比较收益相对最大回撤的补偿",
            ),
            MetricDefinition(
                key="win_rate",
                name="日频胜率",
                formula="count(period_returns > 0) / count(period_returns)",
                annualization=None,
                sign="0 到 1 之间，越高表示正收益交易日占比越高",
                insufficient_data="无收益点时返回 0.0",
                usage="衡量组合权益曲线日收益为正的频率，优先用于基金组合回测解释",
            ),
            MetricDefinition(
                key="profit_factor",
                name="日频盈亏比",
                formula="sum(positive_period_returns) / abs(sum(negative_period_returns))",
                annualization=None,
                sign="非负数；无亏损且有盈利时返回 inf，无盈利时返回 0.0",
                insufficient_data="无收益点时返回 0.0",
                usage="衡量组合日收益盈利总额相对亏损总额的比例",
            ),
            MetricDefinition(
                key="cashflow_win_rate_estimate",
                name="现金流估算胜率",
                formula="count(estimated_cashflow_pnl > 0) / count(estimated_cashflow_pnl)",
                annualization=None,
                sign="0 到 1 之间，仅表示现金流估算盈利基金占比",
                insufficient_data="无可估算现金流闭环时返回 0.0",
                usage="按基金代码聚合现金流估算，非严格逐笔配对；已降级为辅助指标，不应作为专业交易胜率展示",
            ),
            MetricDefinition(
                key="cashflow_profit_factor_estimate",
                name="现金流估算盈亏比",
                formula="sum(positive_estimated_cashflow_pnl) / abs(sum(negative_estimated_cashflow_pnl))",
                annualization=None,
                sign="非负数；无估算亏损且有盈利时返回 inf，无盈利时返回 0.0",
                insufficient_data="无可估算现金流闭环时返回 0.0",
                usage="按基金代码聚合现金流估算，不能等同于严格 lot-level 已实现交易盈亏比",
            ),
            MetricDefinition(
                key="trade_win_rate",
                name="交易近似胜率（已弃用）",
                formula="cashflow_win_rate_estimate",
                annualization=None,
                sign="兼容旧字段；同现金流估算胜率",
                insufficient_data="无可估算现金流闭环时返回 0.0",
                usage="deprecated：请使用 cashflow_win_rate_estimate；非严格逐笔配对",
            ),
            MetricDefinition(
                key="trade_profit_factor",
                name="交易近似盈亏比（已弃用）",
                formula="cashflow_profit_factor_estimate",
                annualization=None,
                sign="兼容旧字段；同现金流估算盈亏比",
                insufficient_data="无可估算现金流闭环时返回 0.0",
                usage="deprecated：请使用 cashflow_profit_factor_estimate；不能等同于严格逐笔交易盈亏比",
            ),
            MetricDefinition(
                key="var_95",
                name="95% VaR",
                formula="-sorted(period_returns)[int((1 - confidence) * n)]",
                annualization=None,
                sign="统一为正数损失；0 表示没有尾部损失，正数表示估计损失幅度",
                insufficient_data="绩效工具少于 10 个收益点返回 NaN；风险因子少于 2 个收益点返回 NaN；回测展示为 0.0",
                usage="估计指定置信度下的单期尾部损失",
            ),
            MetricDefinition(
                key="cvar_95",
                name="95% CVaR / Expected Shortfall",
                formula="-mean(sorted(period_returns)[:max(1, int((1 - confidence) * n))])",
                annualization=None,
                sign="统一为正数损失；0 表示没有尾部损失，正数表示平均尾部损失幅度",
                insufficient_data="绩效工具少于 10 个收益点返回 NaN；风险因子少于 2 个收益点返回 NaN；回测展示为 0.0",
                usage="估计超过 VaR 阈值后的平均尾部损失",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 平台概览统计
# ---------------------------------------------------------------------------


class RecentBacktestItem(BaseModel):
    """最近回测摘要。"""

    run_id: int
    strategy_name: str | None = None
    status: str
    total_return: float | None = None
    sharpe: float | None = None
    max_drawdown: float | None = None
    finished_at: str | None = None
    warnings: list[str] = Field(default_factory=list)


class DashboardStats(BaseModel):
    """概览页统计数据。"""

    fund_count: int = 0
    strategy_count: int = 0
    backtest_count: int = 0
    nav_latest_date: str | None = None
    nav_total_records: int = 0
    recent_backtests: list[RecentBacktestItem] = []


def _backtest_warnings(metrics: dict | None) -> list[str]:
    if not isinstance(metrics, dict):
        return []
    warnings: list[str] = []
    try:
        if float(metrics.get("max_drawdown")) <= -0.9999:
            warnings.append("历史回测疑似由旧版权益曲线生成，最大回撤可能失真，建议重新运行回测")
    except (TypeError, ValueError):
        pass
    return warnings


@router.get(
    "/dashboard",
    response_model=DashboardStats,
    summary="平台概览统计",
    description="返回基金数量、策略数量、回测次数、最新数据日期等概览信息。",
)
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_session),
) -> DashboardStats:
    """Get platform overview statistics."""
    from app.data.models.backtests import BacktestRun
    from app.data.models.fund_nav import FundNav
    from app.data.models.funds import Fund
    from app.data.models.strategies import Strategy

    # 基金总数
    fund_result = await db.execute(
        select(func.count()).select_from(Fund).where(Fund.status == "active")
    )
    fund_count = fund_result.scalar_one()

    # 策略数量
    strategy_result = await db.execute(select(func.count()).select_from(Strategy))
    strategy_count = strategy_result.scalar_one()

    # 回测次数
    backtest_result = await db.execute(select(func.count()).select_from(BacktestRun))
    backtest_count = backtest_result.scalar_one()

    # 最新净值日期和总记录数
    nav_date_result = await db.execute(select(func.max(FundNav.trade_date)))
    nav_latest_date = nav_date_result.scalar_one_or_none()

    nav_count_result = await db.execute(select(func.count()).select_from(FundNav))
    nav_total_records = nav_count_result.scalar_one()

    # 最近5次回测
    recent_stmt = (
        select(BacktestRun, Strategy.name.label("strategy_name"))
        .outerjoin(Strategy, BacktestRun.strategy_id == Strategy.id)
        .order_by(BacktestRun.id.desc())
        .limit(5)
    )
    recent_result = await db.execute(recent_stmt)
    recent_rows = recent_result.all()

    recent_backtests = []
    for row in recent_rows:
        bt = row.BacktestRun
        metrics = bt.metrics or {}
        recent_backtests.append(
            RecentBacktestItem(
                run_id=bt.id,
                strategy_name=row.strategy_name,
                status=bt.status or "pending",
                total_return=metrics.get("total_return"),
                sharpe=metrics.get("sharpe"),
                max_drawdown=metrics.get("max_drawdown"),
                finished_at=bt.finished_at.isoformat() if bt.finished_at else None,
                warnings=_backtest_warnings(metrics),
            )
        )

    return DashboardStats(
        fund_count=fund_count,
        strategy_count=strategy_count,
        backtest_count=backtest_count,
        nav_latest_date=nav_latest_date.isoformat() if nav_latest_date else None,
        nav_total_records=nav_total_records,
        recent_backtests=recent_backtests,
    )
