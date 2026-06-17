"""股票行业分类映射服务。

通过东方财富股票接口获取申万行业分类，并缓存到 Redis。
用于补全基金持仓数据中缺失的行业字段。

缓存策略：
- 单只股票行业信息 TTL = 7 天（行业分类极少变化）
- 批量查询时先查缓存，缓存未命中的再请求接口
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

# Redis key 前缀和 TTL
INDUSTRY_KEY_PREFIX = "stock:industry"
INDUSTRY_TTL_SECONDS = 7 * 24 * 3600  # 7 天


def _industry_key(stock_code: str) -> str:
    return f"{INDUSTRY_KEY_PREFIX}:{stock_code}"


async def get_stock_industries(stock_codes: list[str]) -> dict[str, str | None]:
    """批量获取股票的行业分类。

    优先从 Redis 缓存读取，缓存未命中的通过东方财富接口查询。

    Args:
        stock_codes: 股票代码列表（如 ["600519", "000858"]）

    Returns:
        {stock_code: industry_name} 映射，未查到的值为 None
    """
    if not stock_codes:
        return {}

    result: dict[str, str | None] = {}
    uncached: list[str] = []

    # 1. 先查 Redis 缓存
    try:
        from app.data.cache import get_redis

        client = await get_redis()
        for code in stock_codes:
            cached = await client.get(_industry_key(code))
            if cached is not None:
                result[code] = cached if cached != "__NONE__" else None
            else:
                uncached.append(code)
    except Exception as exc:
        log.warning("stock_industry.cache_read_error", error=str(exc))
        uncached = stock_codes

    # 2. 缓存未命中的，通过接口查询
    if uncached:
        fetched = await _fetch_industries_from_eastmoney(uncached)
        result.update(fetched)

        # 3. 写入缓存
        try:
            from app.data.cache import get_redis

            client = await get_redis()
            for code in uncached:
                value = fetched.get(code)
                cache_value = value if value else "__NONE__"
                await client.set(_industry_key(code), cache_value, ex=INDUSTRY_TTL_SECONDS)
        except Exception as exc:
            log.warning("stock_industry.cache_write_error", error=str(exc))

    return result


async def _fetch_industries_from_eastmoney(stock_codes: list[str]) -> dict[str, str | None]:
    """从东方财富批量获取股票行业分类。

    使用东方财富的股票信息接口，每次最多查询一只股票。
    为避免请求过多，限制并发。
    """
    import asyncio

    from app.data.fetchers.http_client import AsyncHttpClient

    result: dict[str, str | None] = {}
    client = AsyncHttpClient(referer="https://quote.eastmoney.com/", http2=False)

    # 限制并发数
    semaphore = asyncio.Semaphore(5)

    async def _fetch_one(code: str) -> None:
        async with semaphore:
            industry = await _fetch_single_stock_industry(client, code)
            result[code] = industry

    tasks = [_fetch_one(code) for code in stock_codes]
    await asyncio.gather(*tasks, return_exceptions=True)

    await client.aclose()
    return result


async def _fetch_single_stock_industry(
    client: Any, stock_code: str
) -> str | None:
    """从东方财富获取单只股票的行业分类。

    接口：https://push2.eastmoney.com/api/qt/stock/get
    返回数据中 f127 字段为申万行业分类。
    """
    # 判断市场前缀：6开头为上海(1.)，其他为深圳(0.)
    if stock_code.startswith("6"):
        secid = f"1.{stock_code}"
    else:
        secid = f"0.{stock_code}"

    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": secid,
        "fields": "f127",  # f127 = 申万行业
        "ut": "fa5fd1943c7b386f172d6893dbbd1d0c",
    }

    try:
        resp = await client.get(
            url,
            params=params,
            headers={"Referer": "https://quote.eastmoney.com/"},
        )
        if resp.status_code != 200:
            log.debug(
                "stock_industry.http_error",
                stock_code=stock_code,
                status=resp.status_code,
            )
            return None
        data = resp.json()
        if isinstance(data, dict) and "data" in data:
            stock_data = data["data"]
            if isinstance(stock_data, dict):
                industry = stock_data.get("f127")
                if industry and industry != "-":
                    return str(industry)
    except Exception as exc:
        log.debug("stock_industry.fetch_error", stock_code=stock_code, error=str(exc))

    return None


async def enrich_holdings_with_industry(
    positions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """为持仓列表补全行业分类信息。

    对于 industry 为 None 的持仓项，尝试通过股票代码查询行业。
    同时更新数据库中的行业字段。

    Args:
        positions: 持仓列表，每项包含 stock_code, industry 等字段

    Returns:
        补全行业后的持仓列表（原地修改并返回）
    """
    # 找出缺少行业的股票代码
    missing_codes = [
        p["stock_code"]
        for p in positions
        if not p.get("industry") and p.get("stock_code")
    ]

    if not missing_codes:
        return positions

    # 批量查询行业
    industry_map = await get_stock_industries(missing_codes)

    # 补全
    for p in positions:
        if not p.get("industry") and p.get("stock_code"):
            industry = industry_map.get(p["stock_code"])
            if industry:
                p["industry"] = industry

    return positions
