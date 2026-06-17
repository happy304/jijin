"""持仓穿透分析服务。

提供基金组合的底层持仓分析功能：
1. 持仓穿透 — 将多只基金的持仓合并，得到底层等效股票暴露
2. 持仓相似度 — 计算两只基金之间的持仓重叠度（余弦相似度）
3. 行业分布 — 汇总组合的行业集中度
4. 股票选基 — 给定股票代码，找出重仓该股票的基金

灵感来源：xalpha 的 get_stock_holdings() 功能
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class StockExposure:
    """单只股票的等效暴露。"""

    stock_code: str
    stock_name: str | None = None
    weight: float = 0.0  # 在组合中的等效权重
    funds: list[str] = field(default_factory=list)  # 持有该股票的基金列表
    industry: str | None = None


@dataclass
class IndustryExposure:
    """行业暴露。"""

    industry: str
    weight: float = 0.0
    stock_count: int = 0


@dataclass
class HoldingsAnalysisResult:
    """持仓穿透分析结果。"""

    # 底层股票暴露（按权重降序）
    stock_exposures: list[StockExposure] = field(default_factory=list)
    # 行业分布
    industry_distribution: list[IndustryExposure] = field(default_factory=list)
    # 集中度指标
    top5_concentration: float = 0.0  # 前5大持仓占比
    top10_concentration: float = 0.0  # 前10大持仓占比
    hhi: float = 0.0  # 赫芬达尔指数
    total_stocks: int = 0  # 底层股票总数

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "stock_exposures": [
                {
                    "stock_code": s.stock_code,
                    "stock_name": s.stock_name,
                    "weight": round(s.weight, 6),
                    "funds": s.funds,
                    "industry": s.industry,
                }
                for s in self.stock_exposures[:30]  # 只返回前30
            ],
            "industry_distribution": [
                {
                    "industry": ind.industry,
                    "weight": round(ind.weight, 6),
                    "stock_count": ind.stock_count,
                }
                for ind in self.industry_distribution
            ],
            "top5_concentration": round(self.top5_concentration, 4),
            "top10_concentration": round(self.top10_concentration, 4),
            "hhi": round(self.hhi, 6),
            "total_stocks": self.total_stocks,
        }


@dataclass
class SimilarityResult:
    """持仓相似度结果。"""

    fund_a: str
    fund_b: str
    cosine_similarity: float  # 余弦相似度 (0~1)
    overlap_count: int  # 重叠股票数
    overlap_stocks: list[str] = field(default_factory=list)  # 重叠股票代码

    def to_dict(self) -> dict[str, Any]:
        """序列化。"""
        return {
            "fund_a": self.fund_a,
            "fund_b": self.fund_b,
            "cosine_similarity": round(self.cosine_similarity, 4),
            "overlap_count": self.overlap_count,
            "overlap_stocks": self.overlap_stocks[:20],
        }


# ---------------------------------------------------------------------------
# 持仓穿透
# ---------------------------------------------------------------------------


def analyze_portfolio_holdings(
    fund_holdings: dict[str, list[dict[str, Any]]],
    fund_weights: dict[str, float] | None = None,
) -> HoldingsAnalysisResult:
    """分析基金组合的底层持仓。

    将多只基金的持仓按权重合并，得到组合级别的等效股票暴露。

    Args:
        fund_holdings: 各基金的持仓数据
            {fund_code: [{stock_code, stock_name, weight, industry}, ...]}
        fund_weights: 各基金在组合中的权重，默认等权
            {fund_code: weight}

    Returns:
        HoldingsAnalysisResult 分析结果
    """
    if not fund_holdings:
        return HoldingsAnalysisResult()

    # 默认等权
    n_funds = len(fund_holdings)
    if fund_weights is None:
        fund_weights = {code: 1.0 / n_funds for code in fund_holdings}

    # 合并底层持仓
    stock_map: dict[str, StockExposure] = {}

    for fund_code, holdings in fund_holdings.items():
        fund_w = fund_weights.get(fund_code, 1.0 / n_funds)

        for pos in holdings:
            stock_code = pos.get("stock_code", "")
            if not stock_code:
                continue

            position_weight = float(pos.get("weight", 0) or 0)
            # 等效权重 = 基金权重 × 股票在基金中的权重
            effective_weight = fund_w * position_weight

            if stock_code in stock_map:
                stock_map[stock_code].weight += effective_weight
                if fund_code not in stock_map[stock_code].funds:
                    stock_map[stock_code].funds.append(fund_code)
            else:
                stock_map[stock_code] = StockExposure(
                    stock_code=stock_code,
                    stock_name=pos.get("stock_name"),
                    weight=effective_weight,
                    funds=[fund_code],
                    industry=pos.get("industry"),
                )

    # 按权重降序排列
    sorted_stocks = sorted(stock_map.values(), key=lambda s: s.weight, reverse=True)

    # 计算集中度指标
    total_weight = sum(s.weight for s in sorted_stocks)
    if total_weight > 0:
        # 归一化
        for s in sorted_stocks:
            s.weight = s.weight / total_weight

    top5 = sum(s.weight for s in sorted_stocks[:5])
    top10 = sum(s.weight for s in sorted_stocks[:10])
    hhi = sum(s.weight ** 2 for s in sorted_stocks)

    # 行业分布
    industry_map: dict[str, IndustryExposure] = {}
    for s in sorted_stocks:
        ind = s.industry or "未知"
        if ind in industry_map:
            industry_map[ind].weight += s.weight
            industry_map[ind].stock_count += 1
        else:
            industry_map[ind] = IndustryExposure(
                industry=ind,
                weight=s.weight,
                stock_count=1,
            )

    sorted_industries = sorted(
        industry_map.values(), key=lambda i: i.weight, reverse=True
    )

    return HoldingsAnalysisResult(
        stock_exposures=sorted_stocks,
        industry_distribution=sorted_industries,
        top5_concentration=top5,
        top10_concentration=top10,
        hhi=hhi,
        total_stocks=len(sorted_stocks),
    )


# ---------------------------------------------------------------------------
# 持仓相似度
# ---------------------------------------------------------------------------


def compute_holdings_similarity(
    holdings_a: list[dict[str, Any]],
    holdings_b: list[dict[str, Any]],
    fund_a: str = "A",
    fund_b: str = "B",
) -> SimilarityResult:
    """计算两只基金的持仓相似度（余弦相似度）。

    Args:
        holdings_a: 基金A的持仓列表 [{stock_code, weight}, ...]
        holdings_b: 基金B的持仓列表
        fund_a: 基金A代码
        fund_b: 基金B代码

    Returns:
        SimilarityResult
    """
    # 构建权重向量
    vec_a: dict[str, float] = {}
    for pos in holdings_a:
        code = pos.get("stock_code", "")
        if code:
            vec_a[code] = float(pos.get("weight", 0) or 0)

    vec_b: dict[str, float] = {}
    for pos in holdings_b:
        code = pos.get("stock_code", "")
        if code:
            vec_b[code] = float(pos.get("weight", 0) or 0)

    # 所有股票的并集
    all_stocks = set(vec_a.keys()) | set(vec_b.keys())
    overlap_stocks = sorted(set(vec_a.keys()) & set(vec_b.keys()))

    if not all_stocks:
        return SimilarityResult(
            fund_a=fund_a, fund_b=fund_b,
            cosine_similarity=0.0, overlap_count=0,
        )

    # 余弦相似度
    dot_product = sum(vec_a.get(s, 0) * vec_b.get(s, 0) for s in all_stocks)
    norm_a = math.sqrt(sum(v ** 2 for v in vec_a.values()))
    norm_b = math.sqrt(sum(v ** 2 for v in vec_b.values()))

    if norm_a == 0 or norm_b == 0:
        cosine_sim = 0.0
    else:
        cosine_sim = dot_product / (norm_a * norm_b)

    return SimilarityResult(
        fund_a=fund_a,
        fund_b=fund_b,
        cosine_similarity=cosine_sim,
        overlap_count=len(overlap_stocks),
        overlap_stocks=overlap_stocks,
    )


def compute_portfolio_similarity_matrix(
    all_holdings: dict[str, list[dict[str, Any]]],
) -> list[SimilarityResult]:
    """计算基金组合中所有基金两两之间的持仓相似度。

    Args:
        all_holdings: {fund_code: [{stock_code, weight}, ...]}

    Returns:
        所有基金对的相似度列表
    """
    codes = sorted(all_holdings.keys())
    results: list[SimilarityResult] = []

    for i in range(len(codes)):
        for j in range(i + 1, len(codes)):
            result = compute_holdings_similarity(
                all_holdings[codes[i]],
                all_holdings[codes[j]],
                fund_a=codes[i],
                fund_b=codes[j],
            )
            results.append(result)

    # 按相似度降序
    results.sort(key=lambda r: r.cosine_similarity, reverse=True)
    return results
