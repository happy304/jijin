"""Strategy CRUD API endpoints.

Provides:
- ``POST /strategies``         — create a new strategy
- ``GET /strategies``          — list strategies (paginated)
- ``GET /strategies/{id}``     — get strategy detail
- ``PUT /strategies/{id}``     — update a strategy
- ``DELETE /strategies/{id}``  — delete a strategy

Strategy params are validated using Pydantic JSON Schema per strategy_type.
Requirements: 7.5
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.backtests import BacktestRun
from app.data.models.strategies import Strategy
from app.data.session import get_session

router = APIRouter(prefix="/strategies", tags=["strategies"])


# ---------------------------------------------------------------------------
# Pydantic JSON Schema definitions for strategy params validation
# ---------------------------------------------------------------------------

# Per-type parameter schemas. Each defines the expected structure for
# the `params` JSONB field based on strategy_type.
STRATEGY_PARAM_SCHEMAS: dict[str, dict[str, Any]] = {
    "dca": {
        "type": "object",
        "properties": {
            "amount": {"type": "number", "exclusiveMinimum": 0, "description": "定投金额"},
            "frequency": {
                "type": "string",
                "enum": ["weekly", "biweekly", "monthly"],
                "description": "定投频率",
            },
            "dca_type": {
                "type": "string",
                "enum": ["fixed", "value_averaging", "smart"],
                "description": "定投类型",
            },
            "ma_window": {"type": "integer", "minimum": 1, "description": "均线窗口（智能定投）"},
        },
        "required": ["amount", "frequency"],
        "additionalProperties": True,
    },
    "momentum": {
        "type": "object",
        "properties": {
            "lookback_months": {"type": "integer", "minimum": 1, "description": "回看月数"},
            "top_n": {"type": "integer", "minimum": 1, "description": "持有数量"},
            "rebalance_freq": {
                "type": "string",
                "enum": ["weekly", "monthly", "quarterly"],
                "description": "调仓频率",
            },
            "score_factor": {
                "type": "string",
                "enum": ["return", "sharpe", "information_ratio"],
                "description": "评分因子",
            },
        },
        "required": ["lookback_months", "top_n", "rebalance_freq"],
        "additionalProperties": True,
    },
    "risk_parity": {
        "type": "object",
        "properties": {
            "rebalance_freq": {
                "type": "string",
                "enum": ["weekly", "monthly", "quarterly"],
                "description": "调仓频率",
            },
            "cov_method": {
                "type": "string",
                "enum": ["sample", "ewm", "shrinkage"],
                "description": "协方差估计方法",
            },
            "lookback_days": {"type": "integer", "minimum": 20, "description": "回看天数"},
        },
        "required": ["rebalance_freq"],
        "additionalProperties": True,
    },
    "mean_variance": {
        "type": "object",
        "properties": {
            "rebalance_freq": {
                "type": "string",
                "enum": ["weekly", "monthly", "quarterly"],
                "description": "调仓频率",
            },
            "risk_free_rate": {"type": "number", "minimum": 0, "description": "无风险利率"},
            "target_return": {"type": "number", "description": "目标收益率"},
            "max_weight": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 1,
                "description": "单资产最大权重",
            },
        },
        "required": ["rebalance_freq"],
        "additionalProperties": True,
    },
    "timing": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["dual_ma", "macd", "valuation"],
                "description": "择时方法",
            },
            "fast_window": {"type": "integer", "minimum": 1, "description": "快线窗口"},
            "slow_window": {"type": "integer", "minimum": 1, "description": "慢线窗口"},
        },
        "required": ["method"],
        "additionalProperties": True,
    },
    "fof": {
        "type": "object",
        "properties": {
            "factor_weights": {
                "type": "object",
                "description": "因子权重映射",
            },
            "top_n": {"type": "integer", "minimum": 1, "description": "持有数量"},
            "rebalance_freq": {
                "type": "string",
                "enum": ["weekly", "monthly", "quarterly"],
                "description": "调仓频率",
            },
            "optimization": {
                "type": "string",
                "enum": ["equal_weight", "risk_parity", "mean_variance"],
                "description": "优化方法",
            },
        },
        "required": ["factor_weights", "top_n", "rebalance_freq"],
        "additionalProperties": True,
    },
}

VALID_STRATEGY_TYPES = set(STRATEGY_PARAM_SCHEMAS.keys())


def _validate_params_against_schema(
    strategy_type: str | None, params: dict[str, Any]
) -> list[str]:
    """Validate params dict against the JSON Schema for the given strategy_type.

    Returns a list of validation error messages (empty if valid).
    Uses a lightweight validation approach based on the schema definitions.
    """
    if strategy_type is None:
        # No type-specific validation when strategy_type is not set
        return []

    schema = STRATEGY_PARAM_SCHEMAS.get(strategy_type)
    if schema is None:
        return [f"未知策略类型: {strategy_type}。支持的类型: {', '.join(sorted(VALID_STRATEGY_TYPES))}"]

    errors: list[str] = []

    # Check required fields
    required_fields = schema.get("required", [])
    for field in required_fields:
        if field not in params:
            errors.append(f"缺少必填参数: {field}")

    # Validate field types and constraints
    properties = schema.get("properties", {})
    for key, value in params.items():
        if key not in properties:
            continue  # additionalProperties allowed

        prop_schema = properties[key]
        prop_type = prop_schema.get("type")

        # Type checking
        if prop_type == "number" and not isinstance(value, (int, float)):
            errors.append(f"参数 {key} 应为数字类型")
        elif prop_type == "integer" and not isinstance(value, int):
            errors.append(f"参数 {key} 应为整数类型")
        elif prop_type == "string" and not isinstance(value, str):
            errors.append(f"参数 {key} 应为字符串类型")
        elif prop_type == "object" and not isinstance(value, dict):
            errors.append(f"参数 {key} 应为对象类型")

        # Enum validation
        if "enum" in prop_schema and value not in prop_schema["enum"]:
            errors.append(
                f"参数 {key} 的值 '{value}' 不在允许范围内: {prop_schema['enum']}"
            )

        # Numeric constraints
        if isinstance(value, (int, float)):
            if "minimum" in prop_schema and value < prop_schema["minimum"]:
                errors.append(f"参数 {key} 的值 {value} 小于最小值 {prop_schema['minimum']}")
            if "exclusiveMinimum" in prop_schema and value <= prop_schema["exclusiveMinimum"]:
                errors.append(
                    f"参数 {key} 的值 {value} 必须大于 {prop_schema['exclusiveMinimum']}"
                )
            if "maximum" in prop_schema and value > prop_schema["maximum"]:
                errors.append(f"参数 {key} 的值 {value} 大于最大值 {prop_schema['maximum']}")

    return errors


# ---------------------------------------------------------------------------
# Request / Response models (Pydantic v2)
# ---------------------------------------------------------------------------


class StrategyCreate(BaseModel):
    """Request body for creating a strategy."""

    name: str = Field(..., min_length=1, max_length=100, description="策略名称")
    strategy_type: str | None = Field(
        None,
        max_length=40,
        description="策略类型: dca/momentum/risk_parity/mean_variance/timing/fof",
    )
    params: dict[str, Any] = Field(..., description="策略参数 (JSON)")
    universe: dict[str, Any] | list[str] = Field(..., description="基金池配置")
    benchmark: str | None = Field(None, max_length=20, description="基准代码")
    created_by: str | None = Field(None, max_length=40, description="创建者")


class StrategyUpdate(BaseModel):
    """Request body for updating a strategy (partial update)."""

    name: str | None = Field(None, min_length=1, max_length=100, description="策略名称")
    strategy_type: str | None = Field(
        None,
        max_length=40,
        description="策略类型: dca/momentum/risk_parity/mean_variance/timing/fof",
    )
    params: dict[str, Any] | None = Field(None, description="策略参数 (JSON)")
    universe: dict[str, Any] | list[str] | None = Field(None, description="基金池配置")
    benchmark: str | None = Field(None, max_length=20, description="基准代码")


class StrategyResponse(BaseModel):
    """Response model for a single strategy."""

    id: int = Field(..., description="策略 ID")
    name: str = Field(..., description="策略名称")
    strategy_type: str | None = Field(None, description="策略类型")
    params: dict[str, Any] = Field(..., description="策略参数")
    universe: Any = Field(..., description="基金池配置")
    benchmark: str | None = Field(None, description="基准代码")
    created_by: str | None = Field(None, description="创建者")
    created_at: datetime | None = Field(None, description="创建时间")


class PaginatedStrategies(BaseModel):
    """Paginated response wrapper for strategy list."""

    items: list[StrategyResponse] = Field(..., description="策略列表")
    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页条数")
    pages: int = Field(..., description="总页数")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=StrategyResponse,
    status_code=201,
    summary="创建策略",
    description="创建一个新的策略配置。策略参数将根据 strategy_type 进行 JSON Schema 校验。",
)
async def create_strategy(
    body: StrategyCreate,
    db: AsyncSession = Depends(get_session),
) -> StrategyResponse:
    """Create a new strategy."""
    # Validate params against JSON Schema for the strategy_type
    errors = _validate_params_against_schema(body.strategy_type, body.params)
    if errors:
        raise HTTPException(
            status_code=422,
            detail=f"策略参数校验失败: {'; '.join(errors)}",
        )

    # Normalize universe to dict
    universe_data: Any = body.universe
    if isinstance(universe_data, list):
        universe_data = {"fund_codes": universe_data}

    strategy = Strategy(
        name=body.name,
        strategy_type=body.strategy_type,
        params=body.params,
        universe=universe_data,
        benchmark=body.benchmark,
        created_by=body.created_by,
    )
    db.add(strategy)
    await db.commit()
    await db.refresh(strategy)

    return StrategyResponse(
        id=strategy.id,
        name=strategy.name,
        strategy_type=strategy.strategy_type,
        params=strategy.params,
        universe=strategy.universe,
        benchmark=strategy.benchmark,
        created_by=strategy.created_by,
        created_at=strategy.created_at,
    )


@router.get(
    "",
    response_model=PaginatedStrategies,
    summary="策略列表",
    description="分页查询策略列表，支持按类型和创建者过滤。",
)
async def list_strategies(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数"),
    strategy_type: str | None = Query(default=None, description="策略类型过滤"),
    created_by: str | None = Query(default=None, description="创建者过滤"),
    db: AsyncSession = Depends(get_session),
) -> PaginatedStrategies:
    """List strategies with pagination and optional filters."""
    query = select(Strategy)
    count_query = select(func.count()).select_from(Strategy)

    # Apply filters
    if strategy_type:
        query = query.where(Strategy.strategy_type == strategy_type)
        count_query = count_query.where(Strategy.strategy_type == strategy_type)
    if created_by:
        query = query.where(Strategy.created_by == created_by)
        count_query = count_query.where(Strategy.created_by == created_by)

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.order_by(Strategy.id.desc()).offset(offset).limit(page_size)

    result = await db.execute(query)
    strategies = result.scalars().all()

    pages = (total + page_size - 1) // page_size if total > 0 else 0

    items = [
        StrategyResponse(
            id=s.id,
            name=s.name,
            strategy_type=s.strategy_type,
            params=s.params,
            universe=s.universe,
            benchmark=s.benchmark,
            created_by=s.created_by,
            created_at=s.created_at,
        )
        for s in strategies
    ]

    return PaginatedStrategies(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/{strategy_id}",
    response_model=StrategyResponse,
    summary="策略详情",
    description="根据策略 ID 获取完整策略配置。",
)
async def get_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_session),
) -> StrategyResponse:
    """Get strategy detail by ID."""
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()

    if strategy is None:
        raise HTTPException(status_code=404, detail=f"策略 {strategy_id} 不存在")

    return StrategyResponse(
        id=strategy.id,
        name=strategy.name,
        strategy_type=strategy.strategy_type,
        params=strategy.params,
        universe=strategy.universe,
        benchmark=strategy.benchmark,
        created_by=strategy.created_by,
        created_at=strategy.created_at,
    )


@router.put(
    "/{strategy_id}",
    response_model=StrategyResponse,
    summary="更新策略",
    description="更新指定策略的配置。支持部分更新。",
)
async def update_strategy(
    strategy_id: int,
    body: StrategyUpdate,
    db: AsyncSession = Depends(get_session),
) -> StrategyResponse:
    """Update an existing strategy."""
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()

    if strategy is None:
        raise HTTPException(status_code=404, detail=f"策略 {strategy_id} 不存在")

    # Apply partial updates
    update_data = body.model_dump(exclude_unset=True)

    # Determine effective strategy_type for validation
    effective_type = update_data.get("strategy_type", strategy.strategy_type)

    # Validate params if they are being updated
    if "params" in update_data:
        errors = _validate_params_against_schema(effective_type, update_data["params"])
        if errors:
            raise HTTPException(
                status_code=422,
                detail=f"策略参数校验失败: {'; '.join(errors)}",
            )
    elif "strategy_type" in update_data and update_data["strategy_type"] is not None:
        # If only strategy_type is changing, validate existing params against new type
        errors = _validate_params_against_schema(update_data["strategy_type"], strategy.params)
        if errors:
            raise HTTPException(
                status_code=422,
                detail=f"策略参数校验失败: {'; '.join(errors)}",
            )

    for field, value in update_data.items():
        if field == "universe" and isinstance(value, list):
            value = {"fund_codes": value}
        setattr(strategy, field, value)

    await db.commit()
    await db.refresh(strategy)

    return StrategyResponse(
        id=strategy.id,
        name=strategy.name,
        strategy_type=strategy.strategy_type,
        params=strategy.params,
        universe=strategy.universe,
        benchmark=strategy.benchmark,
        created_by=strategy.created_by,
        created_at=strategy.created_at,
    )


@router.delete(
    "/{strategy_id}",
    status_code=204,
    summary="删除策略",
    description="删除指定策略。",
)
async def delete_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_session),
) -> None:
    """Delete a strategy by ID."""
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()

    if strategy is None:
        raise HTTPException(status_code=404, detail=f"策略 {strategy_id} 不存在")

    # Nullify strategy_id in related backtest_runs to avoid FK constraint violation
    await db.execute(
        update(BacktestRun)
        .where(BacktestRun.strategy_id == strategy_id)
        .values(strategy_id=None)
    )

    await db.delete(strategy)
    await db.commit()


# ---------------------------------------------------------------------------
# Backtest date range helper
# ---------------------------------------------------------------------------


class StrategyDateRange(BaseModel):
    """策略基金池可回测的日期范围。"""

    earliest_date: str | None = Field(None, description="最早可用数据日期（所有基金中最晚的成立/数据起始日期）")
    fund_dates: dict[str, str | None] = Field(default_factory=dict, description="各基金的最早数据日期")


@router.get(
    "/{strategy_id}/date-range",
    response_model=StrategyDateRange,
    summary="可回测日期范围",
    description="查询策略基金池中所有基金的最早可用数据日期，用于限制回测起始日期。",
)
async def get_strategy_date_range(
    strategy_id: int,
    db: AsyncSession = Depends(get_session),
) -> StrategyDateRange:
    """Get the earliest available data date for all funds in the strategy universe."""
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()

    if strategy is None:
        raise HTTPException(status_code=404, detail=f"策略 {strategy_id} 不存在")

    universe_codes = strategy.universe
    if isinstance(universe_codes, dict):
        universe_codes = universe_codes.get("fund_codes", [])

    if not universe_codes:
        return StrategyDateRange(earliest_date=None, fund_dates={})

    from app.data.models.fund_nav import FundNav
    from app.data.models.funds import Fund

    fund_dates: dict[str, str | None] = {}
    latest_start: str | None = None

    for code in universe_codes:
        # 优先使用 inception_date
        fund_result = await db.execute(
            select(Fund.inception_date).where(Fund.code == code)
        )
        inception = fund_result.scalar_one_or_none()

        if inception:
            fund_dates[code] = str(inception)
        else:
            # 回退到 NAV 数据中最早日期
            nav_result = await db.execute(
                select(func.min(FundNav.trade_date)).where(FundNav.fund_code == code)
            )
            earliest_nav = nav_result.scalar_one_or_none()
            fund_dates[code] = str(earliest_nav) if earliest_nav else None

        # 取所有基金中最晚的起始日期作为回测最早可用日期
        if fund_dates[code] is not None:
            if latest_start is None or fund_dates[code] > latest_start:
                latest_start = fund_dates[code]

    return StrategyDateRange(earliest_date=latest_start, fund_dates=fund_dates)
