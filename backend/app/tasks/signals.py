"""Strategy signal generation Celery task.

Generates trading signals for all subscribed/active strategies after daily
data updates complete. This task is scheduled at 22:00 (Asia/Shanghai)
by Celery Beat, one hour after the data ingestion window.

The task:
1. Loads all strategies marked as active from the database
2. For each strategy, instantiates the strategy class and runs on_bar
3. Stores generated signals in the signals table
4. Triggers notification dispatch for new signals
5. Returns summary of strategies processed and signals generated

Requirements: 8.2
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, create_engine
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


def _get_today() -> date:
    """获取当前日期（可在测试中 mock）。"""
    return date.today()


def _load_active_strategies() -> list[dict[str, Any]]:
    """从数据库加载所有活跃/订阅状态的策略。

    Returns:
        策略记录列表，每条包含 id, name, strategy_type, params, universe
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.data.models.strategies import Strategy

    settings = get_settings()
    engine = create_engine(settings.database_sync_url)

    strategies = []
    try:
        with Session(engine) as session:
            # 查询所有策略（当前没有 status 字段，加载全部）
            rows = session.query(Strategy).all()
            for row in rows:
                strategies.append({
                    "id": row.id,
                    "name": row.name,
                    "strategy_type": row.strategy_type,
                    "params": row.params,
                    "universe": row.universe,
                    "benchmark": row.benchmark,
                })
    finally:
        engine.dispose()

    return strategies


def _generate_signals_for_strategy(
    strategy_config: dict[str, Any],
    signal_date: date,
) -> list[dict[str, Any]]:
    """为单个策略生成信号。

    实例化策略类，构建简化的 BarContext，调用 on_bar 获取 OrderIntent，
    将 OrderIntent 转换为信号记录。

    Args:
        strategy_config: 策略配置字典
        signal_date: 信号生成日期

    Returns:
        信号记录列表
    """
    from app.domain.strategy.base import create_strategy_from_config

    strategy_id = strategy_config["id"]
    strategy_name = strategy_config["name"]
    strategy_type = strategy_config["strategy_type"]
    params = strategy_config["params"]
    universe = strategy_config["universe"]

    signals: list[dict[str, Any]] = []

    try:
        # 实例化策略
        strategy = create_strategy_from_config(
            strategy_type=strategy_type,
            params=params,
            universe=universe,
        )

        # 构建简化的 BarContext 用于信号生成
        context = _build_signal_context(strategy, signal_date)
        if context is None:
            log.warning(
                "signals.no_context",
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                reason="无法构建策略上下文（可能缺少净值数据）",
            )
            return signals

        # 调用策略 on_bar
        order_intents = strategy.on_bar(context)

        # 将 OrderIntent 转换为信号
        for intent in order_intents:
            signal = {
                "strategy_id": strategy_id,
                "strategy_name": strategy_name,
                "fund_code": intent.fund_code,
                "signal_date": signal_date,
                "direction": intent.direction,
                "strength": None,
                "target_weight": (
                    float(intent.target_weight)
                    if hasattr(intent, "target_weight") and intent.target_weight is not None
                    else None
                ),
                "amount": (
                    float(intent.amount)
                    if intent.amount is not None
                    else None
                ),
                "shares": (
                    float(intent.shares)
                    if intent.shares is not None
                    else None
                ),
                "reason": f"策略 {strategy_name} 生成的 {intent.direction} 信号",
                "metadata_json": {
                    "strategy_type": strategy_type,
                    "params": params,
                },
                "notified": False,
            }
            signals.append(signal)

        log.info(
            "signals.strategy_processed",
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            signals_count=len(signals),
        )

    except Exception as e:
        log.error(
            "signals.strategy_error",
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            error=str(e),
            exc_info=True,
        )

    return signals


def _build_signal_context(strategy: Any, signal_date: date) -> Any:
    """构建用于信号生成的简化 BarContext。

    从数据库加载策略 universe 中基金的近期净值数据，
    构建一个空持仓的 BarContext 供策略决策。

    注意：实盘信号生成时，应使用用户的实际持仓。
    当前简化实现使用空持仓 + 虚拟初始资金。

    Args:
        strategy: 策略实例
        signal_date: 信号日期

    Returns:
        BarContext 实例，如果无法构建返回 None
    """
    from app.domain.backtest.engine_event import BarContext
    from app.domain.backtest.calendar import prev_trading_day
    from app.domain.backtest.portfolio import Portfolio

    # 获取截止日期（T-1）
    cutoff_date = prev_trading_day(signal_date)

    # 加载净值数据
    nav_data = _load_nav_data_for_universe(strategy.universe, cutoff_date)

    if not nav_data:
        return None

    # 构建空持仓的 Portfolio（信号生成模式）
    portfolio = Portfolio(cash=Decimal("1000000"))

    return BarContext(
        current_date=signal_date,
        portfolio=portfolio,
        nav_history=nav_data,
        _cutoff_date=cutoff_date,
    )


