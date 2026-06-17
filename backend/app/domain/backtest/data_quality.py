"""回测前数据质量检查模块。

在回测执行前对基金池的 NAV 数据进行质量评估，生成数据质量报告。
检查项包括：
- 数据覆盖率（回测期间有多少交易日有数据）
- 净值跳变检测（单日涨跌幅异常）
- 连续缺失检测（连续 N 天无数据）
- 基金存续状态检查

需求: 优化计划 5.2
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

# 净值跳变阈值（单日涨跌幅超过此值视为异常）
DEFAULT_SPIKE_THRESHOLD = Decimal("0.15")

# 基金类型净值跳变阈值。使用字符串而非枚举，避免回测领域层依赖数据 schema。
FUND_TYPE_SPIKE_THRESHOLDS: dict[str, Decimal] = {
    "stock": Decimal("0.15"),
    "index": Decimal("0.15"),
    "mixed": Decimal("0.15"),
    "qdii": Decimal("0.20"),
    "fof": Decimal("0.10"),
    "bond": Decimal("0.05"),
    "money": Decimal("0.01"),
}


# ---------------------------------------------------------------------------
# 数据质量报告
# ---------------------------------------------------------------------------


@dataclass
class FundDataQuality:
    """单只基金的数据质量评估。

    Attributes:
        fund_code: 基金代码
        coverage_ratio: 数据覆盖率（有数据的交易日 / 总交易日）
        total_trading_days: 回测期间总交易日数
        available_days: 有 NAV 数据的天数
        max_gap_days: 最大连续缺失天数
        spike_count: 净值跳变次数（单日涨跌幅 > 阈值）
        spike_dates: 跳变日期列表
        first_data_date: 最早有数据的日期
        last_data_date: 最晚有数据的日期
        status: 质量状态 (good/warning/poor)
    """

    fund_code: str
    coverage_ratio: float
    total_trading_days: int
    available_days: int
    max_gap_days: int
    spike_count: int
    spike_threshold: Decimal = DEFAULT_SPIKE_THRESHOLD
    spike_dates: list[date] = field(default_factory=list)
    first_data_date: date | None = None
    last_data_date: date | None = None
    status: str = "good"

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "fund_code": self.fund_code,
            "coverage_ratio": round(self.coverage_ratio, 4),
            "total_trading_days": self.total_trading_days,
            "available_days": self.available_days,
            "max_gap_days": self.max_gap_days,
            "spike_count": self.spike_count,
            "spike_threshold": str(self.spike_threshold),
            "spike_dates": [d.isoformat() for d in self.spike_dates[:10]],
            "first_data_date": self.first_data_date.isoformat() if self.first_data_date else None,
            "last_data_date": self.last_data_date.isoformat() if self.last_data_date else None,
            "status": self.status,
        }


@dataclass
class DataQualityReport:
    """回测数据质量报告。

    Attributes:
        fund_reports: 各基金的质量评估
        overall_status: 整体状态 (good/warning/poor)
        warnings: 警告信息列表
        can_proceed: 是否可以继续回测
    """

    fund_reports: list[FundDataQuality] = field(default_factory=list)
    overall_status: str = "good"
    warnings: list[str] = field(default_factory=list)
    can_proceed: bool = True

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "overall_status": self.overall_status,
            "can_proceed": self.can_proceed,
            "warnings": self.warnings,
            "funds": [f.to_dict() for f in self.fund_reports],
        }


# ---------------------------------------------------------------------------
# 质量检查函数
# ---------------------------------------------------------------------------

# 数据覆盖率阈值
COVERAGE_WARNING_THRESHOLD = 0.8   # 低于 80% 警告
COVERAGE_POOR_THRESHOLD = 0.5      # 低于 50% 标记为 poor

# 最大连续缺失天数阈值
MAX_GAP_WARNING = 10   # 连续缺失 10 天警告
MAX_GAP_POOR = 30      # 连续缺失 30 天标记为 poor


def check_fund_data_quality(
    fund_code: str,
    nav_data: dict[date, Decimal],
    trading_days_list: list[date],
    spike_threshold: Decimal = DEFAULT_SPIKE_THRESHOLD,
) -> FundDataQuality:
    """检查单只基金的数据质量。

    Args:
        fund_code: 基金代码
        nav_data: 该基金的 NAV 数据 {date: nav}
        trading_days_list: 回测期间的交易日列表
        spike_threshold: 净值跳变阈值

    Returns:
        FundDataQuality 评估结果
    """
    total_days = len(trading_days_list)
    if total_days == 0:
        return FundDataQuality(
            fund_code=fund_code,
            coverage_ratio=0.0,
            total_trading_days=0,
            available_days=0,
            max_gap_days=0,
            spike_count=0,
            spike_threshold=spike_threshold,
            status="poor",
        )

    # 计算覆盖率
    available_dates = sorted(d for d in trading_days_list if d in nav_data)
    available_days = len(available_dates)
    coverage_ratio = available_days / total_days

    # 计算最大连续缺失
    max_gap = 0
    current_gap = 0
    for d in trading_days_list:
        if d not in nav_data:
            current_gap += 1
            max_gap = max(max_gap, current_gap)
        else:
            current_gap = 0

    # 检测净值跳变
    spike_dates: list[date] = []
    sorted_dates = sorted(nav_data.keys())
    for i in range(1, len(sorted_dates)):
        prev_nav = nav_data[sorted_dates[i - 1]]
        curr_nav = nav_data[sorted_dates[i]]
        if prev_nav and prev_nav > 0:
            daily_change = abs((curr_nav - prev_nav) / prev_nav)
            if daily_change > spike_threshold:
                spike_dates.append(sorted_dates[i])

    # 确定状态
    status = "good"
    if coverage_ratio < COVERAGE_POOR_THRESHOLD or max_gap > MAX_GAP_POOR:
        status = "poor"
    elif coverage_ratio < COVERAGE_WARNING_THRESHOLD or max_gap > MAX_GAP_WARNING:
        status = "warning"
    elif spike_dates:
        status = "warning"

    first_date = available_dates[0] if available_dates else None
    last_date = available_dates[-1] if available_dates else None

    return FundDataQuality(
        fund_code=fund_code,
        coverage_ratio=coverage_ratio,
        total_trading_days=total_days,
        available_days=available_days,
        max_gap_days=max_gap,
        spike_count=len(spike_dates),
        spike_threshold=spike_threshold,
        spike_dates=spike_dates,
        first_data_date=first_date,
        last_data_date=last_date,
        status=status,
    )


def _spike_threshold_for_fund_type(
    fund_type: str | None,
    default: Decimal = DEFAULT_SPIKE_THRESHOLD,
) -> Decimal:
    """Return the NAV spike threshold for a fund type string.

    The backtest layer stores fund type as plain strings from the DB, so this
    helper intentionally avoids importing data-layer enums.
    """
    if not fund_type:
        return default
    normalized = str(fund_type).strip().lower()
    return FUND_TYPE_SPIKE_THRESHOLDS.get(normalized, default)


def check_backtest_data_quality(
    nav_data: dict[str, dict[date, Decimal]],
    trading_days_list: list[date],
    spike_threshold: Decimal = DEFAULT_SPIKE_THRESHOLD,
    fund_types: dict[str, str | None] | None = None,
) -> DataQualityReport:
    """检查回测数据整体质量。

    Args:
        nav_data: 所有基金的 NAV 数据 {fund_code: {date: nav}}
        trading_days_list: 回测期间的交易日列表
        spike_threshold: 未提供基金类型或类型未知时使用的默认净值跳变阈值
        fund_types: 基金类型映射 {fund_code: fund_type}，用于按类型选择跳变阈值

    Returns:
        DataQualityReport 整体报告
    """
    report = DataQualityReport()

    for fund_code, fund_nav in nav_data.items():
        threshold = _spike_threshold_for_fund_type(
            fund_types.get(fund_code) if fund_types else None,
            default=spike_threshold,
        )
        quality = check_fund_data_quality(
            fund_code, fund_nav, trading_days_list, threshold
        )
        report.fund_reports.append(quality)

    # 汇总整体状态
    warnings: list[str] = []
    has_poor = False
    has_warning = False

    for fq in report.fund_reports:
        if fq.status == "poor":
            has_poor = True
            warnings.append(
                f"{fq.fund_code}: 数据质量差（覆盖率 {fq.coverage_ratio:.1%}，"
                f"最大缺失 {fq.max_gap_days} 天）"
            )
        elif fq.status == "warning":
            has_warning = True
            warning_parts = []
            if fq.coverage_ratio < COVERAGE_WARNING_THRESHOLD:
                warning_parts.append(f"覆盖率 {fq.coverage_ratio:.1%}")
            if fq.max_gap_days > MAX_GAP_WARNING:
                warning_parts.append(f"最大缺失 {fq.max_gap_days} 天")
            if fq.spike_count > 0:
                warning_parts.append(
                    f"净值跳变 {fq.spike_count} 次（阈值 ±{fq.spike_threshold}）"
                )
            warnings.append(f"{fq.fund_code}: {'，'.join(warning_parts)}")

    if has_poor:
        report.overall_status = "poor"
        # 如果所有基金都是 poor，不允许继续
        all_poor = all(fq.status == "poor" for fq in report.fund_reports)
        if all_poor:
            report.can_proceed = False
            warnings.insert(0, "所有基金数据质量均为差，无法进行有效回测")
    elif has_warning:
        report.overall_status = "warning"

    report.warnings = warnings
    return report
