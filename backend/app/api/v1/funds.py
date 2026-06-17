"""Fund search and NAV query API endpoints.

Provides:
- ``GET /funds`` — paginated fund list with filtering
- ``GET /funds/{code}`` — single fund detail
- ``GET /funds/{code}/nav`` — NAV time series for a fund

All endpoints integrate Redis caching (requirement 2.9) and use
Pydantic v2 response models with full OpenAPI documentation
(requirement 7.1, 7.2).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette import status as http_status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.data import cache
from app.data.models.fund_nav import FundNav
from app.data.models.funds import Fund
from app.data.providers.base import AllProvidersFailedError, ProviderNotFoundError
from app.data.providers.factory import build_default_composite_provider
from app.data.session import get_session

router = APIRouter(prefix="/funds", tags=["funds"])


# ---------------------------------------------------------------------------
# Response models (Pydantic v2)
# ---------------------------------------------------------------------------


class FundSummary(BaseModel):
    """Compact fund representation for list endpoints."""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(..., description="基金代码")
    name: str = Field(..., description="基金名称")
    fund_type: str | None = Field(None, description="基金类型")
    status: str = Field(default="active", description="基金状态")
    inception_date: date | None = Field(None, description="成立日期")
    management_fee: Decimal | None = Field(None, description="管理费率")
    company_id: str | None = Field(None, description="基金公司")


class PaginatedFunds(BaseModel):
    """Paginated response wrapper for fund list."""

    items: list[FundSummary] = Field(..., description="基金列表")
    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页条数")
    pages: int = Field(..., description="总页数")


class FundOptionItem(BaseModel):
    """Lightweight fund option for selector scenarios."""

    code: str = Field(..., description="基金代码")
    name: str = Field(..., description="基金名称")
    fund_type: str | None = Field(None, description="基金类型")
    status: str = Field(..., description="基金状态")
    inception_date: date | None = Field(None, description="成立日期")


class FundOptionsResponse(BaseModel):
    """Full local fund options for selectors."""

    items: list[FundOptionItem] = Field(..., description="基金选项列表")


class FundDetail(BaseModel):
    """Full fund detail response."""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(..., description="基金代码")
    name: str = Field(..., description="基金名称")
    fund_type: str | None = Field(None, description="基金类型")
    sub_type: str | None = Field(None, description="基金子类型")
    company_id: str | None = Field(None, description="基金公司")
    inception_date: date | None = Field(None, description="成立日期")
    benchmark: str | None = Field(None, description="业绩基准")
    management_fee: Decimal | None = Field(None, description="管理费率")
    custodian_fee: Decimal | None = Field(None, description="托管费率")
    currency: str = Field(default="CNY", description="币种")
    status: str = Field(default="active", description="基金状态")
    is_purchasable: bool = Field(default=True, description="是否可申购")
    purchase_limit: Decimal | None = Field(None, description="申购限额")
    source: str | None = Field(None, description="数据来源")


class NavItem(BaseModel):
    """Single NAV record in the time series response."""

    model_config = ConfigDict(from_attributes=True)

    trade_date: date = Field(..., description="交易日期")
    unit_nav: Decimal | None = Field(None, description="单位净值")
    accum_nav: Decimal | None = Field(None, description="累计净值")
    adj_nav: Decimal | None = Field(None, description="复权净值")
    daily_return: Decimal | None = Field(None, description="日收益率")


class NavResponse(BaseModel):
    """NAV time series response."""

    fund_code: str = Field(..., description="基金代码")
    start_date: date = Field(..., description="起始日期")
    end_date: date = Field(..., description="结束日期")
    count: int = Field(..., description="记录数")
    needs_ingest: bool = Field(default=False, description="是否需要采集数据（本地无数据时为 True）")
    records: list[NavItem] = Field(..., description="净值记录")


class NavQualityIssue(BaseModel):
    """Single NAV quality issue for audit panels."""

    issue_type: str = Field(..., description="问题类型：missing_gap/spike/adj_nav_missing 等")
    start_date: date | None = Field(None, description="问题开始日期")
    end_date: date | None = Field(None, description="问题结束日期")
    trade_date: date | None = Field(None, description="单日问题日期")
    severity: str = Field(default="warning", description="严重程度：info/warning/poor")
    message: str = Field(..., description="问题说明")


class NavQualityResponse(BaseModel):
    """NAV quality snapshot for one fund."""

    fund_code: str = Field(..., description="基金代码")
    fund_type: str | None = Field(None, description="基金类型")
    start_date: date = Field(..., description="检查起始日期")
    end_date: date = Field(..., description="检查结束日期")
    total_calendar_days: int = Field(..., description="检查区间自然日数量")
    total_nav_points: int = Field(..., description="有效 NAV 点数")
    first_nav_date: date | None = Field(None, description="最早 NAV 日期")
    last_nav_date: date | None = Field(None, description="最晚 NAV 日期")
    coverage_ratio: float = Field(..., description="自然日覆盖率（近似质量指标）")
    adj_nav_points: int = Field(..., description="adj_nav 点数")
    unit_nav_fallback_points: int = Field(..., description="有 unit_nav 但缺 adj_nav 的点数")
    adj_nav_coverage_ratio: float = Field(..., description="复权净值覆盖率")
    max_gap_days: int = Field(..., description="相邻 NAV 日期最大间隔天数")
    spike_threshold: str = Field(..., description="按基金类型使用的单日跳变阈值")
    spike_count: int = Field(..., description="跳变次数")
    status: str = Field(..., description="整体质量状态：good/warning/poor")
    issues: list[NavQualityIssue] = Field(default_factory=list, description="质量问题列表")


class NavQualityOverviewItem(NavQualityResponse):
    """NAV quality overview row for the data quality panel."""

    fund_name: str = Field(..., description="基金名称")


class NavQualityOverviewResponse(BaseModel):
    """Paginated NAV quality overview for local funds."""

    items: list[NavQualityOverviewItem] = Field(..., description="基金 NAV 质量列表")
    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页条数")
    pages: int = Field(..., description="总页数")
    status_counts: dict[str, int] = Field(default_factory=dict, description="当前筛选条件下的质量状态计数")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=PaginatedFunds,
    summary="基金检索",
    description="分页查询基金列表，支持按代码、名称、类型、公司过滤。",
)
async def list_funds(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数"),
    fund_type: str | None = Query(default=None, description="基金类型过滤"),
    keyword: str | None = Query(default=None, description="代码或名称关键词"),
    company_id: str | None = Query(default=None, description="基金公司 ID"),
    status: str | None = Query(default=None, description="基金状态过滤"),
    db: AsyncSession = Depends(get_session),
) -> PaginatedFunds:
    """Paginated fund search with optional filters."""
    # Build base query
    query = select(Fund)
    count_query = select(func.count()).select_from(Fund)

    # Apply filters
    if fund_type:
        query = query.where(Fund.fund_type == fund_type)
        count_query = count_query.where(Fund.fund_type == fund_type)
    if keyword:
        # 转义 LIKE 通配符，防止用户输入 % 或 _ 导致意外匹配
        escaped = keyword.replace("%", r"\%").replace("_", r"\_")
        like_pattern = f"%{escaped}%"
        condition = Fund.code.ilike(like_pattern) | Fund.name.ilike(like_pattern)
        query = query.where(condition)
        count_query = count_query.where(condition)
    if company_id:
        query = query.where(Fund.company_id == company_id)
        count_query = count_query.where(Fund.company_id == company_id)
    if status:
        query = query.where(Fund.status == status)
        count_query = count_query.where(Fund.status == status)
    else:
        # 默认排除已删除的基金
        query = query.where(Fund.status != "deleted")
        count_query = count_query.where(Fund.status != "deleted")

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.order_by(Fund.code).offset(offset).limit(page_size)

    result = await db.execute(query)
    funds = result.scalars().all()

    pages = (total + page_size - 1) // page_size if total > 0 else 0

    return PaginatedFunds(
        items=[FundSummary.model_validate(f) for f in funds],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/options",
    response_model=FundOptionsResponse,
    summary="基金选项简表",
    description="返回本地数据库中的全量基金简表，供下拉选择器等场景使用。",
)
async def list_fund_options(
    db: AsyncSession = Depends(get_session),
) -> FundOptionsResponse:
    """Return full local fund options for selector UIs."""
    query = (
        select(
            Fund.code,
            Fund.name,
            Fund.fund_type,
            Fund.status,
            Fund.inception_date,
        )
        .where(Fund.status != "deleted")
        .order_by(Fund.code)
    )
    result = await db.execute(query)
    rows = result.all()

    return FundOptionsResponse(
        items=[
            FundOptionItem(
                code=row.code,
                name=row.name,
                fund_type=row.fund_type,
                status=row.status,
                inception_date=row.inception_date,
            )
            for row in rows
        ]
    )


# ---------------------------------------------------------------------------
# Online search + ingest (must be before /{code} to avoid route conflict)
# ---------------------------------------------------------------------------


class OnlineSearchResult(BaseModel):
    """在线搜索结果（来自天天基金，不一定在本地数据库中）。"""

    code: str = Field(..., description="基金代码")
    name: str = Field(..., description="基金名称")
    fund_type: str | None = Field(None, description="基金类型")
    in_database: bool = Field(..., description="是否已在本地数据库中")
    nav_status: str = Field(default="none", description="数据状态: none=未采集, partial=部分数据, full=全量数据")


class OnlineSearchResponse(BaseModel):
    """在线搜索响应。"""

    results: list[OnlineSearchResult] = Field(..., description="搜索结果")
    source: str = Field(default="multi_provider", description="数据来源")


class IngestResponse(BaseModel):
    """一键采集响应。"""

    status: str = Field(..., description="状态: success/pending/failed")
    fund_code: str = Field(..., description="基金代码")
    fund_name: str | None = Field(None, description="基金名称")
    message: str = Field(..., description="提示信息")
    task_id: str | None = Field(None, description="Celery 任务 ID，用于轮询采集进度")


def _all_provider_errors_are_not_found(exc: AllProvidersFailedError) -> bool:
    """Return True when every provider failed with not-found semantics."""
    return bool(exc.errors) and all(
        isinstance(err, ProviderNotFoundError) for _, err in exc.errors
    )


@router.get(
    "/online-search",
    response_model=OnlineSearchResponse,
    summary="在线搜索基金",
    description="从天天基金在线搜索基金，返回结果并标注是否已在本地数据库中。",
)
async def online_search(
    keyword: str = Query(..., min_length=1, description="基金代码或名称关键词"),
    db: AsyncSession = Depends(get_session),
) -> OnlineSearchResponse:
    """在线搜索基金，支持代码或名称模糊匹配。"""
    provider = build_default_composite_provider()
    eastmoney_provider = provider.providers[0]

    db_result = await db.execute(select(Fund.code))
    existing_codes = set(db_result.scalars().all())

    async def _append_result(code: str, name: str, fund_type: str | None = None) -> OnlineSearchResult:
        nav_status = "none"
        in_db = code in existing_codes
        if in_db:
            nav_status = await _get_nav_status(db, code)
        return OnlineSearchResult(
            code=code,
            name=name,
            fund_type=fund_type,
            in_database=in_db,
            nav_status=nav_status,
        )

    # 如果输入的是纯数字（基金代码），直接查元数据
    if keyword.isdigit() and len(keyword) == 6:
        try:
            meta, _source = await provider.fetch_fund_meta(keyword)
        except ProviderNotFoundError:
            return OnlineSearchResponse(results=[])
        except AllProvidersFailedError as exc:
            if _all_provider_errors_are_not_found(exc):
                return OnlineSearchResponse(results=[])
            raise HTTPException(
                status_code=http_status.HTTP_502_BAD_GATEWAY,
                detail=f"在线搜索暂时不可用：{exc}",
            ) from exc

        result = await _append_result(
            code=meta.code,
            name=meta.name,
            fund_type=meta.fund_type.value if meta.fund_type else None,
        )
        return OnlineSearchResponse(results=[result])

    results: list[OnlineSearchResult] = []
    search_error: Exception | None = None
    ranking_error: Exception | None = None

    try:
        search_results = await eastmoney_provider.search_funds(keyword, limit=20)
    except Exception as exc:
        search_results = []
        search_error = exc

    seen_codes: set[str] = set()
    for item in search_results:
        code = item.get("code", "")
        name = item.get("name", "")
        fund_type = item.get("fund_type") or None
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        results.append(await _append_result(code=code, name=name, fund_type=fund_type))
        if len(results) >= 20:
            break

    if len(results) < 20:
        try:
            rankings = await eastmoney_provider.fetch_fund_ranking(
                fund_type="all",
                sort_by="6yzf",
                page=1,
                page_size=100,
            )
            for item in rankings:
                code = item.get("code", "")
                name = item.get("name", "")
                if not code or code in seen_codes:
                    continue
                if keyword.lower() in name.lower() or keyword in code:
                    seen_codes.add(code)
                    results.append(await _append_result(code=code, name=name))
                if len(results) >= 20:
                    break
        except Exception as exc:
            ranking_error = exc

    if results:
        return OnlineSearchResponse(results=results)

    if search_error and ranking_error:
        raise HTTPException(
            status_code=http_status.HTTP_502_BAD_GATEWAY,
            detail=(
                "在线搜索暂时不可用：搜索接口与排行榜回退均失败。"
                f" search={search_error}; ranking={ranking_error}"
            ),
        )

    return OnlineSearchResponse(results=[])


async def _get_nav_status(db: AsyncSession, code: str) -> str:
    """判断基金的 NAV 数据状态: none/partial/full。"""
    from app.data.models.fund_nav import FundNav

    # 查询 NAV 数据范围
    result = await db.execute(
        select(
            func.min(FundNav.trade_date),
            func.max(FundNav.trade_date),
            func.count(),
        ).where(FundNav.fund_code == code)
    )
    row = result.one()
    min_date, max_date, count = row

    if count == 0:
        return "none"

    # 获取基金成立日期来判断是否全量采集
    fund_result = await db.execute(
        select(Fund.inception_date).where(Fund.code == code)
    )
    inception_date = fund_result.scalar_one_or_none()

    if min_date and max_date and inception_date:
        # 如果最早数据距成立日期不超过 30 天，认为是全量采集
        gap_from_inception = (min_date - inception_date).days
        if gap_from_inception <= 30:
            return "full"

    # 没有成立日期时，用跨度 > 2 年作为回退判断
    if min_date and max_date:
        span_days = (max_date - min_date).days
        if span_days > 730:
            return "full"

    return "partial"


@router.post(
    "/ingest/{code}",
    response_model=IngestResponse,
    summary="一键采集基金",
    description="采集指定基金的元数据并触发全量净值采集任务。优先使用 Celery 异步执行，Celery 不可用时同步采集。",
)
async def ingest_fund(code: str) -> IngestResponse:
    """一键采集：获取基金元数据写入数据库，并触发全量净值采集。"""
    import asyncio
    import re
    from datetime import date as date_cls, timedelta

    from app.data.repositories.fund_repo import FundRepo
    from app.data.repositories.nav_repo import NavRepo
    from app.data.session import get_sessionmaker
    from app.tasks.ingest import update_daily_nav

    # 基金代码格式校验：必须是6位数字
    if not re.match(r"^\d{6}$", code):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"无效的基金代码格式: {code}，必须为6位数字",
        )

    provider = build_default_composite_provider()
    factory = get_sessionmaker()

    try:
        meta, source = await provider.fetch_fund_meta(code)
    except ProviderNotFoundError as exc:
        raise NotFoundError(f"基金 {code} 不存在") from exc
    except AllProvidersFailedError as exc:
        if _all_provider_errors_are_not_found(exc):
            raise NotFoundError(f"基金 {code} 不存在") from exc
        raise HTTPException(
            status_code=http_status.HTTP_502_BAD_GATEWAY,
            detail=f"无法获取基金 {code} 的信息: {exc}",
        ) from exc

    fund_repo = FundRepo()
    record = meta.model_dump(exclude_none=False)
    record["source"] = source

    async with factory() as session:
        await fund_repo.upsert_many(session, [record])
        await session.commit()

    await cache.invalidate_fund_meta(code)

    try:
        task = update_daily_nav.apply_async(
            kwargs={"fund_code": code, "full": True},
            retry=False,
        )
        return IngestResponse(
            status="pending",
            fund_code=code,
            fund_name=meta.name,
            message=f"元数据已入库：{meta.name}，全量净值采集任务已提交（异步）。",
            task_id=task.id,
        )
    except Exception:
        pass

    try:
        max_sync_lookback = timedelta(days=365 * 3)
        if meta.inception_date and (date_cls.today() - meta.inception_date) <= max_sync_lookback:
            start_date = meta.inception_date
        else:
            start_date = date_cls.today() - max_sync_lookback

        nav_records, nav_source = await asyncio.wait_for(
            provider.fetch_nav_history(code, start_date, date_cls.today()),
            timeout=55.0,
        )

        if not nav_records:
            await asyncio.sleep(3)
            nav_records, nav_source = await asyncio.wait_for(
                provider.fetch_nav_history(code, start_date, date_cls.today()),
                timeout=45.0,
            )

        if not nav_records:
            return IngestResponse(
                status="failed",
                fund_code=code,
                fund_name=meta.name,
                message=f"元数据已入库：{meta.name}，但未获取到净值数据，已保留原有本地净值。",
                task_id=None,
            )

        nav_repo = NavRepo()
        nav_data = [
            {
                "fund_code": r.fund_code,
                "trade_date": r.trade_date,
                "unit_nav": r.unit_nav,
                "accum_nav": r.accum_nav,
                "adj_nav": r.adj_nav,
                "daily_return": r.daily_return,
                "source": nav_source,
                "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            }
            for r in nav_records
        ]
        async with factory() as session:
            await nav_repo.upsert_many(session, nav_data)
            await session.commit()

        await cache.invalidate_nav(code)
        return IngestResponse(
            status="success",
            fund_code=code,
            fund_name=meta.name,
            message=f"采集完成：{meta.name}，共获取 {len(nav_records)} 条净值记录（从 {start_date} 至今）。",
            task_id=None,
        )
    except asyncio.TimeoutError:
        return IngestResponse(
            status="failed",
            fund_code=code,
            fund_name=meta.name,
            message=f"元数据已入库：{meta.name}，同步采集超时（基金历史较长），已保留原有本地净值。",
            task_id=None,
        )
    except AllProvidersFailedError as exc:
        return IngestResponse(
            status="failed",
            fund_code=code,
            fund_name=meta.name,
            message=f"元数据已入库：{meta.name}，同步采集失败: {exc}，已保留原有本地净值。",
            task_id=None,
        )
    except Exception as exc:
        return IngestResponse(
            status="failed",
            fund_code=code,
            fund_name=meta.name,
            message=f"元数据已入库：{meta.name}，同步采集失败: {exc}，已保留原有本地净值。",
            task_id=None,
        )


# ---------------------------------------------------------------------------
# Ingest task status polling
# ---------------------------------------------------------------------------


class IngestTaskStatus(BaseModel):
    """采集任务状态。"""

    task_id: str = Field(..., description="任务 ID")
    state: str = Field(..., description="任务状态: PENDING/STARTED/SUCCESS/FAILURE")
    progress: str | None = Field(None, description="进度描述")
    result: dict | None = Field(None, description="任务结果（完成时）")


@router.get(
    "/ingest-status/{task_id}",
    response_model=IngestTaskStatus,
    summary="查询采集任务状态",
    description="根据任务 ID 轮询采集进度。",
)
async def get_ingest_status(task_id: str) -> IngestTaskStatus:
    """查询 Celery 采集任务的执行状态。"""
    from app.tasks.celery_app import celery_app as celery

    result = celery.AsyncResult(task_id)
    state = result.state  # PENDING, STARTED, SUCCESS, FAILURE, RETRY

    progress: str | None = None
    task_result: dict | None = None

    if state == "PENDING":
        progress = "任务排队中..."
    elif state == "STARTED":
        progress = "正在采集净值数据..."
    elif state == "SUCCESS":
        progress = "采集完成"
        task_result = result.result if isinstance(result.result, dict) else None
    elif state == "FAILURE":
        progress = f"采集失败: {str(result.result)}"
    elif state == "RETRY":
        progress = "正在重试..."

    return IngestTaskStatus(
        task_id=task_id,
        state=state,
        progress=progress,
        result=task_result,
    )


# ---------------------------------------------------------------------------
# Fund detail & NAV quality endpoints
# ---------------------------------------------------------------------------


def _nav_value_for_quality(row: FundNav) -> Decimal | None:
    """Return the NAV value used for quality checks, preferring adjusted NAV."""
    return row.adj_nav if row.adj_nav is not None else row.unit_nav


def _resolve_nav_quality_window(
    start_date: date | None,
    end_date: date | None,
) -> tuple[date, date]:
    """Resolve the NAV quality date window, defaulting to the latest year."""
    resolved_end = end_date or date.today()
    resolved_start = start_date or (resolved_end - timedelta(days=365))
    if resolved_start > resolved_end:
        raise HTTPException(status_code=422, detail="start_date must be <= end_date")
    return resolved_start, resolved_end


def _build_nav_quality_snapshot(
    *,
    code: str,
    fund_type: str | None,
    start_date: date,
    end_date: date,
    nav_rows: list[FundNav],
) -> NavQualityResponse:
    """Build a reusable NAV quality snapshot from already loaded NAV rows."""
    from app.domain.backtest.data_quality import (
        DEFAULT_SPIKE_THRESHOLD,
        _spike_threshold_for_fund_type,
    )

    total_calendar_days = max((end_date - start_date).days + 1, 1)
    valid_rows = [row for row in nav_rows if _nav_value_for_quality(row) is not None]
    total_nav_points = len(valid_rows)
    adj_nav_points = sum(1 for row in valid_rows if row.adj_nav is not None)
    unit_nav_fallback_points = sum(
        1 for row in valid_rows if row.adj_nav is None and row.unit_nav is not None
    )
    coverage_ratio = round(total_nav_points / total_calendar_days, 6)
    adj_nav_coverage_ratio = round(adj_nav_points / total_nav_points, 6) if total_nav_points else 0.0
    first_nav_date = valid_rows[0].trade_date if valid_rows else None
    last_nav_date = valid_rows[-1].trade_date if valid_rows else None

    issues: list[NavQualityIssue] = []
    if total_nav_points == 0:
        issues.append(
            NavQualityIssue(
                issue_type="missing_all",
                start_date=start_date,
                end_date=end_date,
                severity="poor",
                message="检查区间内没有有效 NAV 数据，请先采集或修复数据。",
            )
        )

    if unit_nav_fallback_points > 0:
        fallback_dates = [row.trade_date for row in valid_rows if row.adj_nav is None and row.unit_nav is not None]
        issues.append(
            NavQualityIssue(
                issue_type="adj_nav_missing",
                start_date=fallback_dates[0],
                end_date=fallback_dates[-1],
                severity="warning",
                message=f"{unit_nav_fallback_points} 个净值点缺少 adj_nav，计算时可能回退 unit_nav。",
            )
        )

    max_gap_days = 0
    for prev, curr in zip(valid_rows, valid_rows[1:]):
        gap_days = (curr.trade_date - prev.trade_date).days - 1
        if gap_days > max_gap_days:
            max_gap_days = gap_days
        if gap_days >= 10:
            issues.append(
                NavQualityIssue(
                    issue_type="missing_gap",
                    start_date=prev.trade_date,
                    end_date=curr.trade_date,
                    severity="warning" if gap_days < 30 else "poor",
                    message=f"相邻 NAV 日期间隔 {gap_days} 天，可能存在连续缺失。",
                )
            )

    threshold = _spike_threshold_for_fund_type(fund_type, default=DEFAULT_SPIKE_THRESHOLD)
    spike_count = 0
    for prev, curr in zip(valid_rows, valid_rows[1:]):
        prev_nav = _nav_value_for_quality(prev)
        curr_nav = _nav_value_for_quality(curr)
        if prev_nav is None or curr_nav is None or prev_nav <= 0:
            continue
        daily_change = abs((curr_nav - prev_nav) / prev_nav)
        if daily_change > threshold:
            spike_count += 1
            if len([issue for issue in issues if issue.issue_type == "spike"]) < 20:
                issues.append(
                    NavQualityIssue(
                        issue_type="spike",
                        trade_date=curr.trade_date,
                        severity="warning",
                        message=f"NAV 单日跳变 {daily_change:.2%}，超过基金类型阈值 ±{threshold}。",
                    )
                )

    status = "good"
    if total_nav_points == 0 or coverage_ratio < 0.2 or max_gap_days >= 30:
        status = "poor"
    elif coverage_ratio < 0.5 or unit_nav_fallback_points > 0 or spike_count > 0 or max_gap_days >= 10:
        status = "warning"

    return NavQualityResponse(
        fund_code=code,
        fund_type=fund_type,
        start_date=start_date,
        end_date=end_date,
        total_calendar_days=total_calendar_days,
        total_nav_points=total_nav_points,
        first_nav_date=first_nav_date,
        last_nav_date=last_nav_date,
        coverage_ratio=coverage_ratio,
        adj_nav_points=adj_nav_points,
        unit_nav_fallback_points=unit_nav_fallback_points,
        adj_nav_coverage_ratio=adj_nav_coverage_ratio,
        max_gap_days=max_gap_days,
        spike_threshold=str(threshold),
        spike_count=spike_count,
        status=status,
        issues=issues,
    )


@router.get(
    "/nav-quality-overview",
    response_model=NavQualityOverviewResponse,
    summary="基金 NAV 数据质量概览",
    description="分页返回本地基金的 NAV 覆盖率、复权覆盖率、缺口和跳变摘要，供数据质量检查面板使用。",
)
async def list_fund_nav_quality_overview(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数"),
    fund_type: str | None = Query(default=None, description="基金类型过滤"),
    status: str | None = Query(default=None, description="质量状态过滤：good/warning/poor"),
    keyword: str | None = Query(default=None, description="代码或名称关键词"),
    start_date: date | None = Query(default=None, description="起始日期 (YYYY-MM-DD)，默认近一年"),
    end_date: date | None = Query(default=None, description="结束日期 (YYYY-MM-DD)，默认今天"),
    db: AsyncSession = Depends(get_session),
) -> NavQualityOverviewResponse:
    """Return a paginated NAV quality overview across local funds."""
    start_date, end_date = _resolve_nav_quality_window(start_date, end_date)

    fund_query = select(Fund.code, Fund.name, Fund.fund_type).where(Fund.status != "deleted")
    if fund_type:
        fund_query = fund_query.where(Fund.fund_type == fund_type)
    if keyword:
        escaped = keyword.replace("%", r"\%").replace("_", r"\_")
        like_pattern = f"%{escaped}%"
        fund_query = fund_query.where(Fund.code.ilike(like_pattern) | Fund.name.ilike(like_pattern))

    fund_query = fund_query.order_by(Fund.code)
    fund_result = await db.execute(fund_query)
    fund_rows = list(fund_result.all())

    if not fund_rows:
        return NavQualityOverviewResponse(
            items=[],
            total=0,
            page=page,
            page_size=page_size,
            pages=0,
            status_counts={"good": 0, "warning": 0, "poor": 0},
        )

    fund_codes = [row.code for row in fund_rows]
    nav_result = await db.execute(
        select(FundNav)
        .where(
            FundNav.fund_code.in_(fund_codes),
            FundNav.trade_date >= start_date,
            FundNav.trade_date <= end_date,
        )
        .order_by(FundNav.fund_code, FundNav.trade_date)
    )
    nav_rows_by_code: dict[str, list[FundNav]] = {code: [] for code in fund_codes}
    for nav_row in nav_result.scalars().all():
        nav_rows_by_code.setdefault(nav_row.fund_code, []).append(nav_row)

    all_items: list[NavQualityOverviewItem] = []
    status_counts = {"good": 0, "warning": 0, "poor": 0}
    for fund_row in fund_rows:
        snapshot = _build_nav_quality_snapshot(
            code=fund_row.code,
            fund_type=fund_row.fund_type,
            start_date=start_date,
            end_date=end_date,
            nav_rows=nav_rows_by_code.get(fund_row.code, []),
        )
        status_counts[snapshot.status] = status_counts.get(snapshot.status, 0) + 1
        if status and snapshot.status != status:
            continue
        all_items.append(
            NavQualityOverviewItem(
                **snapshot.model_dump(),
                fund_name=fund_row.name,
            )
        )

    total = len(all_items)
    pages = (total + page_size - 1) // page_size if total > 0 else 0
    offset = (page - 1) * page_size
    return NavQualityOverviewResponse(
        items=all_items[offset : offset + page_size],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
        status_counts=status_counts,
    )


@router.get(
    "/{code}/nav-quality",
    response_model=NavQualityResponse,
    summary="基金 NAV 数据质量快照",
    description="返回单只基金 NAV 覆盖率、复权覆盖率、缺口和按基金类型阈值识别的跳变摘要。",
)
async def get_fund_nav_quality(
    code: str,
    start_date: date | None = Query(default=None, description="起始日期 (YYYY-MM-DD)，默认近一年"),
    end_date: date | None = Query(default=None, description="结束日期 (YYYY-MM-DD)，默认今天"),
    db: AsyncSession = Depends(get_session),
) -> NavQualityResponse:
    """Build a lightweight, auditable NAV quality snapshot for one fund."""
    start_date, end_date = _resolve_nav_quality_window(start_date, end_date)

    fund_result = await db.execute(
        select(Fund.code, Fund.fund_type).where(Fund.code == code)
    )
    fund_row = fund_result.one_or_none()
    if fund_row is None:
        raise HTTPException(status_code=404, detail=f"基金 {code} 不存在")

    nav_result = await db.execute(
        select(FundNav)
        .where(
            FundNav.fund_code == code,
            FundNav.trade_date >= start_date,
            FundNav.trade_date <= end_date,
        )
        .order_by(FundNav.trade_date)
    )
    nav_rows = list(nav_result.scalars().all())

    return _build_nav_quality_snapshot(
        code=code,
        fund_type=fund_row.fund_type,
        start_date=start_date,
        end_date=end_date,
        nav_rows=nav_rows,
    )


# ---------------------------------------------------------------------------
# Delete fund (soft-delete: mark as "deleted" and remove local data)
# ---------------------------------------------------------------------------


class DeleteFundResponse(BaseModel):
    """删除基金响应。"""

    status: str = Field(..., description="状态: success/failed")
    fund_code: str = Field(..., description="基金代码")
    message: str = Field(..., description="提示信息")


@router.delete(
    "/{code}",
    response_model=DeleteFundResponse,
    summary="删除本地基金",
    description="删除本地基金数据（净值、持仓、分红等），并将基金状态标记为 deleted，后续每日更新将跳过该基金。",
)
async def delete_fund(
    code: str,
    db: AsyncSession = Depends(get_session),
) -> DeleteFundResponse:
    """硬删除基金：彻底从数据库中移除基金及其所有关联数据。"""
    from sqlalchemy import delete

    from app.data.models.fund_dividends import FundDividend
    from app.data.models.fund_holdings import FundHolding
    from app.data.models.fund_nav import FundNav

    # 检查基金是否存在
    result = await db.execute(select(Fund).where(Fund.code == code))
    fund = result.scalar_one_or_none()
    if fund is None:
        raise HTTPException(status_code=404, detail=f"基金 {code} 不存在")

    fund_name = fund.name

    # 1. 删除净值数据
    await db.execute(delete(FundNav).where(FundNav.fund_code == code))

    # 2. 删除持仓数据
    await db.execute(delete(FundHolding).where(FundHolding.fund_code == code))

    # 3. 删除分红数据
    await db.execute(delete(FundDividend).where(FundDividend.fund_code == code))

    # 4. 删除 funds 表记录（硬删除）
    await db.execute(delete(Fund).where(Fund.code == code))

    await db.commit()

    # 5. 清除缓存
    await cache.invalidate_fund_meta(code)

    return DeleteFundResponse(
        status="success",
        fund_code=code,
        message=f"基金 {fund_name}({code}) 已彻底删除。",
    )


@router.get(
    "/{code}",
    response_model=FundDetail,
    summary="基金详情",
    description="根据基金代码获取完整基金信息，优先从 Redis 缓存读取。",
)
async def get_fund(
    code: str,
    db: AsyncSession = Depends(get_session),
) -> FundDetail:
    """Get fund detail by code, with Redis cache integration.

    If the fund is not in the local database, attempts to fetch it
    from Eastmoney and persist it before returning.
    """
    # Try cache first
    cached = await cache.get_fund_meta(code)
    if cached is not None:
        return FundDetail(**cached)

    # Cache miss — query database
    result = await db.execute(select(Fund).where(Fund.code == code))
    fund = result.scalar_one_or_none()

    if fund is None:
        # Not in DB — try to fetch from Eastmoney and auto-ingest metadata
        # (元数据轻量，几秒内完成，作为懒加载是合理的)
        try:
            from app.data.providers.eastmoney import EastmoneyProvider
            from app.data.repositories.fund_repo import FundRepo
            from app.data.session import get_sessionmaker

            provider = EastmoneyProvider()
            meta = await provider.fetch_fund_meta(code)

            # Persist to database
            fund_repo = FundRepo()
            record = meta.model_dump(exclude_none=False)
            record["source"] = "eastmoney"

            factory = get_sessionmaker()
            async with factory() as write_session:
                await fund_repo.upsert_many(write_session, [record])
                await write_session.commit()

            # Re-query to get the ORM object
            result = await db.execute(select(Fund).where(Fund.code == code))
            fund = result.scalar_one_or_none()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug(
                "自动采集基金 %s 元数据失败: %s", code, exc
            )

    if fund is None:
        raise HTTPException(status_code=404, detail=f"基金 {code} 不存在")

    detail = FundDetail.model_validate(fund)

    # Write to cache
    await cache.set_fund_meta(code, detail.model_dump(mode="json"))

    return detail


@router.get(
    "/{code}/nav",
    response_model=NavResponse,
    summary="基金净值查询",
    description="查询指定基金的净值时间序列，支持日期范围过滤。默认返回近 30 天数据。",
)
async def get_fund_nav(
    code: str,
    start_date: date | None = Query(default=None, description="起始日期 (YYYY-MM-DD)"),
    end_date: date | None = Query(default=None, description="结束日期 (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_session),
) -> NavResponse:
    """Get NAV time series for a fund, with Redis cache integration.

    If the fund has no NAV data locally, attempts to fetch from Eastmoney.
    """
    # Default date range: last 30 days
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=30)

    # Check if fund exists; if not, auto-ingest metadata first
    fund_result = await db.execute(select(Fund.code).where(Fund.code == code))
    if fund_result.scalar_one_or_none() is None:
        # Try auto-ingest metadata
        try:
            from app.data.providers.eastmoney import EastmoneyProvider
            from app.data.repositories.fund_repo import FundRepo
            from app.data.session import get_sessionmaker

            provider = EastmoneyProvider()
            meta = await provider.fetch_fund_meta(code)
            fund_repo = FundRepo()
            record = meta.model_dump(exclude_none=False)
            record["source"] = "eastmoney"
            factory = get_sessionmaker()
            async with factory() as ws:
                await fund_repo.upsert_many(ws, [record])
                await ws.commit()
        except Exception:
            raise HTTPException(status_code=404, detail=f"基金 {code} 不存在")

    # Try cache first
    cached_records = await cache.get_nav_records(code, start_date, end_date)
    if cached_records is not None:
        return NavResponse(
            fund_code=code,
            start_date=start_date,
            end_date=end_date,
            count=len(cached_records),
            records=[NavItem(**r) for r in cached_records],
        )

    # Cache miss — query database
    query = (
        select(FundNav)
        .where(
            FundNav.fund_code == code,
            FundNav.trade_date >= start_date,
            FundNav.trade_date <= end_date,
        )
        .order_by(FundNav.trade_date)
    )
    result = await db.execute(query)
    nav_rows = result.scalars().all()

    # If no NAV data locally, return empty result with hint
    # (auto-fetch removed: GET should not have write side effects;
    #  user should use POST /funds/ingest/{code} to trigger collection)

    records = [NavItem.model_validate(row) for row in nav_rows]

    # Write to cache (only if we have data)
    if records:
        cache_data: list[dict[str, Any]] = [r.model_dump(mode="json") for r in records]
        await cache.set_nav_records(code, start_date, end_date, cache_data)

    return NavResponse(
        fund_code=code,
        start_date=start_date,
        end_date=end_date,
        count=len(records),
        needs_ingest=len(records) == 0,
        records=records,
    )


# ---------------------------------------------------------------------------
# 单基金持仓分布
# ---------------------------------------------------------------------------


class HoldingPositionItem(BaseModel):
    """单只持仓股票。"""

    stock_code: str = Field(..., description="股票代码")
    stock_name: str | None = Field(None, description="股票名称")
    weight: float = Field(..., description="持仓权重（如 0.08 表示 8%）")
    shares: float | None = Field(None, description="持有股数")
    market_value: float | None = Field(None, description="市值（元）")
    industry: str | None = Field(None, description="所属行业")


class IndustryDistItem(BaseModel):
    """行业分布项。"""

    industry: str = Field(..., description="行业名称")
    weight: float = Field(..., description="行业总权重")
    stock_count: int = Field(..., description="该行业持股数量")


class FundHoldingsResponse(BaseModel):
    """单基金持仓分布响应。"""

    fund_code: str = Field(..., description="基金代码")
    report_date: str | None = Field(None, description="报告日期")
    positions: list[HoldingPositionItem] = Field(default_factory=list, description="持仓列表（按权重降序）")
    industry_distribution: list[IndustryDistItem] = Field(default_factory=list, description="行业分布")
    top5_concentration: float = Field(0.0, description="前5大持仓集中度")
    top10_concentration: float = Field(0.0, description="前10大持仓集中度")
    total_stocks: int = Field(0, description="持股总数")


@router.get(
    "/{code}/holdings",
    response_model=FundHoldingsResponse,
    summary="基金持仓分布",
    description="获取单只基金最新一期的持仓分布数据，包括重仓股和行业分布。",
)
async def get_fund_holdings(
    code: str,
    db: AsyncSession = Depends(get_session),
) -> FundHoldingsResponse:
    """Get latest holdings distribution for a single fund.

    If no local holdings data exists, attempts to auto-fetch from Eastmoney.
    """
    from sqlalchemy import and_, desc

    from app.data.models.fund_holdings import FundHolding

    # 查找该基金最新的报告日期
    result = await db.execute(
        select(func.max(FundHolding.report_date)).where(
            FundHolding.fund_code == code
        )
    )
    report_date = result.scalar_one_or_none()

    # 本地无数据，尝试自动采集
    if report_date is None:
        report_date = await _auto_fetch_single_fund_holdings(code)
        if report_date is None:
            return FundHoldingsResponse(fund_code=code)
        # 需要新 session 读取刚写入的数据
        await db.close()
        from app.data.session import get_sessionmaker

        factory = get_sessionmaker()
        async with factory() as fresh_db:
            return await _build_holdings_response(fresh_db, code, report_date)

    return await _build_holdings_response(db, code, report_date)


async def _auto_fetch_single_fund_holdings(code: str):
    """尝试从天天基金自动采集单只基金的持仓数据。"""
    from datetime import date as date_cls

    from app.data.providers.eastmoney import EastmoneyProvider
    from app.data.repositories.holding_repo import HoldingRepo
    from app.data.session import get_sessionmaker

    provider = EastmoneyProvider()
    repo = HoldingRepo()
    factory = get_sessionmaker()

    # 确定最新季度
    today = date_cls.today()
    month = today.month
    year = today.year
    if month <= 3:
        quarter = f"{year - 1}-Q4"
    elif month <= 6:
        quarter = f"{year}-Q1"
    elif month <= 9:
        quarter = f"{year}-Q2"
    else:
        quarter = f"{year}-Q3"

    try:
        snapshot = await provider.fetch_holdings(code, quarter)
        if snapshot.positions:
            records = []
            for pos in snapshot.positions:
                records.append({
                    "fund_code": snapshot.fund_code,
                    "report_date": snapshot.report_date,
                    "stock_code": pos.stock_code or "UNKNOWN",
                    "stock_name": pos.stock_name,
                    "weight": pos.weight,
                    "shares": pos.shares,
                    "market_value": pos.market_value,
                    "industry": pos.industry,
                })
            async with factory() as session:
                await repo.upsert_many(session, records)
                await session.commit()
            return snapshot.report_date
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug(
            "自动采集基金 %s 持仓失败 (quarter=%s): %s", code, quarter, exc
        )

    return None


async def _build_holdings_response(
    db: AsyncSession, code: str, report_date
) -> FundHoldingsResponse:
    """从数据库构建持仓分布响应。"""
    from sqlalchemy import and_, desc

    from app.data.models.fund_holdings import FundHolding

    stmt = (
        select(FundHolding)
        .where(
            and_(
                FundHolding.fund_code == code,
                FundHolding.report_date == report_date,
            )
        )
        .order_by(desc(FundHolding.weight))
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    if not rows:
        return FundHoldingsResponse(fund_code=code, report_date=str(report_date))

    # 构建持仓列表
    raw_positions = [
        {
            "stock_code": row.stock_code,
            "stock_name": row.stock_name,
            "weight": float(row.weight) if row.weight else 0.0,
            "shares": float(row.shares) if row.shares else None,
            "market_value": float(row.market_value) if row.market_value else None,
            "industry": row.industry,
        }
        for row in rows
    ]

    # 补全缺失的行业分类
    try:
        from app.services.stock_industry import enrich_holdings_with_industry

        raw_positions = await enrich_holdings_with_industry(raw_positions)

        # 将补全的行业信息回写数据库（异步，不阻塞响应）
        await _update_holdings_industry(db, code, report_date, raw_positions)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("行业分类补全失败: fund=%s, error=%s", code, exc)

    positions = []
    industry_map: dict[str, dict] = {}

    for p in raw_positions:
        w = p["weight"]
        positions.append(
            HoldingPositionItem(
                stock_code=p["stock_code"],
                stock_name=p["stock_name"],
                weight=round(w, 6),
                shares=p["shares"],
                market_value=p["market_value"],
                industry=p["industry"],
            )
        )
        # 行业汇总
        ind = p["industry"] or "未知"
        if ind in industry_map:
            industry_map[ind]["weight"] += w
            industry_map[ind]["count"] += 1
        else:
            industry_map[ind] = {"weight": w, "count": 1}

    # 集中度
    weights_sorted = sorted([p["weight"] for p in raw_positions if p["weight"]], reverse=True)
    top5 = sum(weights_sorted[:5])
    top10 = sum(weights_sorted[:10])

    # 行业分布按权重降序
    industry_dist = sorted(
        [
            IndustryDistItem(industry=k, weight=round(v["weight"], 6), stock_count=v["count"])
            for k, v in industry_map.items()
        ],
        key=lambda x: x.weight,
        reverse=True,
    )

    return FundHoldingsResponse(
        fund_code=code,
        report_date=str(report_date),
        positions=positions,
        industry_distribution=industry_dist,
        top5_concentration=round(top5, 4),
        top10_concentration=round(top10, 4),
        total_stocks=len(positions),
    )


async def _update_holdings_industry(
    db: AsyncSession, code: str, report_date, positions: list[dict]
) -> None:
    """将补全的行业信息回写到数据库。"""
    from sqlalchemy import and_, update

    from app.data.models.fund_holdings import FundHolding

    for p in positions:
        if p.get("industry") and p.get("stock_code"):
            try:
                await db.execute(
                    update(FundHolding)
                    .where(
                        and_(
                            FundHolding.fund_code == code,
                            FundHolding.report_date == report_date,
                            FundHolding.stock_code == p["stock_code"],
                        )
                    )
                    .values(industry=p["industry"])
                )
            except Exception:
                pass
    try:
        await db.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 估值分析
# ---------------------------------------------------------------------------


class ValuationItem(BaseModel):
    """估值分析结果。"""

    fund_code: str
    current_nav: float
    percentile: float = Field(..., description="历史百分位 (0~1)")
    zone: str = Field(..., description="估值区间: low/normal/high")
    suggestion: str = Field(..., description="操作建议")
    history_days: int
    history_low: float
    history_high: float
    history_median: float


class ValuationResponse(BaseModel):
    """估值分析响应。"""

    funds: list[ValuationItem]


class ValuationRequest(BaseModel):
    """估值分析请求。"""

    fund_codes: list[str] = Field(..., min_length=1, max_length=20, description="基金代码列表")
    lookback_days: int = Field(default=750, ge=60, le=2520, description="回看天数（默认约3年）")


@router.post(
    "/valuation",
    response_model=ValuationResponse,
    summary="估值分析（净值百分位代理）",
    description=(
        "分析基金净值的历史百分位，判断当前处于历史低位/正常/高位区间。"
        "注意：本接口使用净值百分位作为估值代理，仅对被动指数基金有较好参考价值。"
        "对主动管理型基金，净值上升是正常收益积累，高百分位不代表高估。"
    ),
)
async def analyze_valuation(
    body: ValuationRequest,
    db: AsyncSession = Depends(get_session),
) -> ValuationResponse:
    """Analyze historical valuation percentile for funds."""
    from sqlalchemy import and_

    from app.services.valuation_service import compute_batch_valuation

    today = date.today()
    start = today - timedelta(days=body.lookback_days)

    # 加载各基金的 NAV 数据
    funds_nav: dict[str, dict[date, Decimal]] = {}
    for code in body.fund_codes[:20]:  # 最多20只
        result = await db.execute(
            select(FundNav).where(
                and_(
                    FundNav.fund_code == code,
                    FundNav.trade_date >= start,
                    FundNav.trade_date <= today,
                )
            ).order_by(FundNav.trade_date)
        )
        rows = result.scalars().all()
        if rows:
            funds_nav[code] = {
                row.trade_date: row.unit_nav for row in rows
                if row.unit_nav is not None
            }

    if not funds_nav:
        return ValuationResponse(funds=[])

    # 计算估值
    results = compute_batch_valuation(funds_nav)

    return ValuationResponse(
        funds=[
            ValuationItem(
                fund_code=r.fund_code,
                current_nav=r.current_nav,
                percentile=round(r.percentile, 4),
                zone=r.zone,
                suggestion=r.suggestion,
                history_days=r.history_days,
                history_low=r.history_low,
                history_high=r.history_high,
                history_median=r.history_median,
            )
            for r in results
        ],
    )