def _load_nav_data_for_universe(
    universe: list[str],
    cutoff_date: date,
    lookback_days: int = 365,
) -> dict[str, dict[date, Decimal]]:
    """从数据库加载基金池的净值数据。

    Args:
        universe: 基金代码列表
        cutoff_date: 数据截止日期
        lookback_days: 回溯天数

    Returns:
        {fund_code: {date: nav}} 格式的净值数据
    """
    from datetime import timedelta

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.core.config import get_settings

    if not universe:
        return {}

    settings = get_settings()
    start_date = cutoff_date - timedelta(days=lookback_days)

    nav_data: dict[str, dict[date, Decimal]] = {}
    engine = create_engine(settings.database_sync_url)

    try:
        with Session(engine) as session:
            # 使用原生 SQL 查询净值数据
            placeholders = ", ".join([f":code_{i}" for i in range(len(universe))])
            query = text(
                f"SELECT fund_code, trade_date, adj_nav "
                f"FROM fund_nav "
                f"WHERE fund_code IN ({placeholders}) "
                f"AND trade_date BETWEEN :start_date AND :end_date "
                f"AND adj_nav IS NOT NULL "
                f"ORDER BY fund_code, trade_date"
            )

            params: dict[str, Any] = {
                f"code_{i}": code for i, code in enumerate(universe)
            }
            params["start_date"] = start_date
            params["end_date"] = cutoff_date

            result = session.execute(query, params)
            for row in result:
                fund_code = row[0]
                trade_date = row[1]
                adj_nav = Decimal(str(row[2]))

                if fund_code not in nav_data:
                    nav_data[fund_code] = {}
                nav_data[fund_code][trade_date] = adj_nav
    except Exception as e:
        log.warning(
            "signals.nav_load_error",
            error=str(e),
            universe_size=len(universe),
        )
    finally:
        engine.dispose()

    return nav_data


def _signal_key(signal_data: dict[str, Any]) -> tuple[int, str, date, str]:
    """生成信号幂等键。"""
    return (
        int(signal_data["strategy_id"]),
        str(signal_data["fund_code"]),
        signal_data["signal_date"],
        str(signal_data["direction"]),
    )


def _deduplicate_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批内去重：同一策略/基金/日期/方向仅保留一条。"""
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[int, str, date, str]] = set()
    for signal_data in signals:
        key = _signal_key(signal_data)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(signal_data)
    return deduped


def _filter_existing_signals(session: Session, signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """过滤数据库中已存在的信号。"""
    from app.data.models.signals import Signal

    pending = _deduplicate_signals(signals)
    if not pending:
        return []

    fresh: list[dict[str, Any]] = []
    for signal_data in pending:
        exists = session.query(Signal.id).filter(
            and_(
                Signal.strategy_id == signal_data["strategy_id"],
                Signal.fund_code == signal_data["fund_code"],
                Signal.signal_date == signal_data["signal_date"],
                Signal.direction == signal_data["direction"],
            )
        ).first()
        if exists is None:
            fresh.append(signal_data)
    return fresh


def _store_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将信号存入 signals 表。

    Args:
        signals: 信号记录列表

    Returns:
        实际成功落库的新信号列表
    """
    from app.core.config import get_settings
    from app.data.models.signals import Signal

    if not signals:
        return []

    settings = get_settings()
    engine = create_engine(settings.database_sync_url)
    stored_signals: list[dict[str, Any]] = []

    try:
        with Session(engine) as session:
            fresh_signals = _filter_existing_signals(session, signals)
            for signal_data in fresh_signals:
                signal = Signal(
                    strategy_id=signal_data["strategy_id"],
                    strategy_name=signal_data["strategy_name"],
                    fund_code=signal_data["fund_code"],
                    signal_date=signal_data["signal_date"],
                    direction=signal_data["direction"],
                    strength=signal_data.get("strength"),
                    target_weight=signal_data.get("target_weight"),
                    amount=signal_data.get("amount"),
                    shares=signal_data.get("shares"),
                    reason=signal_data.get("reason"),
                    metadata_json=signal_data.get("metadata_json"),
                    notified=signal_data.get("notified", False),
                )
                session.add(signal)
                stored_signals.append(signal_data)

            session.commit()
    except Exception as e:
        log.error("signals.store_error", error=str(e), exc_info=True)
        stored_signals = []
    finally:
        engine.dispose()

    return stored_signals


