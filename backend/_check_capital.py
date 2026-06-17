"""Check strategy vs backtest initial capital."""
import asyncio
from app.data.session import get_engine, get_sessionmaker
from app.data.models.backtests import BacktestRun
from app.data.models.strategies import Strategy
from sqlalchemy import select


async def main():
    get_engine()
    factory = get_sessionmaker()
    async with factory() as session:
        result = await session.execute(
            select(
                BacktestRun.id,
                BacktestRun.strategy_id,
                BacktestRun.initial_capital,
                Strategy.name,
                Strategy.params,
            )
            .join(Strategy, BacktestRun.strategy_id == Strategy.id, isouter=True)
            .order_by(BacktestRun.id.desc())
            .limit(5)
        )
        rows = result.all()
        for row in rows:
            strategy_capital = None
            if row.params and isinstance(row.params, dict):
                strategy_capital = row.params.get("initial_capital") or row.params.get("invest_amount")
            print(
                f"Run {row.id}: strategy='{row.name}', "
                f"backtest_capital={row.initial_capital}, "
                f"strategy_params_capital={strategy_capital}, "
                f"params_keys={list(row.params.keys()) if row.params else []}"
            )


asyncio.run(main())
