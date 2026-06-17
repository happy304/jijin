"""指数估值数据采集任务。

每日收盘后采集主流指数的 PE/PB/股息率数据，
存入 index_valuation 表，供估值分析服务使用。

采集频率：每日 18:00（收盘后）
数据来源：AkShare（封装中证指数公司数据）
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(
    name="app.tasks.valuation_ingest.ingest_index_valuation",
    queue="ingest",
    bind=True,
    max_retries=3,
    soft_time_limit=10 * 60,
    time_limit=15 * 60,
)
def ingest_index_valuation(self) -> dict[str, Any]:
    """采集指数估值数据。"""
    from app.tasks.async_utils import run_async
    return run_async(_ingest_async())


async def _ingest_async() -> dict[str, Any]:
    """异步执行估值数据采集。"""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.data.providers.index_valuation_provider import (
        INDEX_CODE_MAP,
        IndexValuationProvider,
        compute_percentile,
    )
    from app.data.session import get_engine, get_sessionmaker

    get_engine()
    session_factory = get_sessionmaker()

    provider = IndexValuationProvider()
    results = {"success": 0, "failed": 0, "skipped": 0, "errors": []}

    async with session_factory() as session:
        for index_code, index_name in INDEX_CODE_MAP.items():
            try:
                log.info("valuation_ingest.fetching", index=index_code, name=index_name)

                # 获取最近30天数据（增量更新）
                records = await provider.fetch_valuation_history(
                    index_code,
                    start_date=date.today() - timedelta(days=30),
                    end_date=date.today(),
                )

                if not records:
                    results["skipped"] += 1
                    continue

                # 加载历史 PE 数据用于计算百分位
                hist_query = text(
                    "SELECT pe_ttm FROM index_valuation "
                    "WHERE index_code = :code AND pe_ttm IS NOT NULL "
                    "ORDER BY trade_date"
                )
                hist_result = await session.execute(hist_query, {"code": index_code})
                hist_pe_values = [float(r[0]) for r in hist_result]

                hist_pb_query = text(
                    "SELECT pb FROM index_valuation "
                    "WHERE index_code = :code AND pb IS NOT NULL "
                    "ORDER BY trade_date"
                )
                hist_pb_result = await session.execute(hist_pb_query, {"code": index_code})
                hist_pb_values = [float(r[0]) for r in hist_pb_result]

                # 插入/更新记录
                for rec in records:
                    pe_pct = None
                    pb_pct = None
                    if rec.pe_ttm is not None and hist_pe_values:
                        pe_pct = compute_percentile(hist_pe_values, rec.pe_ttm)
                    if rec.pb is not None and hist_pb_values:
                        pb_pct = compute_percentile(hist_pb_values, rec.pb)

                    # UPSERT
                    upsert_sql = text("""
                        INSERT INTO index_valuation
                            (index_code, trade_date, pe_ttm, pe_percentile,
                             pb, pb_percentile, dividend_yield, roe,
                             index_name, source)
                        VALUES
                            (:index_code, :trade_date, :pe_ttm, :pe_percentile,
                             :pb, :pb_percentile, :dividend_yield, :roe,
                             :index_name, :source)
                        ON CONFLICT (index_code, trade_date)
                        DO UPDATE SET
                            pe_ttm = EXCLUDED.pe_ttm,
                            pe_percentile = EXCLUDED.pe_percentile,
                            pb = EXCLUDED.pb,
                            pb_percentile = EXCLUDED.pb_percentile,
                            dividend_yield = EXCLUDED.dividend_yield,
                            roe = EXCLUDED.roe
                    """)

                    await session.execute(upsert_sql, {
                        "index_code": index_code,
                        "trade_date": rec.trade_date,
                        "pe_ttm": rec.pe_ttm,
                        "pe_percentile": pe_pct,
                        "pb": rec.pb,
                        "pb_percentile": pb_pct,
                        "dividend_yield": rec.dividend_yield,
                        "roe": rec.roe,
                        "index_name": index_name,
                        "source": "akshare",
                    })

                await session.commit()
                results["success"] += 1
                log.info(
                    "valuation_ingest.success",
                    index=index_code,
                    records=len(records),
                )

            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"{index_code}: {str(e)[:100]}")
                log.warning(
                    "valuation_ingest.failed",
                    index=index_code,
                    error=str(e),
                )
                await session.rollback()

    log.info("valuation_ingest.complete", **results)
    return results


__all__ = ["ingest_index_valuation"]
