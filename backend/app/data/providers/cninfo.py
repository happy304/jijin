"""巨潮资讯网（CnInfo）数据 Provider 实现。

作为第三备源（priority=3），提供法定披露数据兜底。
巨潮资讯是中国证监会指定的信息披露网站，数据权威可靠。

设计要点：
- name = "cninfo", priority = 3
- 完全免费，无需注册/Token
- 使用 webapi.cninfo.com.cn 的 JSON 接口
- 公告数据是巨潮的核心优势（法定披露平台）
- 接入 RateLimiter（1 req/s）和 retry 装饰器
- 接入 SnapshotArchive 保存原始响应

巨潮资讯基金相关接口：
- /api/stock/p_public0001: 基金基本信息
- /api/fund/fundNavList: 基金净值
- /api/fund/fundDividend: 基金分红
- /api/disc/announcement: 基金公告（核心优势）
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.data.fetchers.rate_limiter import RateLimiter
from app.data.fetchers.retry import retry_on_network_error
from app.data.providers.base import (
    HealthStatus,
    ProviderError,
    ProviderNotFoundError,
    ProviderTimeoutError,
)
from app.data.providers.snapshot import SnapshotArchive
from app.data.schemas.funds import (
    Announcement,
    AnnouncementCategory,
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
# 常量
# ---------------------------------------------------------------------------

_BASE_URL = "http://webapi.cninfo.com.cn/api"
_REFERER = "http://webapi.cninfo.com.cn/"

_UA_POOL: list[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
]

# 基金类型映射
_FUND_TYPE_MAP: dict[str, FundType] = {
    "股票型": FundType.STOCK,
    "股票指数": FundType.INDEX,
    "指数型": FundType.INDEX,
    "债券型": FundType.BOND,
    "混合型": FundType.MIXED,
    "货币型": FundType.MONEY,
    "货币市场型": FundType.MONEY,
    "QDII": FundType.QDII,
    "FOF": FundType.FOF,
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _safe_decimal(value: Any, default: Decimal | None = None) -> Decimal | None:
    """将任意值安全转换为 Decimal。"""
    if value is None or value == "" or value == "--" or value == "---":
        return default
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except InvalidOperation:
        return default


def _safe_date(value: Any, fmt: str = "%Y-%m-%d") -> date | None:
    """将字符串安全解析为 date。"""
    if not value or str(value).strip() in ("", "--", "暂无"):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    value = str(value).strip()
    for f in (fmt, "%Y%m%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:10], f[:10] if "T" in f else f).date()
        except ValueError:
            continue
    return None


def _parse_fund_type(raw: str | None) -> FundType | None:
    """将中文基金类型映射到 FundType 枚举。"""
    if not raw:
        return None
    raw = raw.strip()
    for key, ft in _FUND_TYPE_MAP.items():
        if key in raw:
            return ft
    return None


# ---------------------------------------------------------------------------
# CnInfoProvider
# ---------------------------------------------------------------------------


class CnInfoProvider:
    """巨潮资讯网数据 Provider（第三备源，priority=3）。

    实现 FundDataProvider Protocol 的全部方法。
    使用巨潮资讯的公开 JSON API，完全免费，数据来源权威。

    Args:
        rate_limiter: 令牌桶限流器，默认 1 req/s。
        snapshot_archive: 原始响应归档。
        timeout: HTTP 请求超时秒数。
    """

    name: str = "cninfo"
    priority: int = 3

    def __init__(
        self,
        *,
        rate_limiter: RateLimiter | None = None,
        snapshot_archive: SnapshotArchive | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._rate_limiter = rate_limiter or RateLimiter(default_rate=1.0)
        self._rate_limiter.configure(self.name, rate=1.0)
        self._snapshot = snapshot_archive or SnapshotArchive()
        self._timeout = timeout
        self._ua_index = 0

    # ------------------------------------------------------------------
    # 内部 HTTP 工具
    # ------------------------------------------------------------------

    def _next_ua(self) -> str:
        """轮换返回下一个 User-Agent。"""
        ua = _UA_POOL[self._ua_index % len(_UA_POOL)]
        self._ua_index += 1
        return ua

    def _build_headers(self) -> dict[str, str]:
        """构建请求头。"""
        return {
            "User-Agent": self._next_ua(),
            "Referer": _REFERER,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }

    @retry_on_network_error
    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行限流 + 重试的 GET 请求，返回 JSON 响应。"""
        await self._rate_limiter.acquire(self.name)
        url = f"{_BASE_URL}{path}"
        headers = self._build_headers()

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            follow_redirects=True,
        ) as client:
            try:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except httpx.TimeoutException as exc:
                raise ProviderTimeoutError(
                    f"请求超时: {url}",
                    provider_name=self.name,
                ) from exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise ProviderNotFoundError(
                        f"资源不存在: {url}",
                        provider_name=self.name,
                    ) from exc
                raise ProviderError(
                    f"HTTP 错误 {exc.response.status_code}: {url}",
                    provider_name=self.name,
                ) from exc
            except json.JSONDecodeError as exc:
                raise ProviderError(
                    f"JSON 解析失败: {url}",
                    provider_name=self.name,
                ) from exc

    @retry_on_network_error
    async def _post(
        self,
        path: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行限流 + 重试的 POST 请求，返回 JSON 响应。"""
        await self._rate_limiter.acquire(self.name)
        url = f"{_BASE_URL}{path}"
        headers = self._build_headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            follow_redirects=True,
        ) as client:
            try:
                resp = await client.post(url, data=data, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except httpx.TimeoutException as exc:
                raise ProviderTimeoutError(
                    f"请求超时: {url}",
                    provider_name=self.name,
                ) from exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise ProviderNotFoundError(
                        f"资源不存在: {url}",
                        provider_name=self.name,
                    ) from exc
                raise ProviderError(
                    f"HTTP 错误 {exc.response.status_code}: {url}",
                    provider_name=self.name,
                ) from exc
            except json.JSONDecodeError as exc:
                raise ProviderError(
                    f"JSON 解析失败: {url}",
                    provider_name=self.name,
                ) from exc

    async def _save_snapshot(
        self,
        fund_code: str,
        endpoint: str,
        data: dict[str, Any],
    ) -> None:
        """保存原始响应快照。"""
        try:
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            await self._snapshot.async_save_raw(
                provider=self.name,
                fund_code=fund_code,
                endpoint=endpoint,
                ext="json",
                data=raw,
            )
        except Exception as exc:
            logger.warning(
                "快照保存失败 fund=%s endpoint=%s: %s",
                fund_code, endpoint, exc,
            )

    # ------------------------------------------------------------------
    # 1. 基础信息 fetch_fund_meta
    # ------------------------------------------------------------------

    async def fetch_fund_meta(self, code: str) -> FundMeta:
        """从巨潮资讯获取基金基础信息。

        使用基金信息查询接口获取法定披露的基本信息。

        Args:
            code: 基金代码（如 "000001"）。

        Returns:
            FundMeta 实例。

        Raises:
            ProviderNotFoundError: 基金代码不存在。
            ProviderTimeoutError: 请求超时。
            ProviderError: 其他错误。
        """
        data = await self._post(
            "/stock/p_public0001",
            data={"scode": code},
        )
        await self._save_snapshot(code, "fund_meta", data)

        # 解析响应
        records = data.get("records", [])
        if not records:
            raise ProviderNotFoundError(
                f"基金代码不存在: {code}",
                provider_name=self.name,
                fund_code=code,
            )

        info = records[0] if records else {}

        # 字段提取
        name = info.get("SECNAME", info.get("F002V", code))
        fund_type_raw = info.get("F009V", info.get("FUNDTYPE", ""))
        inception_raw = info.get("F007D", info.get("ESTABDATE", ""))
        mgmt_fee_raw = info.get("F026N", "")
        cust_fee_raw = info.get("F027N", "")

        # 费率解析
        management_fee: Decimal | None = None
        if mgmt_fee_raw:
            mf = _safe_decimal(mgmt_fee_raw)
            if mf is not None:
                # 巨潮返回的可能是百分比数值或小数
                management_fee = mf / Decimal("100") if mf > Decimal("1") else mf

        custodian_fee: Decimal | None = None
        if cust_fee_raw:
            cf = _safe_decimal(cust_fee_raw)
            if cf is not None:
                custodian_fee = cf / Decimal("100") if cf > Decimal("1") else cf

        return FundMeta(
            code=code,
            name=name or code,
            fund_type=_parse_fund_type(fund_type_raw),
            sub_type=fund_type_raw or None,
            inception_date=_safe_date(inception_raw),
            management_fee=management_fee,
            custodian_fee=custodian_fee,
            status=FundStatus.ACTIVE,
            is_purchasable=True,
            purchase_limit=None,
            source=self.name,
            updated_at=datetime.now(tz=timezone.utc),
        )

    # ------------------------------------------------------------------
    # 2. 历史净值 fetch_nav_history
    # ------------------------------------------------------------------

    async def fetch_nav_history(
        self,
        code: str,
        start: date,
        end: date,
    ) -> list[NavRecord]:
        """从巨潮资讯获取历史净值。

        Args:
            code: 基金代码。
            start: 起始日期（含）。
            end: 结束日期（含）。

        Returns:
            按 trade_date 升序排列的 NavRecord 列表。
        """
        records: list[NavRecord] = []
        page = 1

        while True:
            data = await self._post(
                "/fund/fundNavList",
                data={
                    "code": code,
                    "sdate": start.strftime("%Y-%m-%d"),
                    "edate": end.strftime("%Y-%m-%d"),
                    "curpage": str(page),
                },
            )

            if page == 1:
                await self._save_snapshot(code, "nav_history", data)

            items = data.get("records", [])
            if not items:
                break

            for item in items:
                trade_date = _safe_date(
                    item.get("NAVDATE", item.get("F001D", ""))
                )
                if trade_date is None:
                    continue

                unit_nav = _safe_decimal(item.get("UNITNAV", item.get("F002N")))
                accum_nav = _safe_decimal(item.get("ACCNAV", item.get("F003N")))

                # 日涨跌幅
                daily_return: Decimal | None = None
                dr_raw = item.get("NAVCHGRT", item.get("F004N"))
                if dr_raw is not None:
                    dr = _safe_decimal(dr_raw)
                    if dr is not None:
                        # 巨潮返回百分比数值
                        daily_return = dr / Decimal("100")

                records.append(
                    NavRecord(
                        fund_code=code,
                        trade_date=trade_date,
                        unit_nav=unit_nav,
                        accum_nav=accum_nav,
                        adj_nav=None,
                        daily_return=daily_return,
                        status=NavStatus.NORMAL,
                        source=self.name,
                    )
                )

            # 分页判断
            total_pages = data.get("totalpages", data.get("totalPages", 1))
            if page >= int(total_pages):
                break
            page += 1

        # 按日期升序排列
        records.sort(key=lambda r: r.trade_date)
        return records

    # ------------------------------------------------------------------
    # 3. 持仓 fetch_holdings
    # ------------------------------------------------------------------

    async def fetch_holdings(self, code: str, quarter: str) -> HoldingSnapshot:
        """从巨潮资讯获取季度持仓。

        Args:
            code: 基金代码。
            quarter: 季度标识，格式 "YYYY-QN"。

        Returns:
            HoldingSnapshot 实例。
        """
        report_date = self._quarter_to_report_date(quarter)

        data = await self._post(
            "/fund/fundPortfolio",
            data={
                "code": code,
                "rdate": report_date.strftime("%Y-%m-%d"),
            },
        )
        await self._save_snapshot(code, f"holdings_{quarter}", data)

        items = data.get("records", [])
        positions: list[HoldingPosition] = []

        for item in items:
            stock_code = item.get("SCODE", item.get("F002V", ""))
            stock_name = item.get("SNAME", item.get("F003V", ""))

            weight_raw = item.get("RATIO", item.get("F004N"))
            weight: Decimal | None = None
            if weight_raw is not None:
                w = _safe_decimal(weight_raw)
                if w is not None:
                    weight = w / Decimal("100") if w > Decimal("1") else w

            shares = _safe_decimal(item.get("QUANTITY", item.get("F005N")))
            market_value = _safe_decimal(item.get("MKTVAL", item.get("F006N")))

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

    # ------------------------------------------------------------------
    # 4. 分红 fetch_dividends
    # ------------------------------------------------------------------

    async def fetch_dividends(self, code: str) -> list[DividendRecord]:
        """从巨潮资讯获取分红记录（法定披露源）。

        Args:
            code: 基金代码。

        Returns:
            按 ex_date 升序排列的 DividendRecord 列表。
        """
        data = await self._post(
            "/fund/fundDividend",
            data={"code": code},
        )
        await self._save_snapshot(code, "dividends", data)

        items = data.get("records", [])
        records: list[DividendRecord] = []

        for item in items:
            ex_date = _safe_date(item.get("EXDIVDATE", item.get("F003D")))
            if ex_date is None:
                continue

            record_date = _safe_date(item.get("REGDATE", item.get("F002D")))
            pay_date = _safe_date(item.get("PAYDATE", item.get("F004D")))

            dividend_raw = item.get("DIVAMT", item.get("F005N", "0"))
            dividend_per_share = _safe_decimal(dividend_raw) or Decimal("0")

            records.append(
                DividendRecord(
                    fund_code=code,
                    ex_date=ex_date,
                    record_date=record_date,
                    pay_date=pay_date,
                    dividend_per_share=dividend_per_share,
                    split_ratio=Decimal("1"),
                )
            )

        records.sort(key=lambda r: r.ex_date)
        return records

    # ------------------------------------------------------------------
    # 5. 公告 fetch_announcements（巨潮核心优势）
    # ------------------------------------------------------------------

    async def fetch_announcements(
        self,
        code: str,
        since: date,
    ) -> list[Announcement]:
        """从巨潮资讯获取基金公告（法定披露平台，核心优势）。

        巨潮是证监会指定的信息披露网站，公告数据最全最准。

        Args:
            code: 基金代码。
            since: 起始日期（含）。

        Returns:
            按 publish_date 升序排列的 Announcement 列表。
        """
        announcements: list[Announcement] = []
        page = 1

        while True:
            data = await self._post(
                "/disc/announcement",
                data={
                    "code": code,
                    "sdate": since.strftime("%Y-%m-%d"),
                    "edate": date.today().strftime("%Y-%m-%d"),
                    "curpage": str(page),
                },
            )

            if page == 1:
                await self._save_snapshot(code, "announcements", data)

            items = data.get("records", [])
            if not items:
                break

            for item in items:
                publish_date = _safe_date(
                    item.get("DECLAREDATE", item.get("F001D", ""))
                )
                if publish_date is None:
                    continue

                if publish_date < since:
                    continue

                title = item.get("TITLE", item.get("F002V", ""))
                content_url = item.get("ATTACHURL", item.get("F003V", ""))

                # 根据标题初步分类
                category = self._classify_announcement(title)

                announcements.append(
                    Announcement(
                        fund_code=code,
                        title=title,
                        category=category,
                        publish_date=publish_date,
                        content_url=content_url,
                        parsed_data=None,
                        requires_review=category is None,
                    )
                )

            # 分页判断
            total_pages = data.get("totalpages", data.get("totalPages", 1))
            if page >= int(total_pages):
                break
            page += 1

        announcements.sort(key=lambda a: a.publish_date or date.min)
        return announcements

    def _classify_announcement(self, title: str) -> AnnouncementCategory | None:
        """根据公告标题进行初步分类。

        巨潮公告标题通常包含明确的关键词，可以做规则分类。
        低置信度的留给 LLM 二次分类。
        """
        if not title:
            return None

        # 限购/暂停申购
        if any(kw in title for kw in ("限制大额申购", "暂停申购", "限制申购", "暂停大额")):
            return AnnouncementCategory.LIMIT_PURCHASE

        # 暂停/终止
        if any(kw in title for kw in ("暂停运作", "终止", "清算", "暂停赎回")):
            return AnnouncementCategory.SUSPEND

        # 分红
        if any(kw in title for kw in ("分红", "收益分配", "派息")):
            return AnnouncementCategory.DIVIDEND

        # 基金经理变更
        if any(kw in title for kw in ("基金经理变更", "增聘基金经理", "解聘基金经理")):
            return AnnouncementCategory.MANAGER_CHANGE

        # 合同变更
        if any(kw in title for kw in ("合同修改", "合同变更", "托管协议", "招募说明书")):
            return AnnouncementCategory.CONTRACT_CHANGE

        return None

    # ------------------------------------------------------------------
    # 6. 健康检查
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthStatus:
        """探测巨潮资讯接口是否可用。"""
        start_time = time.monotonic()
        try:
            # 使用轻量级接口探活
            data = await self._post(
                "/stock/p_public0001",
                data={"scode": "000001"},
            )
            latency_ms = (time.monotonic() - start_time) * 1000

            if data.get("records"):
                return HealthStatus(
                    healthy=True,
                    message="巨潮资讯接口正常",
                    latency_ms=latency_ms,
                )
            else:
                return HealthStatus(
                    healthy=False,
                    message="巨潮资讯返回空数据",
                    latency_ms=latency_ms,
                )
        except Exception as exc:
            latency_ms = (time.monotonic() - start_time) * 1000
            return HealthStatus(
                healthy=False,
                message=f"巨潮资讯接口异常: {exc}",
                latency_ms=latency_ms,
            )

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _quarter_to_report_date(self, quarter: str) -> date:
        """将季度字符串转换为报告期末日期。"""
        m = re.match(r"(\d{4})-Q([1-4])", quarter, re.IGNORECASE)
        if not m:
            raise ValueError(f"无效的季度格式: {quarter!r}，应为 YYYY-QN")
        year = int(m.group(1))
        q = int(m.group(2))
        month = q * 3
        if month == 12:
            return date(year, 12, 31)
        elif month == 3:
            return date(year, 3, 31)
        elif month == 6:
            return date(year, 6, 30)
        else:
            return date(year, 9, 30)
