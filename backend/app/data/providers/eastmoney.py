"""天天基金（EastMoney）数据 Provider 实现。

实现 FundDataProvider Protocol 的全部方法，覆盖 9 个接口：
  1. 基础信息   - fundf10.eastmoney.com/jbgk_{code}.html
  2. 历史净值   - api.fund.eastmoney.com/f10/lsjz
  3. 实时估值   - fundgz.1234567.com.cn/js/{code}.js
  4. 综合数据   - fund.eastmoney.com/pingzhongdata/{code}.js
  5. 持仓       - fundf10.eastmoney.com/FundArchivesDatas.aspx
  6. 分红拆分   - fundf10.eastmoney.com/fhsp_{code}.html
  7. 排名榜单   - fund.eastmoney.com/data/rankhandler.aspx
  8. 基金经理   - fundf10.eastmoney.com/jjjl_{code}.html
  9. 公告       - api.fund.eastmoney.com/f10/JJGG

设计要点：
- name = "eastmoney", priority = 1
- 每次请求携带 Referer + UA（UA 池轮换）
- 接入 RateLimiter（2 req/s）和 retry_on_network_error 装饰器
- 接入 SnapshotArchive 保存原始响应
- HTML 解析优先使用 selectolax，回退到 re 模块
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
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
# 尝试导入 selectolax（HTML 解析首选），回退到纯 re
# ---------------------------------------------------------------------------
try:
    from selectolax.parser import HTMLParser as _HTMLParser  # type: ignore[import-untyped]

    _HAS_SELECTOLAX = True
except ImportError:  # pragma: no cover
    _HAS_SELECTOLAX = False

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
_REFERER = "http://fundf10.eastmoney.com/"
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
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
]

# 基金类型映射（天天基金中文 → FundType）
_FUND_TYPE_MAP: dict[str, FundType] = {
    "股票型": FundType.STOCK,
    "股票指数": FundType.INDEX,
    "增强指数型": FundType.INDEX,
    "债券型": FundType.BOND,
    "混合型": FundType.MIXED,
    "货币市场型": FundType.MONEY,
    "货币型": FundType.MONEY,
    "QDII": FundType.QDII,
    "FOF": FundType.FOF,
    "指数型": FundType.INDEX,
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _safe_decimal(value: Any, default: Decimal | None = None) -> Decimal | None:
    """将任意值安全转换为 Decimal，失败返回 default。"""
    if value is None or value == "" or value == "--":
        return default
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return default


def _safe_date(value: str | None, fmt: str = "%Y-%m-%d") -> date | None:
    """将字符串安全解析为 date，失败返回 None。"""
    if not value or value.strip() in ("", "--", "暂无"):
        return None
    value = value.strip()
    # 尝试多种日期格式
    for f in (fmt, "%Y年%m月%d日", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, f).date()
        except ValueError:
            continue
    return None


def _parse_fund_type(raw: str | None) -> FundType | None:
    """将天天基金的中文基金类型映射到 FundType 枚举。"""
    if not raw:
        return None
    raw = raw.strip()
    for key, ft in _FUND_TYPE_MAP.items():
        if key in raw:
            return ft
    return None


def _extract_text_selectolax(html: str, css: str) -> str:
    """用 selectolax 提取第一个匹配节点的文本。"""
    tree = _HTMLParser(html)
    node = tree.css_first(css)
    return node.text(strip=True) if node else ""


def _extract_table_rows_selectolax(html: str, table_css: str) -> list[list[str]]:
    """用 selectolax 提取表格所有行的文本列表。"""
    tree = _HTMLParser(html)
    rows: list[list[str]] = []
    for tr in tree.css(f"{table_css} tr"):
        cells = [td.text(strip=True) for td in tr.css("td,th")]
        if cells:
            rows.append(cells)
    return rows


def _extract_table_rows_re(html: str) -> list[list[str]]:
    """用正则提取 HTML 表格行（selectolax 不可用时的回退）。"""
    rows: list[list[str]] = []
    for tr_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE):
        tr_html = tr_match.group(1)
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr_html, re.DOTALL | re.IGNORECASE)
        cleaned = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        if cleaned:
            rows.append(cleaned)
    return rows


# ---------------------------------------------------------------------------
# EastmoneyProvider
# ---------------------------------------------------------------------------


class EastmoneyProvider:
    """天天基金数据 Provider（主源，priority=1）。

    实现 FundDataProvider Protocol 的全部方法。

    Args:
        rate_limiter: 令牌桶限流器，默认 2 req/s。
        snapshot_archive: 原始响应归档，默认写入 ./local_data/snapshots。
        timeout: HTTP 请求超时秒数。
    """

    name: str = "eastmoney"
    priority: int = 1

    def __init__(
        self,
        *,
        rate_limiter: RateLimiter | None = None,
        snapshot_archive: SnapshotArchive | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._rate_limiter = rate_limiter or RateLimiter(default_rate=5.0)
        self._rate_limiter.configure(self.name, rate=5.0)
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

    def _build_headers(self, referer: str = _REFERER) -> dict[str, str]:
        """构建请求头（含 Referer 和轮换 UA）。"""
        return {
            "User-Agent": self._next_ua(),
            "Referer": referer,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }

    @retry_on_network_error
    async def _get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        referer: str = _REFERER,
    ) -> httpx.Response:
        """执行限流 + 重试的 GET 请求。"""
        await self._rate_limiter.acquire(self.name)
        headers = self._build_headers(referer)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            follow_redirects=True,
        ) as client:
            try:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp
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

    async def _save_snapshot(
        self,
        fund_code: str,
        endpoint: str,
        ext: str,
        data: bytes,
    ) -> None:
        """异步保存原始响应快照（失败不影响主流程）。"""
        try:
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
        """从 fundf10.eastmoney.com/jbgk_{code}.html 获取基金基础信息。

        Args:
            code: 基金代码（如 "000001"）。

        Returns:
            FundMeta 实例。

        Raises:
            ProviderNotFoundError: 基金代码不存在。
            ProviderTimeoutError: 请求超时。
            ProviderError: 其他错误。
        """
        url = f"http://fundf10.eastmoney.com/jbgk_{code}.html"
        resp = await self._get(url)
        raw = resp.content
        await self._save_snapshot(code, "fund_meta", "html", raw)

        html = raw.decode("utf-8", errors="replace")
        return self._parse_fund_meta(code, html)

    def _parse_fund_meta(self, code: str, html: str) -> FundMeta:
        """解析基金基础信息 HTML 页面。"""
        info: dict[str, str] = {}

        if _HAS_SELECTOLAX:
            tree = _HTMLParser(html)
            # 天天基金 jbgk 页面：.info 表格，每行 th + td
            for row in tree.css("table.info tr"):
                ths = row.css("th")
                tds = row.css("td")
                for th, td in zip(ths, tds):
                    key = th.text(strip=True).rstrip("：").rstrip(":")
                    val = td.text(strip=True)
                    info[key] = val
        else:
            # 正则回退：提取 <th>key</th><td>val</td> 对
            for m in re.finditer(
                r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>",
                html,
                re.DOTALL | re.IGNORECASE,
            ):
                key = re.sub(r"<[^>]+>", "", m.group(1)).strip().rstrip("：").rstrip(":")
                val = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                info[key] = val

        # 提取基金名称（页面标题或 h4）
        name = ""
        if _HAS_SELECTOLAX:
            tree2 = _HTMLParser(html)
            h4 = tree2.css_first(".fundDetail-tit h4") or tree2.css_first("h4")
            if h4:
                name = h4.text(strip=True)
        if not name:
            m = re.search(r"<h4[^>]*>(.*?)</h4>", html, re.DOTALL | re.IGNORECASE)
            if m:
                name = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if not name:
            name = info.get("基金全称", info.get("基金简称", code))

        # 字段映射
        fund_type_raw = info.get("基金类型", info.get("基金类别", ""))
        inception_raw = info.get("成立日期", info.get("基金成立日", info.get("成立日期/规模", "")))
        # "成立日期/规模" 格式为 "2015年03月12日 / 59.740亿份"，取斜杠前的日期部分
        if "/" in inception_raw and "年" in inception_raw:
            inception_raw = inception_raw.split("/")[0].strip()
        mgmt_fee_raw = info.get("管理费率", "")
        cust_fee_raw = info.get("托管费率", "")
        status_raw = info.get("基金状态", "正常")
        purchase_raw = info.get("购买状态", "")
        limit_raw = info.get("单次购买上限", info.get("申购上限", ""))

        # 管理费率解析（"1.50%/年" → Decimal("0.015")）
        def _parse_rate(raw: str) -> Decimal | None:
            m2 = re.search(r"([\d.]+)%", raw)
            if m2:
                try:
                    return Decimal(m2.group(1)) / Decimal("100")
                except InvalidOperation:
                    pass
            return None

        # 购买上限解析（"100万元" → Decimal("1000000")）
        def _parse_limit(raw: str) -> Decimal | None:
            if not raw or raw in ("--", "暂无限制", "不限"):
                return None
            m2 = re.search(r"([\d,.]+)\s*万", raw)
            if m2:
                try:
                    return Decimal(m2.group(1).replace(",", "")) * Decimal("10000")
                except InvalidOperation:
                    pass
            m2 = re.search(r"([\d,.]+)", raw)
            if m2:
                try:
                    return Decimal(m2.group(1).replace(",", ""))
                except InvalidOperation:
                    pass
            return None

        is_purchasable = "暂停" not in purchase_raw and "限制" not in purchase_raw
        status = FundStatus.ACTIVE
        if "暂停" in status_raw or "清盘" in status_raw:
            status = FundStatus.SUSPENDED

        # 业绩比较基准
        benchmark_raw = info.get("业绩比较基准", info.get("业绩基准", ""))
        benchmark = benchmark_raw if benchmark_raw and benchmark_raw not in ("--", "---", "") else None

        return FundMeta(
            code=code,
            name=name or code,
            fund_type=_parse_fund_type(fund_type_raw),
            sub_type=fund_type_raw or None,
            inception_date=_safe_date(inception_raw),
            benchmark=benchmark,
            management_fee=_parse_rate(mgmt_fee_raw),
            custodian_fee=_parse_rate(cust_fee_raw),
            status=status,
            is_purchasable=is_purchasable,
            purchase_limit=_parse_limit(limit_raw),
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
        """从 api.fund.eastmoney.com/f10/lsjz 获取历史净值。

        天天基金 API 对单次请求的数据量有隐性限制，当日期跨度较大时
        （如全量采集从成立日期到今天），需要按年分段请求以确保获取完整数据。

        Args:
            code: 基金代码。
            start: 起始日期（含）。
            end: 结束日期（含）。

        Returns:
            按 trade_date 升序排列的 NavRecord 列表。
        """
        from datetime import timedelta

        # 如果日期跨度超过 1 年，按年分段请求以避免 API 截断数据
        span_days = (end - start).days
        if span_days > 365:
            all_records: list[NavRecord] = []
            seg_start = start
            while seg_start <= end:
                seg_end = min(seg_start + timedelta(days=365), end)
                segment = await self._fetch_nav_page_loop(code, seg_start, seg_end)
                all_records.extend(segment)
                logger.info(
                    "fetch_nav_history 分段完成: fund=%s, %s ~ %s, 获取 %d 条",
                    code, seg_start, seg_end, len(segment),
                )
                seg_start = seg_end + timedelta(days=1)
            # 去重（按 trade_date）并排序
            seen: set[date] = set()
            unique_records: list[NavRecord] = []
            for r in all_records:
                if r.trade_date not in seen:
                    seen.add(r.trade_date)
                    unique_records.append(r)
            unique_records.sort(key=lambda r: r.trade_date)
            return unique_records
        else:
            return await self._fetch_nav_page_loop(code, start, end)

    async def _fetch_nav_page_loop(
        self,
        code: str,
        start: date,
        end: date,
    ) -> list[NavRecord]:
        """分页循环获取指定日期范围内的历史净值。

        天天基金 API 服务端强制每页最多返回 20 条数据（无论请求的 pageSize 多大），
        因此 pageSize 必须设为 20 以确保分页终止条件正确工作。

        Args:
            code: 基金代码。
            start: 起始日期（含）。
            end: 结束日期（含）。

        Returns:
            NavRecord 列表（未排序）。
        """
        records: list[NavRecord] = []
        page = 1
        page_size = 20  # 天天基金 API 服务端强制每页最多 20 条

        while True:
            params = {
                "fundCode": code,
                "pageIndex": page,
                "pageSize": page_size,
                "startDate": start.strftime("%Y-%m-%d"),
                "endDate": end.strftime("%Y-%m-%d"),
                "callback": "",
            }
            resp = await self._get(
                "http://api.fund.eastmoney.com/f10/lsjz",
                params=params,
                referer=f"http://fundf10.eastmoney.com/jdzf_{code}.html",
            )
            raw = resp.content
            if page == 1:
                await self._save_snapshot(code, "nav_history", "json", raw)

            try:
                text = raw.decode("utf-8", errors="replace").strip()
                # 处理可能的 JSONP 包装（即使 callback 为空，某些情况下仍可能返回 JSONP）
                if text.startswith("(") and text.endswith(")"):
                    text = text[1:-1]
                elif text.startswith("jQuery") or text.startswith("jsonp"):
                    # 提取 JSONP 中的 JSON 部分
                    import re as _re
                    m = _re.search(r"\((\{.*\})\)\s*;?\s*$", text, _re.DOTALL)
                    if m:
                        text = m.group(1)
                data = json.loads(text)
            except Exception as exc:
                raise ProviderError(
                    f"历史净值 JSON 解析失败: {exc}",
                    provider_name=self.name,
                    fund_code=code,
                ) from exc

            items = (
                data.get("Data", {}).get("LSJZList", [])
                if isinstance(data.get("Data"), dict)
                else []
            )
            if not items:
                break

            for item in items:
                trade_date = _safe_date(item.get("FSRQ"))
                if trade_date is None:
                    continue
                unit_nav = _safe_decimal(item.get("DWJZ"))
                accum_nav = _safe_decimal(item.get("LJJZ"))
                daily_return_raw = item.get("JZZZL", "")
                daily_return: Decimal | None = None
                if daily_return_raw and daily_return_raw not in ("", "--"):
                    dr = _safe_decimal(daily_return_raw)
                    if dr is not None:
                        # 天天基金返回的是百分比字符串，如 "1.23"
                        daily_return = dr / Decimal("100")

                status_raw = item.get("FHSP", "")
                nav_status = NavStatus.NORMAL
                if "暂停" in str(status_raw):
                    nav_status = NavStatus.SUSPENDED

                records.append(
                    NavRecord(
                        fund_code=code,
                        trade_date=trade_date,
                        unit_nav=unit_nav,
                        accum_nav=accum_nav,
                        adj_nav=None,  # 由 adj_nav 服务计算
                        daily_return=daily_return,
                        status=nav_status,
                        source=self.name,
                    )
                )

            # 判断是否还有更多页
            # TotalCount 可能在顶层或 Data 内部
            total_count = data.get("TotalCount", 0)
            if not total_count and isinstance(data.get("Data"), dict):
                total_count = data["Data"].get("TotalCount", 0)
            # 安全转换为 int
            try:
                total_count = int(total_count)
            except (TypeError, ValueError):
                total_count = 0

            if total_count > 0 and len(records) >= total_count:
                break
            if len(items) < page_size:
                # 返回的记录数少于请求的 pageSize，说明已经是最后一页
                break
            page += 1

        # 按日期升序排列
        records.sort(key=lambda r: r.trade_date)
        return records

    # ------------------------------------------------------------------
    # 3. 实时估值（内部辅助，非 Protocol 方法）
    # ------------------------------------------------------------------

    async def fetch_realtime_estimate(self, code: str) -> dict[str, Any]:
        """从 fundgz.1234567.com.cn/js/{code}.js 获取实时估值（JSONP）。

        Returns:
            包含 gsz（估算净值）、gszzl（估算涨跌幅）、gztime（估算时间）的字典。
        """
        url = f"http://fundgz.1234567.com.cn/js/{code}.js"
        resp = await self._get(url, referer="http://fund.eastmoney.com/")
        raw = resp.content
        await self._save_snapshot(code, "realtime_estimate", "js", raw)

        text = raw.decode("utf-8", errors="replace")
        # JSONP 格式：jsonpgz({...});
        m = re.search(r"jsonpgz\s*\(\s*(\{.*?\})\s*\)\s*;?", text, re.DOTALL)
        if not m:
            raise ProviderError(
                f"实时估值 JSONP 解析失败: {text[:200]}",
                provider_name=self.name,
                fund_code=code,
            )
        import json
        try:
            return json.loads(m.group(1))
        except Exception as exc:
            raise ProviderError(
                f"实时估值 JSON 解析失败: {exc}",
                provider_name=self.name,
                fund_code=code,
            ) from exc

    # ------------------------------------------------------------------
    # 4. 综合数据 pingzhongdata（内部辅助）
    # ------------------------------------------------------------------

    async def fetch_pingzhongdata(self, code: str) -> dict[str, Any]:
        """从 fund.eastmoney.com/pingzhongdata/{code}.js 获取综合数据。

        Returns:
            包含净值序列、持仓等综合数据的字典。
        """
        url = f"http://fund.eastmoney.com/pingzhongdata/{code}.js"
        resp = await self._get(url, referer="http://fund.eastmoney.com/")
        raw = resp.content
        await self._save_snapshot(code, "pingzhongdata", "js", raw)

        text = raw.decode("utf-8", errors="replace")
        result: dict[str, Any] = {}

        # 提取 JS 变量：var name = value;
        for m in re.finditer(
            r"var\s+(\w+)\s*=\s*(\[.*?\]|\{.*?\}|\".*?\"|'.*?'|\d[\d.]*)\s*;",
            text,
            re.DOTALL,
        ):
            var_name = m.group(1)
            var_val_str = m.group(2)
            try:
                result[var_name] = json.loads(var_val_str)
            except Exception:
                result[var_name] = var_val_str

        return result

    # ------------------------------------------------------------------
    # 5. 持仓 fetch_holdings
    # ------------------------------------------------------------------

    async def fetch_holdings(self, code: str, quarter: str) -> HoldingSnapshot:
        """从 fundf10.eastmoney.com/FundArchivesDatas.aspx 获取季度持仓。

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
        # 解析季度 → 年份和月份
        year, month = self._parse_quarter(quarter)
        report_date = self._quarter_to_report_date(quarter)

        # 天天基金持仓接口
        url = "http://fundf10.eastmoney.com/FundArchivesDatas.aspx"
        params = {
            "type": "jjcc",
            "code": code,
            "topline": "20",  # 前 20 大持仓
            "year": str(year),
            "month": str(month),
        }
        resp = await self._get(url, params=params)
        raw = resp.content
        await self._save_snapshot(code, f"holdings_{quarter}", "html", raw)

        html = raw.decode("utf-8", errors="replace")
        positions = self._parse_holdings_html(html)

        return HoldingSnapshot(
            fund_code=code,
            report_date=report_date,
            positions=positions,
        )

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

    def _parse_holdings_html(self, html: str) -> list[HoldingPosition]:
        """解析持仓 HTML 页面，提取持仓列表。"""
        positions: list[HoldingPosition] = []

        if _HAS_SELECTOLAX:
            tree = _HTMLParser(html)
            # 天天基金持仓表格：class="w782 comm tzxq"
            for table in tree.css("table.w782, table.comm"):
                for tr in table.css("tbody tr"):
                    tds = tr.css("td")
                    if len(tds) < 7:
                        continue
                    # 实际列：序号(0)、股票代码(1)、股票名称(2)、最新价(3)、
                    #         涨跌幅(4)、相关资讯(5)、占净值比例(6)、持股数(7)、持仓市值(8)
                    stock_code = tds[1].text(strip=True) if len(tds) > 1 else None
                    stock_name = tds[2].text(strip=True) if len(tds) > 2 else None
                    weight_raw = tds[6].text(strip=True) if len(tds) > 6 else ""
                    shares_raw = tds[7].text(strip=True) if len(tds) > 7 else ""
                    market_value_raw = tds[8].text(strip=True) if len(tds) > 8 else ""

                    # 解析占净值比（"5.23%" → Decimal("0.0523")）
                    weight = self._parse_percent(weight_raw)
                    # 解析持股数（万股 → 股）
                    shares = self._parse_wan(shares_raw)
                    # 解析市值（万元 → 元）
                    market_value = self._parse_wan(market_value_raw)

                    if stock_code or stock_name:
                        positions.append(
                            HoldingPosition(
                                stock_code=stock_code or None,
                                stock_name=stock_name or None,
                                weight=weight,
                                shares=shares,
                                market_value=market_value,
                                industry=None,  # 行业信息需要额外接口
                            )
                        )
        else:
            # 正则回退
            rows = _extract_table_rows_re(html)
            for row in rows:
                if len(row) < 7:
                    continue
                # 跳过表头
                if "股票代码" in row[1] or "序号" in row[0]:
                    continue
                # 实际列：序号(0)、股票代码(1)、股票名称(2)、最新价(3)、
                #         涨跌幅(4)、相关资讯(5)、占净值比例(6)、持股数(7)、持仓市值(8)
                stock_code = row[1].strip() if len(row) > 1 else None
                stock_name = row[2].strip() if len(row) > 2 else None
                weight_raw = row[6].strip() if len(row) > 6 else ""
                shares_raw = row[7].strip() if len(row) > 7 else ""
                market_value_raw = row[8].strip() if len(row) > 8 else ""

                weight = self._parse_percent(weight_raw)
                shares = self._parse_wan(shares_raw)
                market_value = self._parse_wan(market_value_raw)

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

        return positions

    def _parse_percent(self, raw: str) -> Decimal | None:
        """解析百分比字符串（"5.23%" → Decimal("0.0523")）。"""
        if not raw or raw in ("--", "---", ""):
            return None
        m = re.search(r"([\d.]+)%?", raw)
        if m:
            try:
                return Decimal(m.group(1)) / Decimal("100")
            except InvalidOperation:
                pass
        return None

    def _parse_wan(self, raw: str) -> Decimal | None:
        """解析万单位数值（"123.45" 万 → Decimal("1234500")）。"""
        if not raw or raw in ("--", "---", ""):
            return None
        # 移除逗号和空格
        cleaned = raw.replace(",", "").replace(" ", "").replace("万", "")
        try:
            val = Decimal(cleaned)
            # 天天基金持仓数据单位是万股/万元
            return val * Decimal("10000")
        except InvalidOperation:
            return None

    # ------------------------------------------------------------------
    # 6. 分红拆分 fetch_dividends
    # ------------------------------------------------------------------

    async def fetch_dividends(self, code: str) -> list[DividendRecord]:
        """从 fundf10.eastmoney.com/fhsp_{code}.html 获取分红拆分记录。

        Args:
            code: 基金代码。

        Returns:
            按 ex_date 升序排列的 DividendRecord 列表。

        Raises:
            ProviderNotFoundError: 基金代码不存在。
            ProviderTimeoutError: 请求超时。
            ProviderError: 其他错误。
        """
        url = f"http://fundf10.eastmoney.com/fhsp_{code}.html"
        resp = await self._get(url)
        raw = resp.content
        await self._save_snapshot(code, "dividends", "html", raw)

        html = raw.decode("utf-8", errors="replace")
        records = self._parse_dividends_html(code, html)

        # 按除权日升序排列
        records.sort(key=lambda r: r.ex_date)
        return records

    def _parse_dividends_html(self, code: str, html: str) -> list[DividendRecord]:
        """解析分红拆分 HTML 页面。"""
        records: list[DividendRecord] = []

        if _HAS_SELECTOLAX:
            tree = _HTMLParser(html)
            # 分红表格
            for table in tree.css("table.w782, table.fhsp"):
                for tr in table.css("tbody tr"):
                    tds = tr.css("td")
                    if len(tds) < 4:
                        continue
                    # 典型列：年份、权益登记日、除息日、每份分红
                    # 或：年份、除息日、每份分红、分红发放日
                    cells = [td.text(strip=True) for td in tds]
                    record = self._parse_dividend_row(code, cells)
                    if record:
                        records.append(record)
        else:
            rows = _extract_table_rows_re(html)
            for row in rows:
                if len(row) < 3:
                    continue
                # 跳过表头
                if "权益登记日" in str(row) or "年份" in str(row):
                    continue
                record = self._parse_dividend_row(code, row)
                if record:
                    records.append(record)

        return records

    def _parse_dividend_row(
        self, code: str, cells: list[str]
    ) -> DividendRecord | None:
        """解析单行分红数据。"""
        if len(cells) < 3:
            return None

        # 尝试识别日期和分红金额
        ex_date: date | None = None
        record_date: date | None = None
        pay_date: date | None = None
        dividend_per_share = Decimal("0")
        split_ratio = Decimal("1")

        for cell in cells:
            cell = cell.strip()
            # 尝试解析日期
            d = _safe_date(cell)
            if d:
                if ex_date is None:
                    ex_date = d
                elif record_date is None:
                    record_date = d
                elif pay_date is None:
                    pay_date = d
                continue

            # 尝试解析分红金额（"每份派现0.05元" 或 "0.0500"）
            m = re.search(r"每份派现?\s*([\d.]+)\s*元?", cell)
            if m:
                try:
                    dividend_per_share = Decimal(m.group(1))
                except InvalidOperation:
                    pass
                continue

            # 纯数字可能是分红金额
            m = re.match(r"^([\d.]+)$", cell)
            if m and dividend_per_share == Decimal("0"):
                try:
                    val = Decimal(m.group(1))
                    if val < Decimal("10"):  # 分红金额通常小于 10
                        dividend_per_share = val
                except InvalidOperation:
                    pass

            # 拆分比例（"1:1.5" 或 "每份拆分为1.5份"）
            m = re.search(r"1\s*:\s*([\d.]+)", cell)
            if m:
                try:
                    split_ratio = Decimal(m.group(1))
                except InvalidOperation:
                    pass
            m = re.search(r"拆分为?\s*([\d.]+)\s*份", cell)
            if m:
                try:
                    split_ratio = Decimal(m.group(1))
                except InvalidOperation:
                    pass

        if ex_date is None:
            return None

        return DividendRecord(
            fund_code=code,
            ex_date=ex_date,
            record_date=record_date,
            pay_date=pay_date,
            dividend_per_share=dividend_per_share,
            split_ratio=split_ratio,
        )

    # ------------------------------------------------------------------
    # 7. 排名榜单（内部辅助）
    # ------------------------------------------------------------------

    async def fetch_fund_ranking(
        self,
        fund_type: str = "all",
        sort_by: str = "6yzf",  # 6 月涨幅
        page: int = 1,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        """从 fund.eastmoney.com/data/rankhandler.aspx 获取基金排名。

        Args:
            fund_type: 基金类型筛选（all/gp/hh/zq/zs/qdii/fof）。
            sort_by: 排序字段（6yzf=6月涨幅, 1nzf=1年涨幅, etc.）。
            page: 页码。
            page_size: 每页数量。

        Returns:
            基金排名数据列表。
        """
        # 基金类型映射
        type_map = {
            "all": "all",
            "stock": "gp",
            "mixed": "hh",
            "bond": "zq",
            "index": "zs",
            "qdii": "qdii",
            "fof": "fof",
        }
        ft = type_map.get(fund_type, fund_type)

        url = "http://fund.eastmoney.com/data/rankhandler.aspx"
        params = {
            "op": "ph",
            "dt": "kf",  # 开放式基金
            "ft": ft,
            "rs": "",
            "gs": "0",
            "sc": sort_by,
            "st": "desc",
            "sd": "",
            "ed": "",
            "qdii": "",
            "tabSubtype": ",,,,,",
            "pi": str(page),
            "pn": str(page_size),
            "dx": "1",
            "v": "0.123456",
        }
        resp = await self._get(
            url,
            params=params,
            referer="http://fund.eastmoney.com/data/fundranking.html",
        )
        raw = resp.content
        await self._save_snapshot("ranking", f"ranking_{ft}_{page}", "js", raw)

        text = raw.decode("utf-8", errors="replace")
        # JSONP 格式：var rankData = {datas:[...],allRecords:N,...};
        # 键名无引号，不是合法 JSON，直接用正则提取 datas 数组
        m = re.search(r"datas:\[(.*?)\]", text, re.DOTALL)
        if not m:
            return []

        try:
            # datas 内容是逗号分隔的带引号字符串: "xxx","yyy",...
            raw_items = re.findall(r'"([^"]*)"', m.group(1))
            results = []
            for item in raw_items:
                parts = item.split(",")
                if len(parts) >= 20:
                    results.append({
                        "code": parts[0],
                        "name": parts[1],
                        "unit_nav": _safe_decimal(parts[4]),
                        "accum_nav": _safe_decimal(parts[5]),
                        "daily_return": _safe_decimal(parts[6]),
                        "weekly_return": _safe_decimal(parts[7]),
                        "monthly_return": _safe_decimal(parts[8]),
                        "quarterly_return": _safe_decimal(parts[9]),
                        "half_year_return": _safe_decimal(parts[10]),
                        "yearly_return": _safe_decimal(parts[11]),
                    })
            return results
        except Exception as exc:
            logger.warning("排名数据解析失败: %s", exc)
            return []

    # ------------------------------------------------------------------
    # 8. 基金经理（内部辅助）
    # ------------------------------------------------------------------

    async def fetch_fund_manager(self, code: str) -> list[dict[str, Any]]:
        """从 fundf10.eastmoney.com/jjjl_{code}.html 获取基金经理信息。

        Args:
            code: 基金代码。

        Returns:
            基金经理信息列表（含历任经理）。
        """
        url = f"http://fundf10.eastmoney.com/jjjl_{code}.html"
        resp = await self._get(url)
        raw = resp.content
        await self._save_snapshot(code, "manager", "html", raw)

        html = raw.decode("utf-8", errors="replace")
        managers: list[dict[str, Any]] = []

        if _HAS_SELECTOLAX:
            tree = _HTMLParser(html)
            # 现任经理
            for div in tree.css(".jlinfo"):
                name_node = div.css_first(".name a")
                if name_node:
                    name = name_node.text(strip=True)
                    # 提取任职日期等信息
                    info_text = div.text(strip=True)
                    start_date = None
                    m = re.search(r"(\d{4}-\d{2}-\d{2})", info_text)
                    if m:
                        start_date = _safe_date(m.group(1))
                    managers.append({
                        "name": name,
                        "start_date": start_date,
                        "is_current": True,
                    })

            # 历任经理表格
            for table in tree.css("table.w782"):
                for tr in table.css("tbody tr"):
                    tds = tr.css("td")
                    if len(tds) >= 4:
                        name = tds[0].text(strip=True)
                        start_raw = tds[1].text(strip=True)
                        end_raw = tds[2].text(strip=True)
                        tenure_raw = tds[3].text(strip=True) if len(tds) > 3 else ""

                        start_date = _safe_date(start_raw)
                        end_date = _safe_date(end_raw)

                        # 解析任期天数
                        tenure_days = None
                        m = re.search(r"(\d+)\s*天", tenure_raw)
                        if m:
                            tenure_days = int(m.group(1))

                        managers.append({
                            "name": name,
                            "start_date": start_date,
                            "end_date": end_date,
                            "tenure_days": tenure_days,
                            "is_current": end_date is None or end_raw in ("至今", "--"),
                        })
        else:
            # 正则回退
            rows = _extract_table_rows_re(html)
            for row in rows:
                if len(row) >= 3 and "基金经理" not in row[0]:
                    name = row[0].strip()
                    start_date = _safe_date(row[1].strip()) if len(row) > 1 else None
                    end_date = _safe_date(row[2].strip()) if len(row) > 2 else None
                    managers.append({
                        "name": name,
                        "start_date": start_date,
                        "end_date": end_date,
                        "is_current": end_date is None,
                    })

        return managers

    # ------------------------------------------------------------------
    # 9. 公告 fetch_announcements
    # ------------------------------------------------------------------

    async def fetch_announcements(
        self,
        code: str,
        since: date,
    ) -> list[Announcement]:
        """从 api.fund.eastmoney.com/f10/JJGG 获取基金公告。

        Args:
            code: 基金代码。
            since: 起始日期（含），只返回该日期及之后的公告。

        Returns:
            按 publish_date 升序排列的 Announcement 列表。

        Raises:
            ProviderNotFoundError: 基金代码不存在。
            ProviderTimeoutError: 请求超时。
            ProviderError: 其他错误。
        """
        announcements: list[Announcement] = []
        page = 1
        page_size = 30

        while True:
            params = {
                "fundcode": code,
                "pageIndex": page,
                "pageSize": page_size,
                "type": "0",  # 0=全部公告
                "callback": "",
            }
            resp = await self._get(
                "http://api.fund.eastmoney.com/f10/JJGG",
                params=params,
                referer=f"http://fundf10.eastmoney.com/jjgg_{code}.html",
            )
            raw = resp.content
            if page == 1:
                await self._save_snapshot(code, "announcements", "json", raw)

            try:
                data = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception as exc:
                raise ProviderError(
                    f"公告 JSON 解析失败: {exc}",
                    provider_name=self.name,
                    fund_code=code,
                ) from exc

            items = data.get("Data", []) if isinstance(data.get("Data"), list) else []
            if not items:
                break

            found_older = False
            for item in items:
                publish_date = _safe_date(item.get("FBRQ"))
                if publish_date is None:
                    continue

                # 跳过早于 since 的公告
                if publish_date < since:
                    found_older = True
                    continue

                title = item.get("TITLE", "")
                content_url = item.get("ATTACHURL", "") or item.get("Url", "")

                announcements.append(
                    Announcement(
                        fund_code=code,
                        title=title,
                        category=None,  # 由 LLM 分类
                        publish_date=publish_date,
                        content_url=content_url,
                        parsed_data=None,
                        requires_review=False,
                    )
                )

            # 如果已经遇到早于 since 的公告，停止翻页
            if found_older:
                break

            # 判断是否还有更多页
            total_count = data.get("TotalCount", 0)
            if page * page_size >= total_count:
                break
            page += 1

        # 按发布日期升序排列
        announcements.sort(key=lambda a: a.publish_date or date.min)
        return announcements

    # ------------------------------------------------------------------
    # 健康检查 health_check
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthStatus:
        """探测天天基金接口是否可用。

        通过请求一个轻量级接口（实时估值）来检测连通性。

        Returns:
            HealthStatus 实例。
        """
        start_time = time.monotonic()
        try:
            # 使用一个常见基金代码测试
            test_code = "000001"
            url = f"http://fundgz.1234567.com.cn/js/{test_code}.js"
            await self._rate_limiter.acquire(self.name)
            headers = self._build_headers(referer="http://fund.eastmoney.com/")

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0),
                follow_redirects=True,
            ) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()

            latency_ms = (time.monotonic() - start_time) * 1000
            return HealthStatus(
                healthy=True,
                message="天天基金接口正常",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - start_time) * 1000
            return HealthStatus(
                healthy=False,
                message=f"天天基金接口异常: {exc}",
                latency_ms=latency_ms,
            )

    # ------------------------------------------------------------------
    # 10. 基金搜索（模糊匹配）
    # ------------------------------------------------------------------

    async def search_funds(self, keyword: str, limit: int = 20) -> list[dict[str, str]]:
        """通过天天基金搜索接口模糊匹配基金。

        使用 fundsuggest 接口，支持代码和名称模糊搜索，比排行榜匹配更全面。

        Args:
            keyword: 搜索关键词（代码或名称）。
            limit: 最大返回数量。

        Returns:
            [{"code": "000001", "name": "华夏成长", "fund_type": "混合型"}, ...]
        """
        url = "http://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
        params = {
            "m": "1",
            "key": keyword,
            "pageindex": "1",
            "pagesize": str(limit),
        }
        try:
            resp = await self._get(
                url,
                params=params,
                referer="http://fund.eastmoney.com/",
            )
            raw = resp.content
            text = raw.decode("utf-8", errors="replace").strip()

            # 响应可能是 JSONP 或纯 JSON
            if text.startswith("(") and text.endswith(")"):
                text = text[1:-1]
            elif not text.startswith("{"):
                m = re.search(r"\((\{.*\})\)\s*;?\s*$", text, re.DOTALL)
                if m:
                    text = m.group(1)

            data = json.loads(text)
            items = data.get("Datas", [])
            results: list[dict[str, str]] = []
            for item in items:
                if isinstance(item, dict):
                    results.append({
                        "code": item.get("CODE", ""),
                        "name": item.get("NAME", ""),
                        "fund_type": item.get("FundBaseInfo", {}).get("FTYPE", "") if isinstance(item.get("FundBaseInfo"), dict) else "",
                    })
            return results[:limit]
        except Exception as exc:
            logger.warning("基金搜索接口失败 keyword=%s: %s", keyword, exc)
            return []