def _send_notifications(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """调用通知模块推送信号。

    Args:
        signals: 信号记录列表

    Returns:
        通知发送结果摘要
    """
    from app.notify.service import NotificationResult, SignalNotification, send_signal_notifications

    if not signals:
        return {"total": 0, "sent": 0, "failed": 0}

    notifications = []
    for signal_data in signals:
        notifications.append(
            SignalNotification(
                strategy_id=signal_data["strategy_id"],
                strategy_name=signal_data["strategy_name"],
                fund_code=signal_data["fund_code"],
                direction=signal_data["direction"],
                signal_date=str(signal_data["signal_date"]),
                strength=signal_data.get("strength"),
                target_weight=signal_data.get("target_weight"),
                reason=signal_data.get("reason"),
            )
        )

    result = send_signal_notifications(notifications)

    return {
        "total": result.total,
        "sent": result.sent,
        "failed": result.failed,
        "channels_used": result.channels_used,
    }


@celery_app.task(
    name="app.tasks.signals.generate_strategy_signals",
    queue="backtest",
    bind=True,
    max_retries=2,
    soft_time_limit=20 * 60,
    time_limit=25 * 60,
)
def generate_strategy_signals(self) -> dict[str, Any]:
    """Generate trading signals for all active/subscribed strategies.

    This task:
    1. Loads all strategies marked as 'subscribed' or 'active'
    2. For each strategy, runs the signal generation logic
    3. Stores generated signals in the signals table
    4. Triggers notification dispatch for new signals

    Returns
    -------
    dict
        Summary with counts of strategies processed and signals generated.
    """
    log.info("signals.generate_strategy_signals.start")

    signal_date = _get_today()
    strategies_processed = 0
    strategies_failed = 0
    all_signals: list[dict[str, Any]] = []

    # 1. 加载所有活跃策略
    try:
        strategies = _load_active_strategies()
    except Exception as e:
        log.error("signals.load_strategies_error", error=str(e), exc_info=True)
        return {
            "status": "error",
            "error": f"加载策略失败: {str(e)}",
            "strategies_processed": 0,
            "signals_generated": 0,
        }

    log.info("signals.strategies_loaded", count=len(strategies))

    if not strategies:
        result = {
            "status": "success",
            "strategies_processed": 0,
            "signals_generated": 0,
            "message": "无活跃策略需要处理",
        }
        log.info("signals.generate_strategy_signals.complete", **result)
        return result

    # 2. 为每个策略生成信号
    for strategy_config in strategies:
        try:
            signals = _generate_signals_for_strategy(strategy_config, signal_date)
            all_signals.extend(signals)
            strategies_processed += 1
        except Exception as e:
            strategies_failed += 1
            log.error(
                "signals.strategy_generation_error",
                strategy_id=strategy_config["id"],
                strategy_name=strategy_config["name"],
                error=str(e),
                exc_info=True,
            )

    generated_count = len(all_signals)
    deduped_signals = _deduplicate_signals(all_signals)

    # 3. 存入 signals 表
    stored_signals: list[dict[str, Any]] = []
    if deduped_signals:
        try:
            stored_signals = _store_signals(deduped_signals)
        except Exception as e:
            log.error("signals.store_batch_error", error=str(e), exc_info=True)

    # 4. 调用通知模块推送：仅对成功落库的新信号通知
    notification_result: dict[str, Any] = {"total": 0, "sent": 0, "failed": 0}
    if stored_signals:
        try:
            notification_result = _send_notifications(stored_signals)
        except Exception as e:
            log.error("signals.notification_error", error=str(e), exc_info=True)

    # 5. 返回摘要
    result = {
        "status": "success",
        "signal_date": str(signal_date),
        "strategies_processed": strategies_processed,
        "strategies_failed": strategies_failed,
        "signals_generated": generated_count,
        "signals_deduplicated": len(deduped_signals),
        "signals_stored": len(stored_signals),
        "notification": notification_result,
    }

    # 标记任务完成（供 chain_guard 检测）
    try:
        from app.tasks.chain_guard import mark_task_done
        mark_task_done("daily-strategy-signals")
    except Exception:
        pass

    log.info("signals.generate_strategy_signals.complete", **result)
    return result


__all__ = ["generate_strategy_signals"]
