"""基金自动发现 API 端点。

提供：
- ``GET /discovery/rankings``     — 查询排名快照数据
- ``GET /discovery/stats``        — 发现统计概览
- ``POST /discovery/trigger``     — 手动触发发现任务
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.fund_ranking import FundRanking
from app.data.models.funds import Fund
from app.data.session import get_session

router = APIRouter(prefix="/discovery", tags=["discovery"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class RankingItem(BaseModel):
    """单条排名记录。"""

    model_config = ConfigDict(from_attributes=True)

    fund_code: str = Field(..., description="基金代码")
    fund_name: str | None = Field(None, description="基金名称")
    snapshot_date: date = Field(..., description="快照日期")
    sort_metric: str = Field(..., description="排序维度")
    rank_position: int = Field(..., description="排名位次")
    fund_type: str | None = Field(None, description="基金类型")
    daily_return: Decimal | None = Field(None, description="日涨幅")
    weekly_return: Decimal | None = Field(None, description="近1周涨幅")
    monthly_return: Decimal | None = Field(None, description="近1月涨幅")
    quarterly_return: Decimal | None = Field(None, description="近3月涨幅")
    half_year_return: Decimal | None = Field(None, description="近6月涨幅")
    yearly_return: Decimal | None = Field(None, description="近1年涨幅")


class PaginatedRankings(BaseModel):
    """分页排名响应。"""

    items: list[RankingItem] = Field(..., description="排名列表")
    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页条数")


class DiscoveryStats(BaseModel):
    """发现统计概览。"""

    total_funds_tracked: int = Field(..., description="当前跟踪基金总数")
    funds_from_discovery: int = Field(..., description="通过发现任务添加的基金数")
    latest_snapshot_date: date | None = Field(None, description="最新快照日期")
    ranking_records_count: int = Field(..., description="排名记录总数")
    unique_funds_in_rankings: int = Field(..., description="排名中不重复基金数")
    dimensions_tracked: list[str] = Field(..., description="跟踪的排名维度")


class TriggerResponse(BaseModel):
    """手动触发响应。"""

    status: str = Field(..., description="触发状态")
    task_id: str = Field(..., description="Celery 任务 ID")
    message: str = Field(..., description="提示信息")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/rankings",
    response_model=PaginatedRankings,
    summary="排名快照查询",
    description="查询基金排名快照数据，支持按日期、维度、类型过滤。",
)
async def list_rankings(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=50, ge=1, le=200, description="每页条数"),
    snapshot_date: date | None = Query(default=None, description="快照日期"),
    sort_metric: str | None = Query(default=None, description="排序维度: 6yzf/1nzf/3nzf"),
    fund_type: str | None = Query(default=None, description="基金类型: stock/mixed/index"),
    fund_code: str | None = Query(default=None, description="基金代码"),
    db: AsyncSession = Depends(get_session),
) -> PaginatedRankings:
    """分页查询排名快照。"""
    query = select(FundRanking)
    count_query = select(func.count()).select_from(FundRanking)

    # 默认查最新一天的数据
    if snapshot_date is None:
        latest_stmt = select(func.max(FundRanking.snapshot_date))
        latest_result = await db.execute(latest_stmt)
        snapshot_date = latest_result.scalar_one_or_none()
        if snapshot_date is None:
            return PaginatedRankings(items=[], total=0, page=page, page_size=page_size)

    query = query.where(FundRanking.snapshot_date == snapshot_date)
    count_query = count_query.where(FundRanking.snapshot_date == snapshot_date)

    if sort_metric:
        query = query.where(FundRanking.sort_metric == sort_metric)
        count_query = count_query.where(FundRanking.sort_metric == sort_metric)
    if fund_type:
        query = query.where(FundRanking.fund_type == fund_type)
        count_query = count_query.where(FundRanking.fund_type == fund_type)
    if fund_code:
        query = query.where(FundRanking.fund_code == fund_code)
        count_query = count_query.where(FundRanking.fund_code == fund_code)

    # Total count
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Paginate
    offset = (page - 1) * page_size
    query = query.order_by(FundRanking.rank_position).offset(offset).limit(page_size)

    result = await db.execute(query)
    rankings = result.scalars().all()

    return PaginatedRankings(
        items=[RankingItem.model_validate(r) for r in rankings],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/stats",
    response_model=DiscoveryStats,
    summary="发现统计",
    description="获取基金自动发现的统计概览信息。",
)
async def get_discovery_stats(
    db: AsyncSession = Depends(get_session),
) -> DiscoveryStats:
    """返回发现系统的统计概览。"""
    # 当前跟踪基金总数
    total_funds_result = await db.execute(
        select(func.count()).select_from(Fund).where(Fund.status == "active")
    )
    total_funds = total_funds_result.scalar_one()

    # 通过发现任务添加的基金数
    discovery_funds_result = await db.execute(
        select(func.count()).select_from(Fund).where(Fund.source == "discovery")
    )
    funds_from_discovery = discovery_funds_result.scalar_one()

    # 最新快照日期
    latest_date_result = await db.execute(
        select(func.max(FundRanking.snapshot_date))
    )
    latest_snapshot_date = latest_date_result.scalar_one_or_none()

    # 排名记录总数
    ranking_count_result = await db.execute(
        select(func.count()).select_from(FundRanking)
    )
    ranking_records_count = ranking_count_result.scalar_one()

    # 排名中不重复基金数
    unique_funds_result = await db.execute(
        select(func.count(func.distinct(FundRanking.fund_code)))
    )
    unique_funds = unique_funds_result.scalar_one()

    # 跟踪的排名维度
    metrics_result = await db.execute(
        select(func.distinct(FundRanking.sort_metric))
    )
    dimensions = [row[0] for row in metrics_result.all() if row[0]]

    return DiscoveryStats(
        total_funds_tracked=total_funds,
        funds_from_discovery=funds_from_discovery,
        latest_snapshot_date=latest_snapshot_date,
        ranking_records_count=ranking_records_count,
        unique_funds_in_rankings=unique_funds,
        dimensions_tracked=dimensions,
    )


@router.post(
    "/trigger",
    response_model=TriggerResponse,
    summary="手动触发发现",
    description="手动触发一次基金自动发现任务。默认同步执行，传 async=true 则提交到 Celery 队列异步执行。",
)
async def trigger_discovery(
    async_mode: bool = Query(default=False, alias="async", description="是否异步执行"),
) -> TriggerResponse:
    """手动触发 discover_funds 任务。

    默认同步执行并返回结果；传 async=true 则提交到 Celery 队列异步执行。
    """
    from app.tasks.discovery import discover_funds

    if async_mode:
        result = discover_funds.apply_async(queue="ingest")
        return TriggerResponse(
            status="triggered",
            task_id=result.id,
            message="发现任务已提交，后台执行中，预计 1-2 分钟完成。",
        )

    # 同步模式：直接调用异步实现
    from app.tasks.discovery import _discover_funds_async

    result = await _discover_funds_async()
    codes_discovered = result.get("unique_codes_discovered", 0)
    funds_created = result.get("funds_created", 0)

    return TriggerResponse(
        status="completed",
        task_id="sync",
        message=f"发现完成：发现 {codes_discovered} 只基金，新增 {funds_created} 只。",
    )


# ---------------------------------------------------------------------------
# 4433 法则筛选
# ---------------------------------------------------------------------------


class Filter4433Request(BaseModel):
    """4433 筛选请求参数。"""

    fund_type: str | None = Field(None, description="基金类型: stock/mixed/bond/index/all")
    year1_percentile: float = Field(default=0.25, ge=0.01, le=1.0, description="近1年排名百分位阈值")
    year2_percentile: float = Field(default=0.25, ge=0.01, le=1.0, description="近2年排名百分位阈值")
    month6_percentile: float = Field(default=0.333, ge=0.01, le=1.0, description="近6月排名百分位阈值")
    month3_percentile: float = Field(default=0.333, ge=0.01, le=1.0, description="近3月排名百分位阈值")
    min_inception_years: float | None = Field(default=3.0, description="最小成立年限")


class Fund4433Item(BaseModel):
    """4433 筛选结果项。"""

    fund_code: str
    fund_name: str | None = None
    fund_type: str | None = None
    rank_1y: float | None = None
    rank_6m: float | None = None
    rank_3m: float | None = None
    return_1y: float | None = None
    return_6m: float | None = None
    return_3m: float | None = None
    pass_all: bool = True


class Filter4433Response(BaseModel):
    """4433 筛选响应。"""

    total_screened: int = Field(..., description="筛选的基金总数")
    passed_count: int = Field(..., description="通过 4433 的基金数")
    pass_rate: float = Field(..., description="通过率")
    funds: list[Fund4433Item] = Field(default_factory=list)


@router.post(
    "/4433",
    response_model=Filter4433Response,
    summary="4433 法则筛选",
    description="使用 4433 法则筛选基金：近1年前1/4、长期前1/4、近6月前1/3、近3月前1/3。",
)
async def filter_4433(
    body: Filter4433Request | None = None,
    db: AsyncSession = Depends(get_session),
) -> Filter4433Response:
    """Apply 4433 fund screening rule."""
    from sqlalchemy import and_, distinct

    from app.domain.discovery.fund_filter_4433 import (
        Filter4433Params,
        apply_4433_filter,
    )

    if body is None:
        body = Filter4433Request()

    # 获取最新快照日期
    latest_stmt = select(func.max(FundRanking.snapshot_date))
    if body.fund_type and body.fund_type != "all":
        latest_stmt = latest_stmt.where(FundRanking.fund_type == body.fund_type)
    latest_result = await db.execute(latest_stmt)
    latest_date = latest_result.scalar_one_or_none()

    if latest_date is None:
        return Filter4433Response(total_screened=0, passed_count=0, pass_rate=0, funds=[])

    # 查询排名数据 — 需要多个维度的排名
    # 构建每只基金的各期排名
    fund_type_filter = body.fund_type if body.fund_type and body.fund_type != "all" else None

    # 获取所有基金在最新日期的排名数据
    query = select(FundRanking).where(FundRanking.snapshot_date == latest_date)
    if fund_type_filter:
        query = query.where(FundRanking.fund_type == fund_type_filter)

    result = await db.execute(query)
    all_rankings = result.scalars().all()

    # 按基金代码聚合各维度排名
    fund_data: dict[str, dict[str, Any]] = {}
    for r in all_rankings:
        if r.fund_code not in fund_data:
            fund_data[r.fund_code] = {
                "fund_code": r.fund_code,
                "fund_name": r.fund_name,
                "fund_type": r.fund_type or "all",
            }

        # 映射排名维度到标准字段
        metric = r.sort_metric
        if metric in ("1nzf", "1n"):
            fund_data[r.fund_code]["rank_1y"] = r.rank_position
            fund_data[r.fund_code]["return_1y"] = float(r.yearly_return) if r.yearly_return else None
        elif metric in ("2nzf", "2n"):
            fund_data[r.fund_code]["rank_2y"] = r.rank_position
        elif metric in ("3nzf", "3n"):
            fund_data[r.fund_code]["rank_3y"] = r.rank_position
        elif metric in ("6yzf", "6y"):
            fund_data[r.fund_code]["rank_6m"] = r.rank_position
            fund_data[r.fund_code]["return_6m"] = float(r.half_year_return) if r.half_year_return else None
        elif metric in ("3yzf", "3y"):
            fund_data[r.fund_code]["rank_3m"] = r.rank_position
            fund_data[r.fund_code]["return_3m"] = float(r.quarterly_return) if r.quarterly_return else None

    # 计算各类型基金总数
    type_counts: dict[str, int] = {}
    for fd in fund_data.values():
        ft = fd.get("fund_type", "all")
        type_counts[ft] = type_counts.get(ft, 0) + 1

    # 成立年限筛选
    if body.min_inception_years:
        from datetime import timedelta as td
        min_inception = latest_date - td(days=int(body.min_inception_years * 365))
        inception_result = await db.execute(
            select(Fund.code, Fund.inception_date).where(
                Fund.code.in_(list(fund_data.keys()))
            )
        )
        inception_map = {row.code: row.inception_date for row in inception_result.all()}

        # 过滤掉成立时间不够的（inception_date 为 NULL 或不在 funds 表中的保留）
        excluded_codes = set()
        for code, inception in inception_map.items():
            if inception and inception > min_inception:
                excluded_codes.add(code)

        fund_data = {k: v for k, v in fund_data.items() if k not in excluded_codes}

    total_screened = len(fund_data)

    # 应用 4433 筛选
    params = Filter4433Params(
        year1_percentile=body.year1_percentile,
        year2_percentile=body.year2_percentile,
        year3_percentile=body.year2_percentile,
        month6_percentile=body.month6_percentile,
        month3_percentile=body.month3_percentile,
    )

    passed = apply_4433_filter(
        list(fund_data.values()),
        type_counts,
        params,
    )

    pass_rate = len(passed) / total_screened if total_screened > 0 else 0

    return Filter4433Response(
        total_screened=total_screened,
        passed_count=len(passed),
        pass_rate=round(pass_rate, 4),
        funds=[
            Fund4433Item(
                fund_code=f.fund_code,
                fund_name=f.fund_name,
                fund_type=f.fund_type,
                rank_1y=f.rank_1y,
                rank_6m=f.rank_6m,
                rank_3m=f.rank_3m,
                return_1y=f.return_1y,
                return_6m=f.return_6m,
                return_3m=f.return_3m,
                pass_all=f.pass_all,
            )
            for f in passed[:100]  # 最多返回100只
        ],
    )
