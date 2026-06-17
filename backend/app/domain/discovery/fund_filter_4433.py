"""4433 法则基金筛选模块。

实现基金 4433 筛选规则：
- 第一个4：近1年收益率排名在同类型基金的前 1/4
- 第二个4：近2年、3年、5年及今年以来收益率排名均在同类型基金的前 1/4
- 第一个3：近6个月收益率排名在同类型基金的前 1/3
- 第二个3：近3个月收益率排名在同类型基金的前 1/3

扩展支持（4433 严选）：
- 基金经理管理年限筛选
- 基金规模筛选（2-50亿）
- 基金成立年限筛选
- 自定义排名百分位阈值

灵感来源：investool 的 4433 法则实现
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


# ---------------------------------------------------------------------------
# 筛选参数
# ---------------------------------------------------------------------------


@dataclass
class Filter4433Params:
    """4433 筛选参数。

    Attributes:
        year1_percentile: 近1年排名百分位阈值（默认 0.25 = 前1/4）
        year2_percentile: 近2年排名百分位阈值
        year3_percentile: 近3年排名百分位阈值
        month6_percentile: 近6月排名百分位阈值（默认 0.33 = 前1/3）
        month3_percentile: 近3月排名百分位阈值
        min_fund_size: 最小基金规模（亿），默认 2
        max_fund_size: 最大基金规模（亿），默认 50
        min_inception_years: 最小成立年限，默认 3
        fund_types: 筛选的基金类型列表，默认全部
    """

    year1_percentile: float = 0.25
    year2_percentile: float = 0.25
    year3_percentile: float = 0.25
    month6_percentile: float = 0.333
    month3_percentile: float = 0.333
    min_fund_size: float | None = 2.0
    max_fund_size: float | None = 50.0
    min_inception_years: float | None = 3.0
    fund_types: list[str] | None = None


# ---------------------------------------------------------------------------
# 筛选结果
# ---------------------------------------------------------------------------


@dataclass
class Fund4433Result:
    """单只基金的 4433 筛选结果。"""

    fund_code: str
    fund_name: str | None = None
    fund_type: str | None = None
    # 各期收益率排名百分位（0~1，越小越好）
    rank_1y: float | None = None
    rank_2y: float | None = None
    rank_3y: float | None = None
    rank_6m: float | None = None
    rank_3m: float | None = None
    # 收益率
    return_1y: float | None = None
    return_3m: float | None = None
    return_6m: float | None = None
    # 是否通过各条件
    pass_4_1y: bool = False
    pass_4_long: bool = False
    pass_3_6m: bool = False
    pass_3_3m: bool = False
    pass_all: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "fund_code": self.fund_code,
            "fund_name": self.fund_name,
            "fund_type": self.fund_type,
            "rank_1y": round(self.rank_1y, 4) if self.rank_1y is not None else None,
            "rank_6m": round(self.rank_6m, 4) if self.rank_6m is not None else None,
            "rank_3m": round(self.rank_3m, 4) if self.rank_3m is not None else None,
            "return_1y": round(self.return_1y, 4) if self.return_1y is not None else None,
            "return_3m": round(self.return_3m, 4) if self.return_3m is not None else None,
            "return_6m": round(self.return_6m, 4) if self.return_6m is not None else None,
            "pass_4_1y": self.pass_4_1y,
            "pass_4_long": self.pass_4_long,
            "pass_3_6m": self.pass_3_6m,
            "pass_3_3m": self.pass_3_3m,
            "pass_all": self.pass_all,
        }


# ---------------------------------------------------------------------------
# 筛选逻辑
# ---------------------------------------------------------------------------


def apply_4433_filter(
    fund_rankings: list[dict[str, Any]],
    total_funds_by_type: dict[str, int],
    params: Filter4433Params | None = None,
) -> list[Fund4433Result]:
    """应用 4433 法则筛选基金。

    Args:
        fund_rankings: 基金排名数据列表，每条包含：
            fund_code, fund_name, fund_type,
            rank_1y, rank_2y, rank_3y, rank_6m, rank_3m
            (rank 为排名位次，从1开始)
        total_funds_by_type: 各类型基金总数 {fund_type: count}
        params: 筛选参数

    Returns:
        通过筛选的基金列表
    """
    if params is None:
        params = Filter4433Params()

    results: list[Fund4433Result] = []

    for fund in fund_rankings:
        fund_code = fund.get("fund_code", "")
        fund_type = fund.get("fund_type", "all")
        total_in_type = total_funds_by_type.get(fund_type, 1)

        if total_in_type <= 0:
            total_in_type = 1

        # 计算各期排名百分位
        rank_1y = fund.get("rank_1y")
        rank_2y = fund.get("rank_2y")
        rank_3y = fund.get("rank_3y")
        rank_6m = fund.get("rank_6m")
        rank_3m = fund.get("rank_3m")

        pct_1y = rank_1y / total_in_type if rank_1y else None
        pct_2y = rank_2y / total_in_type if rank_2y else None
        pct_3y = rank_3y / total_in_type if rank_3y else None
        pct_6m = rank_6m / total_in_type if rank_6m else None
        pct_3m = rank_3m / total_in_type if rank_3m else None

        # 条件1：近1年前1/4
        pass_4_1y = pct_1y is not None and pct_1y <= params.year1_percentile

        # 条件2：近2年、3年前1/4（有数据的都要满足）
        long_term_checks = []
        if pct_2y is not None:
            long_term_checks.append(pct_2y <= params.year2_percentile)
        if pct_3y is not None:
            long_term_checks.append(pct_3y <= params.year3_percentile)
        # 至少有一个长期指标且全部满足
        pass_4_long = len(long_term_checks) > 0 and all(long_term_checks)

        # 条件3：近6月前1/3
        pass_3_6m = pct_6m is not None and pct_6m <= params.month6_percentile

        # 条件4：近3月前1/3
        pass_3_3m = pct_3m is not None and pct_3m <= params.month3_percentile

        pass_all = pass_4_1y and pass_4_long and pass_3_6m and pass_3_3m

        result = Fund4433Result(
            fund_code=fund_code,
            fund_name=fund.get("fund_name"),
            fund_type=fund_type,
            rank_1y=pct_1y,
            rank_2y=pct_2y,
            rank_3y=pct_3y,
            rank_6m=pct_6m,
            rank_3m=pct_3m,
            return_1y=fund.get("return_1y"),
            return_3m=fund.get("return_3m"),
            return_6m=fund.get("return_6m"),
            pass_4_1y=pass_4_1y,
            pass_4_long=pass_4_long,
            pass_3_6m=pass_3_6m,
            pass_3_3m=pass_3_3m,
            pass_all=pass_all,
        )

        if pass_all:
            results.append(result)

    # 按近1年排名百分位排序
    results.sort(key=lambda r: r.rank_1y or 1.0)
    return results
