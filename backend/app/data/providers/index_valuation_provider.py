"""指数估值数据采集 Provider。

数据来源：中证指数公司官网 + AkShare 封装
提供主流宽基指数和行业指数的 PE/PB/股息率历史数据。

支持的指数：
- 沪深300 (000300)
- 中证500 (000905)
- 中证1000 (000852)
- 创业板指 (399006)
- 上证50 (000016)
- 中证红利 (000922)
- 等等

采集频率：每日收盘后更新
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ValuationRecord:
    """单条估值数据记录。"""

    index_code: str
    index_name: str
    trade_date: date
    pe_ttm: float | None = None
    pb: float | None = None
    dividend_yield: float | None = None
    roe: float | None = None


# 常用指数代码映射
INDEX_CODE_MAP: dict[str, str] = {
    "000300": "沪深300",
    "000905": "中证500",
    "000852": "中证1000",
    "399006": "创业板指",
    "000016": "上证50",
    "000922": "中证红利",
    "000015": "红利指数",
    "399673": "创业板50",
    "000688": "科创50",
    "399330": "深证100",
    "000010": "上证180",
    "000903": "中证100",
}

# 基金代码 → 跟踪指数代码的映射（常见指数基金）
# 实际使用时应从数据库 funds.benchmark 字段解析
FUND_TO_INDEX: dict[str, str] = {
    "510300": "000300",  # 华泰柏瑞沪深300ETF
    "510500": "000905",  # 南方中证500ETF
    "159919": "000300",  # 嘉实沪深300ETF
    "510050": "000016",  # 华夏上证50ETF
    "159915": "399006",  # 易方达创业板ETF
    "512100": "000922",  # 南方中证红利ETF
}


class IndexValuationProvider:
    """指数估值数据采集器。

    优先使用 AkShare 获取数据（免费、无需注册）。
    AkShare 封装了中证指数公司和国证指数的公开数据。
    """

    name = "index_valuation"

    async def fetch_valuation_history(
        self,
        index_code: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[ValuationRecord]:
        """获取指数历史估值数据。

        Args:
            index_code: 指数代码（如 000300）
            start_date: 起始日期
            end_date: 结束日期

        Returns:
            估值记录列表
        """
        if end_date is None:
            end_date = date.today()
        if start_date is None:
            start_date = end_date - timedelta(days=3650)  # 默认10年

        index_name = INDEX_CODE_MAP.get(index_code, index_code)

        try:
            return await self._fetch_via_akshare(
                index_code, index_name, start_date, end_date
            )
        except Exception as e:
            logger.warning(
                "index_valuation.akshare_failed",
                index_code=index_code,
                error=str(e),
            )
            # 降级：尝试其他数据源
            return []

    async def _fetch_via_akshare(
        self,
        index_code: str,
        index_name: str,
        start_date: date,
        end_date: date,
    ) -> list[ValuationRecord]:
        """通过 AkShare 获取指数估值数据。

        优先使用 ak.stock_index_pe_lg（乐咕数据，约20年历史），
        回退到 ak.stock_zh_index_value_csindex（中证指数公司，仅20天）。
        """
        import asyncio

        def _sync_fetch() -> list[ValuationRecord]:
            try:
                import akshare as ak
            except ImportError:
                logger.error("akshare not installed, cannot fetch valuation data")
                return []

            records: list[ValuationRecord] = []

            # 优先使用 stock_index_pe_lg（长历史，约20年）
            try:
                df = ak.stock_index_pe_lg(symbol=index_name)

                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        try:
                            trade_d = row.get("日期")
                            if trade_d is None:
                                continue
                            if hasattr(trade_d, "date"):
                                trade_d = trade_d.date()
                            elif isinstance(trade_d, str):
                                from datetime import datetime as dt
                                trade_d = dt.strptime(str(trade_d), "%Y-%m-%d").date()

                            if trade_d < start_date or trade_d > end_date:
                                continue

                            # 滚动市盈率 = PE-TTM
                            pe_ttm = row.get("滚动市盈率")

                            records.append(ValuationRecord(
                                index_code=index_code,
                                index_name=index_name,
                                trade_date=trade_d,
                                pe_ttm=float(pe_ttm) if pe_ttm is not None else None,
                            ))
                        except (ValueError, TypeError):
                            continue

                    if records:
                        return records

            except Exception as e:
                logger.warning(f"akshare stock_index_pe_lg failed for {index_name}: {e}")

            # 回退到 stock_zh_index_value_csindex（仅最近20天）
            try:
                df = ak.stock_zh_index_value_csindex(symbol=index_code)

                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        try:
                            trade_d = row.get("日期")
                            if trade_d is None:
                                continue
                            if hasattr(trade_d, "date"):
                                trade_d = trade_d.date()
                            elif isinstance(trade_d, str):
                                from datetime import datetime as dt
                                trade_d = dt.strptime(str(trade_d), "%Y-%m-%d").date()

                            if trade_d < start_date or trade_d > end_date:
                                continue

                            pe_val = row.get("市盈率1")
                            dividend_val = row.get("股息率1")

                            records.append(ValuationRecord(
                                index_code=index_code,
                                index_name=index_name,
                                trade_date=trade_d,
                                pe_ttm=float(pe_val) if pe_val is not None else None,
                                dividend_yield=float(dividend_val) / 100.0 if dividend_val is not None else None,
                            ))
                        except (ValueError, TypeError):
                            continue

            except Exception as e:
                logger.warning(f"akshare csindex fallback failed for {index_code}: {e}")

            return records

        # 在线程池中运行同步代码
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_fetch)

    async def fetch_current_valuation(
        self,
        index_code: str,
    ) -> ValuationRecord | None:
        """获取指数最新估值。"""
        records = await self.fetch_valuation_history(
            index_code,
            start_date=date.today() - timedelta(days=7),
            end_date=date.today(),
        )
        return records[-1] if records else None

    def get_tracked_index(self, fund_code: str) -> str | None:
        """根据基金代码查找跟踪的指数代码。"""
        return FUND_TO_INDEX.get(fund_code)


def compute_percentile(
    values: list[float],
    current: float,
) -> float:
    """计算当前值在历史序列中的百分位。"""
    if not values:
        return 0.5
    count_below = sum(1 for v in values if v < current)
    return count_below / max(len(values) - 1, 1)


__all__ = [
    "IndexValuationProvider",
    "ValuationRecord",
    "INDEX_CODE_MAP",
    "FUND_TO_INDEX",
    "compute_percentile",
]
