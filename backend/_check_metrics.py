"""Quick script to check backtest metrics in DB."""
import asyncio
from app.data.session import get_engine, get_sessionmaker
from app.data.models.backtests import BacktestRun
from sqlalchemy import select


async def main():
    get_engine()
    factory = get_sessionmaker()
    async with factory() as session:
        result = await session.execute(
            select(BacktestRun.id, BacktestRun.status, BacktestRun.metrics)
            .order_by(BacktestRun.id.desc())
            .limit(5)
        )
        rows = result.all()
        for row in rows:
            metrics = row.metrics
            if metrics:
                sharpe = metrics.get("sharpe")
                inference = metrics.get("sharpe_inference")
                print(f"Run {row.id}: status={row.status}, sharpe={sharpe}, has_inference={inference is not None}")
                print(f"  metrics keys: {list(metrics.keys())[:15]}")
            else:
                print(f"Run {row.id}: status={row.status}, metrics=None")


asyncio.run(main())
