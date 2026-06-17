"""AkShare 数据 Provider 实现（备源）。

实现 FundDataProvider Protocol 的全部方法，作为天天基金的备用数据源。
AkShare 是一个开源的金融数据接口库，封装了多个数据源的 API。

设计要点：
- name = "akshare", priority = 2（备源）
- 封装 akshare 同步函数为异步接口
- 统一字段命名与异常处理
- 接入 RateLimiter 和 retry 装饰器
- 接入 SnapshotArchive 保存原始响应

AkShare 基金相关主要函数：
- fund_name_em(): 获取基金列表
- fund_open_fund_info_em(): 获取开放式基金基本信息
- fund_open_fund_daily_em(): 获取开放式基金历史净值
- fund_portfolio_hold_em(): 获取基金持仓
- fund_fh_em(): 获取基金分红
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import Any

from app.data.fetchers.rate_limiter import RateLimiter
from app.data.providers.base import (
    HealthStatus,
    ProviderError,
    ProviderNotFoundError,
    ProviderTimeoutError,
)
from app.data.providers.snapshot import SnapshotArchive
from app.data.schemas.funds import (
    Announcement,
    DividendRecord,
    FundMeta,
    FundStatus,
    FundType,
    HoldingPosition,
    HoldingSnapshot,
    NavRecord,
    NavStatus,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 尝试导入 akshare
# ---------------------------------------------------------------------------
try:
    import akshare as ak  # type: ignore[import-untyped]
    import pandas as pd

    _HAS_AKSHARE = True
except ImportError:  # pragma: no cover
    _HAS_AKSHARE = False
    ak = None  # type: ignore[assignment]
    pd = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 基金类型映射（AkShare 中文 → FundType）
_FUND_TYPE_MAP: dict[str, FundType] = {
    "股票型": FundType.STOCK,
    "股票指数": FundType.INDEX,
    "指数型": FundType.INDEX,
    "增强指数型": FundType.INDEX,
    "债券型": FundType.BOND,
    "混合型": FundType.MIXED,
    "货币型": FundType.MONEY,
    "货币市场型": FundType.MONEY,
    "QDII": FundType.QDII,
    "QDII型": FundType.QDII,
    "FOF": FundType.FOF,
    "FOF型": FundType.FOF,
}

# 线程池用于运行同步的 akshare 函数
_EXECUTOR = ThreadPoolExecutor(max_workers=4)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _safe_decimal(value: Any, default: Decimal | None = None) -> Decimal | None:
    """将任意值安全转换为 Decimal，失败返回 default。"""
    if value is None or value == "" or value == "--" or value == "---":
        return default
    if pd is not None and pd.isna(value):
        return default
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except InvalidOperation:
        return default


def _safe_date(value: Any, fmt: str = "%Y-%m-%d") -> date | None:
    """将字符串或 datetime 安全解析为 date，失败返回 None。"""
    if value is None:
        return None
    if pd is not None and pd.isna(value):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if not value or value in ("", "--", "暂无"):
        return None
    try:
        return datetime.strptime(value, fmt).date()
    except ValueError:
        # 尝试其他格式
        for alt_fmt in ("%Y%m%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(value, alt_fmt).date()
            except ValueError:
                continue
        return None


def _parse_fund_type(raw: str | None) -> FundType | None:
    """将 AkShare 的中文基金类型映射到 FundType 枚举。"""
    if not raw:
        return None
    raw = raw.strip()
    for key, ft in _FUND_TYPE_MAP.items():
        if key in raw:
            return ft
    return None


def _check_akshare_available() -> None:
    """检查 akshare 是否可用，不可用则抛出异常。"""
    if not _HAS_AKSHARE:
        raise ProviderError(
            "akshare 库未安装，请运行 pip install akshare",
            provider_name="akshare",
        )


# ---------------------------------------------------------------------------
# AkshareProvider
# ---------------------------------------------------------------------------


class AkshareProvider:
    """AkShare 数据 Provider（备源，priority=2）。

    实现 FundDataProvider Protocol 的全部方法。
    封装 akshare 的同步函数为异步接口。

    Args:
        rate_limiter: 令牌桶限流器，默认 1 req/s（akshare 底层有自己的限流）。
        snapshot_archive: 原始响应归档，默认写入 ./local_data/snapshots。
        timeout: 请求超时秒数。
    """

    name: str = "akshare"
    priority: int = 2

    def __init__(
        self,
        *,
        rate_limiter: RateLimiter | None = None,
        snapshot_archive: SnapshotArchive | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._rate_limiter = rate_limiter or RateLimiter(default_rate=1.0)
        self._rate_limiter.configure(self.name, rate=1.0)
        self._snapshot = snapshot_archive or SnapshotArchive()
        self._timeout = timeout

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """在线程池中运行同步函数。"""
        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(_EXECUTOR, partial(func, *args, **kwargs)),
            timeout=self._timeout,
        )

    async def _save_snapshot(
        self,
        fund_code: str,
        endpoint: str,
        ext: str,
        data: bytes | str,
    ) -> None:
        """异步保存原始响应快照（失败不影响主流程）。"""
        try:
            if isinstance(data, str):
                data = data.encode("utf-8")
            await self._snapshot.async_save_raw(
                provider=self.name,
                fund_code=fund_code,
                endpoint=endpoint,
                ext=ext,
                data=data,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("快照保存失败 fund=%s endpoint=%s: %s", fund_code, endpoint, exc)

    # ------------------------------------------------------------------
    # 1. 基础信息 fetch_fund_meta
    # ------------------------------------------------------------------

    async def fetch_fund_meta(self, code: str) -> FundMeta:
        """从 AkShare 获取基金基础信息。

        使用 fund_open_fund_info_em 接口获取基金详情。

        Args:
            code: 基金代码（如 "000001"）。

        Returns:
            FundMeta 实例。

        Raises:
            ProviderNotFoundError: 基金代码不存在。
            ProviderTimeoutError: 请求超时。
            ProviderError: 其他错误。
        """
        _check_akshare_available()
        await self._rate_limiter.acquire(self.name)

        try:
            # 获取基金基本信息
            df = await self._run_sync(ak.fund_open_fund_info_em, symbol=code, indicator="单位净值走势")
            
            # 尝试获取更详细的信息
            try:
                info_df = await self._run_sync(ak.fund_individual_basic_info_xq, symbol=code)
                info_dict = {}
                if info_df is not None and not info_df.empty:
                    for _, row in info_df.iterrows():
                        key = str(row.iloc[0]).strip() if len(row) > 0 else ""
                        val = str(row.iloc[1]).strip() if len(row) > 1 else ""
                        info_dict[key] = val
            except Exception:
                info_dict = {}

            # 保存快照
            snapshot_data = f"code={code}\ninfo={info_dict}"
            await self._save_snapshot(code, "fund_meta", "txt", snapshot_data)

            # 解析基金名称
            name = info_dict.get("基金全称", info_dict.get("基金简称", code))
            if not name or name == code:
                # 尝试从基金列表获取名称
                try:
                    fund_list = await self._run_sync(ak.fund_name_em)
                    if fund_list is not None and not fund_list.empty:
                        match = fund_list[fund_list["基金代码"] == code]
                        if not match.empty:
                            name = match.iloc[0].get("基金简称", code)
                except Exception:
                    pass

            # 解析基金类型
            fund_type_raw = info_dict.get("基金类型", "")
            fund_type = _parse_fund_type(fund_type_raw)

            # 解析成立日期
            inception_raw = info_dict.get("成立日期", info_dict.get("基金成立日", ""))
            inception_date = _safe_date(inception_raw)

            # 解析费率
            mgmt_fee_raw = info_dict.get("管理费率", "")
            cust_fee_raw = info_dict.get("托管费率", "")

            def _parse_rate(raw: str) -> Decimal | None:
                if not raw:
                    return None
                m = re.search(r"([\d.]+)%", raw)
                if m:
                    try:
                        return Decimal(m.group(1)) / Decimal("100")
                    except InvalidOperation:
                        pass
                return None

            return FundMeta(
                code=code,
                name=name or code,
                fund_type=fund_type,
                sub_type=fund_type_raw or None,
                inception_date=inception_date,
                management_fee=_parse_rate(mgmt_fee_raw),
                custodian_fee=_parse_rate(cust_fee_raw),
                status=FundStatus.ACTIVE,
                is_purchasable=True,
                purchase_limit=None,
                source=self.name,
                updated_at=datetime.now(tz=timezone.utc),
            )

        except asyncio.TimeoutError as exc:
            raise ProviderTimeoutError(
                f"请求超时: fund_meta for {code}",
                provider_name=self.name,
                fund_code=code,
            ) from exc
        except Exception as exc:
            if "不存在" in str(exc) or "not found" in str(exc).lower():
                raise ProviderNotFoundError(
                    f"基金代码不存在: {code}",
                    provider_name=self.name,
                    fund_code=code,
                ) from exc
            raise ProviderError(
                f"获取基金信息失败: {exc}",
                provider_name=self.name,
                fund_code=code,
            ) from exc

    # ------------------------------------------------------------------
    # 2. 历史净值 fetch_nav_history
    # ------------------------------------------------------------------

    async def fetch_nav_history(
        self,
        code: str,
        start: date,
        end: date,
    ) -> list[NavRecord]:
        """从 AkShare 获取历史净值。

        使用 fund_open_fund_info_em 接口获取净值数据。

        Args:
            code: 基金代码。
            start: 起始日期（含）。
            end: 结束日期（含）。

        Returns:
            按 trade_date 升序排列的 NavRecord 列表。
        """
        _check_akshare_available()
        await self._rate_limiter.acquire(self.name)

        try:
            # 获取单位净值走势
            df = await self._run_sync(
                ak.fund_open_fund_info_em,
                symbol=code,
                indicator="单位净值走势",
            )

            if df is None or df.empty:
                return []

            # 保存快照
            snapshot_data = df.to_csv(index=False)
            await self._save_snapshot(code, "nav_history", "csv", snapshot_data)

            records: list[NavRecord] = []

            for _, row in df.iterrows():
                # 解析日期
                trade_date = _safe_date(row.get("净值日期"))
                if trade_date is None:
                    continue

                # 过滤日期范围
                if trade_date < start or trade_date > end:
                    continue

                # 解析净值
                unit_nav = _safe_decimal(row.get("单位净值"))
                accum_nav = _safe_decimal(row.get("累计净值"))
                
                # 解析日涨跌幅
                daily_return_raw = row.get("日增长率")
                daily_return: Decimal | None = None
                if daily_return_raw is not None and not (pd.isna(daily_return_raw) if pd else False):
                    dr = _safe_decimal(daily_return_raw)
                    if dr is not None:
                        # AkShare 返回的是百分比数值，如 1.23 表示 1.23%
                        daily_return = dr / Decimal("100")

                records.append(
                    NavRecord(
                        fund_code=code,
                        trade_date=trade_date,
                        unit_nav=unit_nav,
                        accum_nav=accum_nav,
                        adj_nav=None,  # 由 adj_nav 服务计算
                        daily_return=daily_return,
                        status=NavStatus.NORMAL,
                        source=self.name,
                    )
                )

            # 按日期升序排列
            records.sort(key=lambda r: r.trade_date)
            return records

        except asyncio.TimeoutError as exc:
            raise ProviderTimeoutError(
                f"请求超时: nav_history for {code}",
                provider_name=self.name,
                fund_code=code,
            ) from exc
        except Exception as exc:
            if "不存在" in str(exc) or "not found" in str(exc).lower():
                raise ProviderNotFoundError(
                    f"基金代码不存在: {code}",
                    provider_name=self.name,
                    fund_code=code,
                ) from exc
            raise ProviderError(
                f"获取历史净值失败: {exc}",
                provider_name=self.name,
                fund_code=code,
            ) from exc

    # ------------------------------------------------------------------
    # 3. 持仓 fetch_holdings
    # ------------------------------------------------------------------

    async def fetch_holdings(self, code: str, quarter: str) -> HoldingSnapshot:
        """从 AkShare 获取季度持仓。

        使用 fund_portfolio_hold_em 接口获取持仓数据。

        Args:
            code: 基金代码。
            quarter: 季度标识，格式 "YYYY-QN"（如 "2024-Q1"）。

        Returns:
            HoldingSnapshot 实例，包含前 N 大持仓。

        Raises:
            ProviderNotFoundError: 基金代码或季度不存在。
            ProviderTimeoutError: 请求超时。
            ProviderError: 其他错误。
        """
        _check_akshare_available()
        await self._rate_limiter.acquire(self.name)

        # 解析季度
        year, month = self._parse_quarter(quarter)
        report_date = self._quarter_to_report_date(quarter)

        # AkShare 使用年份字符串
        year_str = str(year)

        try:
            # 获取持仓数据
            df = await self._run_sync(
                ak.fund_portfolio_hold_em,
                symbol=code,
                date=year_str,
            )

            if df is None or df.empty:
                return HoldingSnapshot(
                    fund_code=code,
                    report_date=report_date,
                    positions=[],
                )

            # 保存快照
            snapshot_data = df.to_csv(index=False)
            await self._save_snapshot(code, f"holdings_{quarter}", "csv", snapshot_data)

            # 过滤到指定季度
            positions: list[HoldingPosition] = []
            
            for _, row in df.iterrows():
                # 检查季度匹配
                row_quarter = row.get("季度", "")
                if not self._match_quarter(row_quarter, year, month):
                    continue

                stock_code = str(row.get("股票代码", "")).strip()
                stock_name = str(row.get("股票名称", "")).strip()
                
                # 解析占净值比（百分比 → 小数）
                weight_raw = row.get("占净值比例")
                weight = None
                if weight_raw is not None:
                    w = _safe_decimal(weight_raw)
                    if w is not None:
                        weight = w / Decimal("100")

                # 解析持股数（万股 → 股）
                shares_raw = row.get("持股数")
                shares = None
                if shares_raw is not None:
                    s = _safe_decimal(shares_raw)
                    if s is not None:
                        shares = s * Decimal("10000")

                # 解析市值（万元 → 元）
                market_value_raw = row.get("持仓市值")
                market_value = None
                if market_value_raw is not None:
                    mv = _safe_decimal(market_value_raw)
                    if mv is not None:
                        market_value = mv * Decimal("10000")

                if stock_code or stock_name:
                    positions.append(
                        HoldingPosition(
                            stock_code=stock_code or None,
                            stock_name=stock_name or None,
                            weight=weight,
                            shares=shares,
                            market_value=market_value,
                            industry=None,
                        )
                    )

            return HoldingSnapshot(
                fund_code=code,
                report_date=report_date,
                positions=positions,
            )

        except asyncio.TimeoutError as exc:
            raise ProviderTimeoutError(
                f"请求超时: holdings for {code} {quarter}",
                provider_name=self.name,
                fund_code=code,
            ) from exc
        except Exception as exc:
            if "不存在" in str(exc) or "not found" in str(exc).lower():
                raise ProviderNotFoundError(
                    f"基金代码或季度不存在: {code} {quarter}",
                    provider_name=self.name,
                    fund_code=code,
                ) from exc
            raise ProviderError(
                f"获取持仓数据失败: {exc}",
                provider_name=self.name,
                fund_code=code,
            ) from exc

    def _parse_quarter(self, quarter: str) -> tuple[int, int]:
        """解析季度字符串为 (年份, 月份)。

        Args:
            quarter: 格式 "YYYY-QN"，如 "2024-Q1"。

        Returns:
            (year, month) 元组，month 为季度末月份（3/6/9/12）。
        """
        m = re.match(r"(\d{4})-Q([1-4])", quarter, re.IGNORECASE)
        if not m:
            raise ValueError(f"无效的季度格式: {quarter!r}，应为 YYYY-QN")
        year = int(m.group(1))
        q = int(m.group(2))
        month = q * 3  # Q1→3, Q2→6, Q3→9, Q4→12
        return year, month

    def _quarter_to_report_date(self, quarter: str) -> date:
        """将季度字符串转换为报告期末日期。"""
        year, month = self._parse_quarter(quarter)
        # 季度末最后一天
        if month == 12:
            return date(year, 12, 31)
        elif month == 3:
            return date(year, 3, 31)
        elif month == 6:
            return date(year, 6, 30)
        else:  # month == 9
            return date(year, 9, 30)

    def _match_quarter(self, row_quarter: str, year: int, month: int) -> bool:
        """检查行数据的季度是否匹配目标季度。"""
        if not row_quarter:
            return False
        # AkShare 返回的季度格式可能是 "2024年1季度" 或 "2024-Q1" 等
        row_quarter = str(row_quarter).strip()
        
        # 尝试匹配 "YYYY年N季度" 格式
        m = re.search(r"(\d{4})年(\d)季度", row_quarter)
        if m:
            row_year = int(m.group(1))
            row_q = int(m.group(2))
            target_q = month // 3
            return row_year == year and row_q == target_q

        # 尝试匹配 "YYYY-QN" 格式
        m = re.search(r"(\d{4})-?Q?(\d)", row_quarter, re.IGNORECASE)
        if m:
            row_year = int(m.group(1))
            row_q = int(m.group(2))
            target_q = month // 3
            return row_year == year and row_q == target_q

        return False

    # ------------------------------------------------------------------
    # 4. 分红拆分 fetch_dividends
    # ------------------------------------------------------------------

    async def fetch_dividends(self, code: str) -> list[DividendRecord]:
        """从 AkShare 获取分红拆分记录。

        使用 fund_fh_em 接口获取分红数据。

        Args:
            code: 基金代码。

        Returns:
            按 ex_date 升序排列的 DividendRecord 列表。

        Raises:
            ProviderNotFoundError: 基金代码不存在。
            ProviderTimeoutError: 请求超时。
            ProviderError: 其他错误。
        """
        _check_akshare_available()
        await self._rate_limiter.acquire(self.name)

        try:
            # 获取分红数据
            df = await self._run_sync(ak.fund_fh_em, symbol=code)

            if df is None or df.empty:
                return []

            # 保存快照
            snapshot_data = df.to_csv(index=False)
            await self._save_snapshot(code, "dividends", "csv", snapshot_data)

            records: list[DividendRecord] = []

            for _, row in df.iterrows():
                # 解析除权日
                ex_date = _safe_date(row.get("除息日", row.get("权益登记日")))
                if ex_date is None:
                    continue

                # 解析权益登记日
                record_date = _safe_date(row.get("权益登记日"))
                
                # 解析派息日
                pay_date = _safe_date(row.get("派息日", row.get("红利发放日")))

                # 解析每份分红
                dividend_raw = row.get("每份分红", row.get("每份派息"))
                dividend_per_share = _safe_decimal(dividend_raw) or Decimal("0")

                records.append(
                    DividendRecord(
                        fund_code=code,
                        ex_date=ex_date,
                        record_date=record_date,
                        pay_date=pay_date,
                        dividend_per_share=dividend_per_share,
                        split_ratio=Decimal("1"),  # AkShare 分红接口不包含拆分信息
                    )
                )

            # 按除权日升序排列
            records.sort(key=lambda r: r.ex_date)
            return records

        except asyncio.TimeoutError as exc:
            raise ProviderTimeoutError(
                f"请求超时: dividends for {code}",
                provider_name=self.name,
                fund_code=code,
            ) from exc
        except Exception as exc:
            if "不存在" in str(exc) or "not found" in str(exc).lower():
                raise ProviderNotFoundError(
                    f"基金代码不存在: {code}",
                    provider_name=self.name,
                    fund_code=code,
                ) from exc
            raise ProviderError(
                f"获取分红数据失败: {exc}",
                provider_name=self.name,
                fund_code=code,
            ) from exc

    # ------------------------------------------------------------------
    # 5. 公告 fetch_announcements
    # ------------------------------------------------------------------

    async def fetch_announcements(
        self,
        code: str,
        since: date,
    ) -> list[Announcement]:
        """从 AkShare 获取基金公告。

        注意：AkShare 目前没有直接的基金公告接口，
        此方法返回空列表，公告数据应从主源（天天基金）获取。

        Args:
            code: 基金代码。
            since: 起始日期（含）。

        Returns:
            空列表（AkShare 不支持公告接口）。
        """
        _check_akshare_available()
        
        # AkShare 没有基金公告接口，返回空列表
        logger.info(
            "AkShare 不支持基金公告接口，fund_code=%s since=%s",
            code,
            since,
        )
        return []

    # ------------------------------------------------------------------
    # 6. 健康检查 health_check
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthStatus:
        """探测 AkShare 接口是否可用。

        通过请求基金列表接口来检测连通性。

        Returns:
            HealthStatus 实例。
        """
        if not _HAS_AKSHARE:
            return HealthStatus(
                healthy=False,
                message="akshare 库未安装",
                latency_ms=0,
            )

        start_time = time.monotonic()
        try:
            await self._rate_limiter.acquire(self.name)
            
            # 使用基金列表接口测试
            df = await self._run_sync(ak.fund_name_em)
            
            if df is None or df.empty:
                latency_ms = (time.monotonic() - start_time) * 1000
                return HealthStatus(
                    healthy=False,
                    message="AkShare 返回空数据",
                    latency_ms=latency_ms,
                )

            latency_ms = (time.monotonic() - start_time) * 1000
            return HealthStatus(
                healthy=True,
                message=f"AkShare 接口正常，基金数量: {len(df)}",
                latency_ms=latency_ms,
            )
        except asyncio.TimeoutError:
            latency_ms = (time.monotonic() - start_time) * 1000
            return HealthStatus(
                healthy=False,
                message="AkShare 请求超时",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - start_time) * 1000
            return HealthStatus(
                healthy=False,
                message=f"AkShare 接口异常: {exc}",
                latency_ms=latency_ms,
            )
