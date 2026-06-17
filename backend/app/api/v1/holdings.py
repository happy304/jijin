"""持仓分析 API 端点。

提供：
- ``POST /holdings/penetrate``     — 持仓穿透分析
- ``POST /holdings/similarity``    — 持仓相似度计算
- ``GET /holdings/by-stock``       — 股票选基（反向查询）
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, and_, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.fund_holdings import FundHolding
from app.data.session import get_session

router = APIRouter(prefix="/holdings", tags=["holdings"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class PenetrateRequest(BaseModel):
    """持仓穿透请求。"""

    fund_codes: list[str] = Field(..., min_length=1, max_length=20, description="基金代码列表")
    fund_weights: dict[str, float] | None = Field(None, description="各基金权重，默认等权")
    report_date: date | None = Field(None, description="报告日期，默认最新")


class StockExposureItem(BaseModel):
    """股票暴露。"""

    stock_code: str
    stock_name: str | None = None
    weight: float
    funds: list[str]
    industry: str | None = None


class IndustryItem(BaseModel):
    """行业分布。"""

    industry: str
    weight: float
    stock_count: int


class PenetrateResponse(BaseModel):
    """持仓穿透响应。"""

    stock_exposures: list[StockExposureItem] = Field(default_factory=list)
    industry_distribution: list[IndustryItem] = Field(default_factory=list)
    top5_concentration: float = 0.0
    top10_concentration: float = 0.0
    hhi: float = 0.0
    total_stocks: int = 0


class SimilarityRequest(BaseModel):
    """持仓相似度请求。"""

    fund_codes: list[str] = Field(..., min_length=2, max_length=20, description="基金代码列表（至少2只）")
    report_date: date | None = Field(None, description="报告日期，默认最新")


class SimilarityItem(BaseModel):
    """相似度结果项。"""

    fund_a: str
    fund_b: str
    cosine_similarity: float
    overlap_count: int
    overlap_stocks: list[str]


class SimilarityResponse(BaseModel):
    """持仓相似度响应。"""

    pairs: list[SimilarityItem]
    avg_similarity: float = Field(..., description="平均相似度")
    max_similarity: float = Field(..., description="最大相似度")
    warning: str | None = Field(None, description="高相似度警告")


class StockFundItem(BaseModel):
    """持有某股票的基金。"""

    fund_code: str
    fund_name: str | None = None
    weight: float
    report_date: str


class StockFundResponse(BaseModel):
    """股票选基响应。"""

    stock_code: str
    stock_name: str | None = None
    funds: list[StockFundItem]
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/penetrate",
    response_model=PenetrateResponse,
    summary="持仓穿透",
    description="分析基金组合的底层股票暴露和行业分布。",
)
async def penetrate_holdings(
    body: PenetrateRequest,
    db: AsyncSession = Depends(get_session),
) -> PenetrateResponse:
    """Analyze underlying stock exposures of a fund portfolio."""
    from app.services.holdings_analysis import analyze_portfolio_holdings

    # 确定报告日期
    report_date = body.report_date
    if report_date is None:
        # 使用最新的报告日期
        result = await db.execute(
            select(func.max(FundHolding.report_date)).where(
                FundHolding.fund_code.in_(body.fund_codes)
            )
        )
        report_date = result.scalar_one_or_none()

    # 如果本地无持仓数据，尝试自动采集
    if report_date is None:
        report_date = await _auto_fetch_holdings(body.fund_codes)
        if report_date is None:
            raise HTTPException(status_code=404, detail="未找到持仓数据")
        # 刷新 session 以读取新写入的数据
        await db.close()
        from app.data.session import get_sessionmaker
        factory = get_sessionmaker()
        async with factory() as fresh_db:
            return await _do_penetrate(fresh_db, body, report_date)

    return await _do_penetrate(db, body, report_date)


async def _auto_fetch_holdings(fund_codes: list[str]):
    """尝试从天天基金自动采集持仓数据，返回 report_date 或 None。"""
    from datetime import date, timedelta
    from app.data.providers.eastmoney import EastmoneyProvider
    from app.data.repositories.holding_repo import HoldingRepo
    from app.data.session import get_sessionmaker

    provider = EastmoneyProvider()
    repo = HoldingRepo()
    factory = get_sessionmaker()

    # 确定最新季度
    today = date.today()
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

    report_date = None
    for code in fund_codes:
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
                report_date = snapshot.report_date
        except Exception:
            pass

    return report_date


async def _do_penetrate(
    db: AsyncSession,
    body: PenetrateRequest,
    report_date,
) -> PenetrateResponse:
    """执行持仓穿透分析的核心逻辑。"""
    from app.services.holdings_analysis import analyze_portfolio_holdings

    # 加载各基金持仓
    fund_holdings: dict[str, list[dict[str, Any]]] = {}
    for code in body.fund_codes:
        stmt = select(FundHolding).where(
            and_(
                FundHolding.fund_code == code,
                FundHolding.report_date == report_date,
            )
        ).order_by(desc(FundHolding.weight))
        result = await db.execute(stmt)
        rows = result.scalars().all()

        fund_holdings[code] = [
            {
                "stock_code": row.stock_code,
                "stock_name": row.stock_name,
                "weight": float(row.weight) if row.weight else 0,
                "industry": row.industry,
            }
            for row in rows
        ]

    if not any(fund_holdings.values()):
        raise HTTPException(
            status_code=404,
            detail=f"基金 {body.fund_codes} 在 {report_date} 无持仓数据",
        )

    # 分析
    analysis = analyze_portfolio_holdings(fund_holdings, body.fund_weights)

    return PenetrateResponse(
        stock_exposures=[
            StockExposureItem(
                stock_code=s.stock_code,
                stock_name=s.stock_name,
                weight=round(s.weight, 6),
                funds=s.funds,
                industry=s.industry,
            )
            for s in analysis.stock_exposures[:30]
        ],
        industry_distribution=[
            IndustryItem(
                industry=ind.industry,
                weight=round(ind.weight, 6),
                stock_count=ind.stock_count,
            )
            for ind in analysis.industry_distribution
        ],
        top5_concentration=round(analysis.top5_concentration, 4),
        top10_concentration=round(analysis.top10_concentration, 4),
        hhi=round(analysis.hhi, 6),
        total_stocks=analysis.total_stocks,
    )


@router.post(
    "/similarity",
    response_model=SimilarityResponse,
    summary="持仓相似度",
    description="计算基金组合中各基金之间的持仓重叠度。",
)
async def compute_similarity(
    body: SimilarityRequest,
    db: AsyncSession = Depends(get_session),
) -> SimilarityResponse:
    """Compute pairwise holdings similarity for a set of funds."""
    from app.services.holdings_analysis import compute_portfolio_similarity_matrix

    # 确定报告日期
    report_date = body.report_date
    if report_date is None:
        result = await db.execute(
            select(func.max(FundHolding.report_date)).where(
                FundHolding.fund_code.in_(body.fund_codes)
            )
        )
        report_date = result.scalar_one_or_none()
        if report_date is None:
            raise HTTPException(status_code=404, detail="未找到持仓数据")

    # 加载持仓
    all_holdings: dict[str, list[dict[str, Any]]] = {}
    for code in body.fund_codes:
        stmt = select(FundHolding).where(
            and_(
                FundHolding.fund_code == code,
                FundHolding.report_date == report_date,
            )
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()
        all_holdings[code] = [
            {"stock_code": row.stock_code, "weight": float(row.weight) if row.weight else 0}
            for row in rows
        ]

    # 计算相似度矩阵
    pairs = compute_portfolio_similarity_matrix(all_holdings)

    if not pairs:
        return SimilarityResponse(pairs=[], avg_similarity=0, max_similarity=0)

    avg_sim = sum(p.cosine_similarity for p in pairs) / len(pairs)
    max_sim = max(p.cosine_similarity for p in pairs)

    warning = None
    if max_sim > 0.7:
        high_pairs = [p for p in pairs if p.cosine_similarity > 0.7]
        warning = (
            f"存在 {len(high_pairs)} 对基金持仓高度相似（>70%），"
            f"可能存在过度集中风险"
        )

    return SimilarityResponse(
        pairs=[
            SimilarityItem(
                fund_a=p.fund_a,
                fund_b=p.fund_b,
                cosine_similarity=round(p.cosine_similarity, 4),
                overlap_count=p.overlap_count,
                overlap_stocks=p.overlap_stocks[:10],
            )
            for p in pairs
        ],
        avg_similarity=round(avg_sim, 4),
        max_similarity=round(max_sim, 4),
        warning=warning,
    )


@router.get(
    "/by-stock",
    response_model=StockFundResponse,
    summary="股票选基",
    description="给定股票代码，找出重仓该股票的基金。",
)
async def find_funds_by_stock(
    stock_code: str = Query(..., description="股票代码"),
    min_weight: float = Query(default=0.01, description="最小持仓权重"),
    limit: int = Query(default=20, ge=1, le=100, description="返回数量"),
    db: AsyncSession = Depends(get_session),
) -> StockFundResponse:
    """Find funds that hold a specific stock."""
    from app.data.models.funds import Fund

    # 查询最新一期持有该股票的基金
    # 先找最新报告日期
    latest_date_result = await db.execute(
        select(func.max(FundHolding.report_date)).where(
            FundHolding.stock_code == stock_code
        )
    )
    latest_date = latest_date_result.scalar_one_or_none()

    if latest_date is None:
        return StockFundResponse(
            stock_code=stock_code, stock_name=None, funds=[], total=0
        )

    # 查询持有该股票的基金
    stmt = (
        select(FundHolding, Fund.name.label("fund_name"))
        .outerjoin(Fund, FundHolding.fund_code == Fund.code)
        .where(
            and_(
                FundHolding.stock_code == stock_code,
                FundHolding.report_date == latest_date,
                FundHolding.weight >= min_weight,
            )
        )
        .order_by(desc(FundHolding.weight))
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.all()

    # 获取股票名称
    stock_name = None
    if rows:
        first_holding = rows[0].FundHolding
        stock_name = first_holding.stock_name

    # 总数
    count_result = await db.execute(
        select(func.count()).select_from(FundHolding).where(
            and_(
                FundHolding.stock_code == stock_code,
                FundHolding.report_date == latest_date,
                FundHolding.weight >= min_weight,
            )
        )
    )
    total = count_result.scalar_one()

    return StockFundResponse(
        stock_code=stock_code,
        stock_name=stock_name,
        funds=[
            StockFundItem(
                fund_code=row.FundHolding.fund_code,
                fund_name=row.fund_name,
                weight=float(row.FundHolding.weight) if row.FundHolding.weight else 0,
                report_date=latest_date.isoformat(),
            )
            for row in rows
        ],
        total=total,
    )
