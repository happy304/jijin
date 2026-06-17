"""基准指数数据采集任务。

采集主流指数的日收益率数据，用于回测基准对比。
支持增量更新（从最后一条记录的日期开始采集）。

数据源：AkShare（stock_zh_index_daily_em）
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

log = get_logger(__name__)

# 默认采集的基准指数
DEFAULT_BENCHMARKS = {
    "000300": "沪深300",
    "000905": "中证500",
    "000016": "上证50",
}


def _run_async(coro):
    """在新事件循环中运行异步函数。"""
    from app.tasks.async_utils import run_async
    return run_async(coro)


@celery_app.task(
    name="app.tasks.ingest_benchmark.update_benchmark_nav",
    queue="ingest",
    bind=True,
    max_retries=2,
    soft_time_limit=10 * 60,
    time_limit=15 * 60,
)
def update_benchmark_nav(
    self,
    index_codes: list[str] | None = None,
) -> dict[str, Any]:
    """采集基准指数日净值数据。

    Args:
        index_codes: 要采集的指数代码列表，默认采集所有预定义基准。

    Returns:
        采集结果摘要。
    """
    return _run_async(_update_benchmark_nav_async(index_codes))


async def _update_benchmark_nav_async(
    index_codes: list[str] | None = None,
) -> dict[str, Any]:
    """异步实现基准数据采集。"""
    from sqlalchemy import select, func as sa_func

    from app.data.models.benchmark import BenchmarkNav, BENCHMARK_INDICES
    from app.data.session import get_engine, get_sessionmaker

    get_engine()
    session_factory = get_sessionmaker()

    codes = index_codes or list(DEFAULT_BENCHMARKS.keys())
    today = date.today()

    success = 0
    failed = 0
    total_records = 0

    for code in codes:
        try:
            # 查询最后一条记录的日期
            async with session_factory() as session:
                result = await session.execute(
                    select(sa_func.max(BenchmarkNav.trade_date)).where(
                        BenchmarkNav.index_code == code
                    )
                )
                last_date = result.scalar_one_or_none()

            if last_date:
                start = last_date + timedelta(days=1)
            else:
                start = today - timedelta(days=365 * 3)  # 默认采集3年

            if start > today:
                log.debug("ingest_benchmark.up_to_date", index_code=code)
                success += 1
                continue

            # 使用 akshare 获取指数数据
            records = await _fetch_index_data(code, start, today)

            if not records:
                success += 1
                continue

            # 批量写入
            async with session_factory() as session:
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                for batch_start in range(0, len(records), 500):
                    batch = records[batch_start:batch_start + 500]
                    stmt = pg_insert(BenchmarkNav).values(batch)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["index_code", "trade_date"],
                        set_={
                            "close": stmt.excluded.close,
                            "daily_return": stmt.excluded.daily_return,
                            "index_name": stmt.excluded.index_name,
                            "source": stmt.excluded.source,
                        },
                    )
                    await session.execute(stmt)
                await session.commit()

            total_records += len(records)
            success += 1
            log.info(
                "ingest_benchmark.ok",
                index_code=code,
                records=len(records),
            )

        except Exception as exc:
            failed += 1
            log.error(
                "ingest_benchmark.failed",
                index_code=code,
                error=str(exc),
            )

    return {
        "success": success,
        "failed": failed,
        "total_records": total_records,
    }


async def _fetch_index_data(
    index_code: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    """通过 akshare 获取指数日线数据。

    Args:
        index_code: 指数代码
        start: 起始日期
        end: 结束日期

    Returns:
        记录列表，每条包含 index_code, trade_date, close, daily_return 等字段
    """
    import concurrent.futures

    try:
        import akshare as ak
        import pandas as pd
    except ImportError:
        log.warning("ingest_benchmark: akshare 未安装，跳过基准数据采集")
        return []

    loop = asyncio.get_event_loop()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def _fetch():
        """同步获取数据。"""
        try:
            df = ak.stock_zh_index_daily_em(
                symbol=index_code,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
            return df
        except Exception as e:
            log.warning("akshare index fetch failed: %s", str(e))
            return None

    df = await loop.run_in_executor(executor, _fetch)

    if df is None or df.empty:
        return []

    index_name = DEFAULT_BENCHMARKS.get(index_code, index_code)
    records: list[dict[str, Any]] = []

    # 确保按日期排序
    if "date" in df.columns:
        df = df.sort_values("date").reset_index(drop=True)
        date_col = "date"
    elif "日期" in df.columns:
        df = df.sort_values("日期").reset_index(drop=True)
        date_col = "日期"
    else:
        return []

    # 确定收盘价列
    if "close" in df.columns:
        close_col = "close"
    elif "收盘" in df.columns:
        close_col = "收盘"
    else:
        return []

    prev_close = None
    for _, row in df.iterrows():
        try:
            trade_date_val = row[date_col]
            if isinstance(trade_date_val, str):
                trade_date_val = date.fromisoformat(trade_date_val)
            elif hasattr(trade_date_val, "date"):
                trade_date_val = trade_date_val.date()

            close_val = Decimal(str(row[close_col]))

            # 计算日收益率
            daily_return = None
            if prev_close and prev_close > 0:
                daily_return = (close_val - prev_close) / prev_close

            prev_close = close_val

            records.append({
                "index_code": index_code,
                "trade_date": trade_date_val,
                "close": close_val,
                "daily_return": daily_return,
                "index_name": index_name,
                "source": "akshare",
            })
        except (ValueError, InvalidOperation, TypeError):
            continue

    return records
