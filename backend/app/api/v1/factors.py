"""Factor computation API endpoints.

Provides:
- ``GET /factors`` — list all registered factors with metadata
- ``POST /factors/compute`` — batch compute factors for given funds
- ``GET /funds/{code}/factors`` — compute all factors for a single fund

All endpoints use Pydantic v2 response models with full OpenAPI documentation
(requirements 7.2, 7.6).
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.session import get_session

from app.domain.factors import list_factors
from app.services.factor_service import FactorEngine, Frequency

router = APIRouter(tags=["factors"])


# ---------------------------------------------------------------------------
# Response / Request models (Pydantic v2)
# ---------------------------------------------------------------------------


class FactorMeta(BaseModel):
    """Metadata for a single registered factor."""

    name: str = Field(..., description="因子名称")
    category: str = Field(..., description="因子类别 (return/risk/risk_adjusted/benchmark/holding/manager)")
    window: int | None = Field(None, description="所需最小窗口长度（周期数）")
    return_type: str = Field(..., description="返回类型 (scalar/series)")
    description: str = Field("", description="因子描述")


class FactorListResponse(BaseModel):
    """Response for the factor metadata list endpoint."""

    total: int = Field(..., description="因子总数")
    factors: list[FactorMeta] = Field(..., description="因子元数据列表")


class FactorComputeRequest(BaseModel):
    """Request body for batch factor computation."""

    fund_codes: list[str] = Field(
        ..., min_length=1, description="基金代码列表"
    )
    factor_names: list[str] = Field(
        ..., min_length=1, description="因子名称列表"
    )
    window: int | None = Field(None, ge=1, description="滚动窗口长度（周期数）")
    freq: Frequency = Field(default="daily", description="计算频率 (daily/weekly/monthly)")
    benchmark_code: str | None = Field(None, description="基准代码（用于基准相关因子）")


class FactorValue(BaseModel):
    """A single factor value for a fund."""

    fund_code: str = Field(..., description="基金代码")
    factor_name: str = Field(..., description="因子名称")
    value: float | None = Field(None, description="因子值（NaN 表示为 null）")


class FactorComputeResponse(BaseModel):
    """Response for batch factor computation."""

    fund_codes: list[str] = Field(..., description="基金代码列表")
    factor_names: list[str] = Field(..., description="因子名称列表")
    window: int | None = Field(None, description="使用的窗口长度")
    freq: str = Field(..., description="计算频率")
    results: list[FactorValue] = Field(..., description="因子计算结果")


class FundFactorResponse(BaseModel):
    """Response for single-fund factor computation."""

    fund_code: str = Field(..., description="基金代码")
    factors: list[FactorValue] = Field(..., description="该基金的所有因子值")


# ---------------------------------------------------------------------------
# NAV data stub (to be replaced with real DB access in production)
# ---------------------------------------------------------------------------


def _get_nav_data(fund_codes: list[str]) -> dict[str, pd.Series]:
    """Retrieve NAV data for the given fund codes.

    This is a stub implementation that generates synthetic NAV data for
    demonstration and testing purposes. In production, this would query
    the fund_nav table via the NavRepo.

    Returns:
        Mapping of fund_code → NAV pd.Series (date-indexed).
    """
    nav_data: dict[str, pd.Series] = {}
    for code in fund_codes:
        # Generate 252 trading days of synthetic NAV data
        dates = pd.bdate_range(end=pd.Timestamp.today(), periods=252)
        rng = np.random.default_rng(seed=hash(code) % (2**31))
        returns = rng.normal(0.0003, 0.015, size=len(dates))
        nav_values = 1.0 * np.cumprod(1 + returns)
        nav_data[code] = pd.Series(nav_values, index=dates, name=code)
    return nav_data


def _get_benchmark_nav(benchmark_code: str | None) -> Optional[pd.Series]:
    """Retrieve benchmark NAV data.

    Stub implementation generating synthetic benchmark data.
    """
    if benchmark_code is None:
        return None
    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=252)
    rng = np.random.default_rng(seed=hash(benchmark_code) % (2**31))
    returns = rng.normal(0.0002, 0.012, size=len(dates))
    nav_values = 1.0 * np.cumprod(1 + returns)
    return pd.Series(nav_values, index=dates, name=benchmark_code)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/factors",
    response_model=FactorListResponse,
    summary="因子元数据列表",
    description="返回所有已注册因子的元数据信息，支持按类别过滤。",
)
async def get_factors(
    category: str | None = Query(default=None, description="按类别过滤 (return/risk/risk_adjusted/benchmark/holding/manager)"),
) -> FactorListResponse:
    """List all registered factors with optional category filter."""
    factors = list_factors(category=category)
    items = [
        FactorMeta(
            name=f.name,
            category=f.category,
            window=f.window,
            return_type=f.return_type,
            description=f.description,
        )
        for f in factors
    ]
    return FactorListResponse(total=len(items), factors=items)


@router.post(
    "/factors/compute",
    response_model=FactorComputeResponse,
    summary="批量计算因子",
    description="对指定基金列表批量计算指定因子，支持自定义窗口和频率。",
)
async def compute_factors(
    request: FactorComputeRequest,
) -> FactorComputeResponse:
    """Batch compute factors for given funds and factor names."""
    # Validate factor names exist
    available = {f.name for f in list_factors()}
    invalid = set(request.factor_names) - available
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"未知因子: {', '.join(sorted(invalid))}。可用因子: {', '.join(sorted(available))}",
        )

    # Get NAV data (stub for now)
    nav_data = _get_nav_data(request.fund_codes)
    benchmark_nav = _get_benchmark_nav(request.benchmark_code)

    # Run the factor engine
    engine = FactorEngine(
        nav_data=nav_data,
        factor_names=request.factor_names,
        window=request.window,
        freq=request.freq,
        benchmark_nav=benchmark_nav,
    )
    df = engine.compute()

    # Convert DataFrame to flat result list
    results: list[FactorValue] = []
    for fund_code in df.index:
        for factor_name in df.columns:
            val = df.loc[fund_code, factor_name]
            results.append(
                FactorValue(
                    fund_code=str(fund_code),
                    factor_name=str(factor_name),
                    value=None if (val is None or np.isnan(val)) else float(val),
                )
            )

    return FactorComputeResponse(
        fund_codes=request.fund_codes,
        factor_names=request.factor_names,
        window=request.window,
        freq=request.freq,
        results=results,
    )


@router.get(
    "/funds/{code}/factors",
    response_model=FundFactorResponse,
    summary="单基金因子",
    description="计算并返回指定基金的所有已注册因子值。",
)
async def get_fund_factors(
    code: str,
    category: str | None = Query(default=None, description="按类别过滤因子"),
    window: int | None = Query(default=None, ge=1, description="窗口长度"),
    freq: Frequency = Query(default="daily", description="计算频率"),
) -> FundFactorResponse:
    """Compute all registered factors for a single fund."""
    # Get factor names (optionally filtered by category)
    factors = list_factors(category=category)
    if not factors:
        return FundFactorResponse(fund_code=code, factors=[])

    factor_names = [f.name for f in factors]

    # Get NAV data (stub)
    nav_data = _get_nav_data([code])

    # Run the factor engine
    engine = FactorEngine(
        nav_data=nav_data,
        factor_names=factor_names,
        window=window,
        freq=freq,
    )
    df = engine.compute()

    # Build response
    results: list[FactorValue] = []
    for factor_name in df.columns:
        val = df.loc[code, factor_name]
        results.append(
            FactorValue(
                fund_code=code,
                factor_name=str(factor_name),
                value=None if (val is None or np.isnan(val)) else float(val),
            )
        )

    return FundFactorResponse(fund_code=code, factors=results)



# ---------------------------------------------------------------------------
# Factor IC / quintile evaluation
# ---------------------------------------------------------------------------


class FactorEvaluateRequest(BaseModel):
    """Request body for cross-sectional factor IC + quintile evaluation."""

    fund_codes: list[str] = Field(..., min_length=5, description="基金代码池（≥5）")
    factor_name: str = Field(..., description="已注册的因子名（如 'sharpe', 'volatility'）")
    start_date: str | None = Field(None, description="评估起始日期 YYYY-MM-DD")
    end_date: str | None = Field(None, description="评估结束日期 YYYY-MM-DD")
    rebalance_freq: str = Field(
        "M",
        description=(
            "因子计算 / 调仓频率（pandas freq alias）。"
            "'D'=日, 'W'=周, 'M'=月（默认）, 'Q'=季"
        ),
    )
    decay_horizons: list[int] = Field(
        default_factory=lambda: [1, 5, 10, 20],
        description="IC 衰减分析的前向 horizon 列表",
    )
    n_groups: int = Field(5, ge=2, le=20, description="分组数（5=quintile, 10=decile）")
    method: str = Field(
        "spearman",
        description="相关方法：'pearson' / 'spearman'（Rank IC）",
    )


class ICStatsResponse(BaseModel):
    """One IC summary block."""

    ic_mean: float | None = None
    ic_std: float | None = None
    ic_ir: float | None = None
    ic_ir_annualized: float | None = None
    ic_t_stat: float | None = None
    ic_p_value: float | None = None
    ic_positive_rate: float | None = None
    ic_significant_rate: float | None = None
    n_periods: int = 0
    method: str = "spearman"


class QuintileResponse(BaseModel):
    """Long-short quintile backtest summary."""

    n_groups: int
    annualized_returns: dict[str, float | None]
    sharpes: dict[str, float | None]
    long_short_sharpe: float | None
    long_short_total_return: float | None
    monotonicity: int = Field(
        ...,
        description="1=分组收益单调上升, -1=单调下降, 0=非单调",
    )


class FactorEvaluateResponse(BaseModel):
    """Combined IC + quintile factor evaluation."""

    factor_name: str
    n_assets: int
    n_dates: int
    ic_pearson: ICStatsResponse | None = None
    ic_spearman: ICStatsResponse | None = None
    ic_decay: dict[str, ICStatsResponse] = Field(default_factory=dict)
    quintile: QuintileResponse | None = None
    note: str | None = None


@router.post(
    "/factors/evaluate",
    response_model=FactorEvaluateResponse,
    summary="因子横截面评估 (IC / Rank IC / 衰减 / 分组回测)",
    description=(
        "对一个已注册因子进行横截面评估，返回 IC / Rank IC / IC 衰减 / "
        "Top-N 分组多空回测的统计结果。\n\n"
        "**注意**：当前实现按 ``rebalance_freq`` 在每个时间点对每只基金计算"
        "因子值，再用次期收益做相关性检验。基金池太小（< 5）或样本期太短（< 10 期）"
        "会拒绝。"
    ),
)
async def evaluate_factor_endpoint(
    request: FactorEvaluateRequest,
    session: AsyncSession = Depends(get_session),
) -> FactorEvaluateResponse:
    """Evaluate a registered factor's predictive power."""
    from datetime import date as _date

    import numpy as np
    import pandas as pd

    from app.data.models.fund_nav import FundNav
    from app.domain.factors.ic_analysis import evaluate_factor as do_evaluate
    from app.domain.factors.registry import get_factor
    from sqlalchemy import select as sql_select

    # 1. Validate factor exists
    try:
        factor_def = get_factor(request.factor_name)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"因子未注册: {request.factor_name}",
        ) from exc

    # 1.1 Validate method early (before any DB I/O) so client errors
    # surface as 422 rather than being masked by the "sample too short" branch.
    if request.method not in {"pearson", "spearman"}:
        raise HTTPException(
            status_code=422,
            detail=f"method 必须为 pearson 或 spearman, 收到 {request.method}",
        )

    # 2. Load NAV panels for the requested universe
    stmt = sql_select(FundNav).where(FundNav.fund_code.in_(request.fund_codes))
    if request.start_date:
        stmt = stmt.where(FundNav.trade_date >= _date.fromisoformat(request.start_date))
    if request.end_date:
        stmt = stmt.where(FundNav.trade_date <= _date.fromisoformat(request.end_date))
    result = await session.execute(stmt.order_by(FundNav.trade_date))
    rows = result.scalars().all()

    if not rows:
        raise HTTPException(status_code=404, detail="数据库中无该基金池的 NAV 数据")

    # 3. Build wide NAV panel: index=date, columns=fund_code
    nav_records = [
        {"date": r.trade_date, "fund": r.fund_code, "nav": float(r.unit_nav)}
        for r in rows
        if r.unit_nav is not None
    ]
    nav_long = pd.DataFrame(nav_records)
    if nav_long.empty:
        raise HTTPException(status_code=404, detail="净值数据为空")

    nav_panel = nav_long.pivot(index="date", columns="fund", values="nav")
    nav_panel.index = pd.DatetimeIndex(nav_panel.index)
    nav_panel = nav_panel.sort_index()

    # 4. Resample to rebalance_freq using last NAV in the period
    freq_alias = request.rebalance_freq.upper()
    if freq_alias not in {"D", "W", "M", "ME", "Q", "QE"}:
        raise HTTPException(
            status_code=422,
            detail=f"不支持的 rebalance_freq={request.rebalance_freq}",
        )
    if freq_alias == "M":
        freq_alias = "ME"
    if freq_alias == "Q":
        freq_alias = "QE"
    if freq_alias != "D":
        nav_panel = nav_panel.resample(freq_alias).last().dropna(how="all")

    if len(nav_panel) < 10:
        return FactorEvaluateResponse(
            factor_name=request.factor_name,
            n_assets=nav_panel.shape[1],
            n_dates=len(nav_panel),
            note=f"样本期太短 ({len(nav_panel)} < 10 期)，无法做有意义的 IC 检验",
        )

    # 5. Build factor panel: at each date t, compute factor for each fund using
    #    a trailing window of NAVs ending at t.
    n_dates, n_assets = nav_panel.shape
    factor_panel = pd.DataFrame(
        np.nan, index=nav_panel.index, columns=nav_panel.columns, dtype=float
    )
    # Use a simple expanding window with a max lookback of 252 periods (1 year)
    max_lookback = 252 if freq_alias == "D" else (52 if freq_alias == "W" else 24)
    for t_idx in range(max(2, max_lookback // 4), n_dates):
        start_idx = max(0, t_idx - max_lookback + 1)
        for code in nav_panel.columns:
            window_nav = nav_panel[code].iloc[start_idx : t_idx + 1].dropna()
            if len(window_nav) < 2:
                continue
            try:
                value = factor_def.fn(window_nav)
                # Skip Series outputs (rolling factors) — take last
                if isinstance(value, pd.Series):
                    value = value.dropna().iloc[-1] if not value.empty else np.nan
                if value is not None and np.isfinite(value):
                    factor_panel.iat[t_idx, factor_panel.columns.get_loc(code)] = float(value)
            except Exception:
                # Skip funds where the factor cannot be computed for this window
                continue

    # 6. Build returns panel: (NAV_t / NAV_{t-1}) - 1 at the rebalance frequency
    returns_panel = nav_panel.pct_change().fillna(0.0)

    # 7. Validate enough non-NaN factor data
    valid_factor_cells = factor_panel.notna().sum().sum()
    if valid_factor_cells < 30:
        return FactorEvaluateResponse(
            factor_name=request.factor_name,
            n_assets=n_assets,
            n_dates=n_dates,
            note=(
                f"因子有效数据点过少 ({valid_factor_cells} < 30)，无法做有意义的 IC 检验。"
                "可能是因为大部分基金在样本期内净值数据不连续。"
            ),
        )

    # 8. Run IC + quintile evaluation
    eval_result = do_evaluate(
        factor_panel=factor_panel,
        returns_panel=returns_panel,
        decay_horizons=tuple(h for h in request.decay_horizons if h >= 1),
        n_groups=request.n_groups,
    )

    def _ic_to_response(ic) -> ICStatsResponse | None:
        if ic is None:
            return None
        d = ic.to_dict()
        return ICStatsResponse(
            ic_mean=d.get("ic_mean"),
            ic_std=d.get("ic_std"),
            ic_ir=d.get("ic_ir"),
            ic_ir_annualized=d.get("ic_ir_annualized"),
            ic_t_stat=d.get("ic_t_stat"),
            ic_p_value=d.get("ic_p_value"),
            ic_positive_rate=d.get("ic_positive_rate"),
            ic_significant_rate=d.get("ic_significant_rate"),
            n_periods=d.get("n_periods", 0),
            method=d.get("method", request.method),
        )

    quintile_response = None
    if eval_result.quintile is not None:
        q = eval_result.quintile.to_dict()
        quintile_response = QuintileResponse(
            n_groups=q["n_groups"],
            annualized_returns=q["annualized_returns"],
            sharpes=q["sharpes"],
            long_short_sharpe=q["long_short_sharpe"],
            long_short_total_return=q.get("long_short_total_return"),
            monotonicity=q["monotonicity"],
        )

    return FactorEvaluateResponse(
        factor_name=request.factor_name,
        n_assets=n_assets,
        n_dates=n_dates,
        ic_pearson=_ic_to_response(eval_result.ic_pearson),
        ic_spearman=_ic_to_response(eval_result.ic_spearman),
        ic_decay={
            str(h): _ic_to_response(s) for h, s in eval_result.ic_decay.items() if s
        },
        quintile=quintile_response,
    )
