"""回测事件类型与事件总线单元测试。

覆盖：
- 所有事件类型的创建与序列化
- EventBus 的订阅、分发、排序机制
- 缓冲队列模式（enqueue + flush）
- 边界条件（无订阅者、多处理器、相同时间戳排序稳定性）
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from app.domain.backtest.events import (
    ConfirmEvent,
    DividendEvent,
    Event,
    EventBus,
    MarketCloseEvent,
    MarketOpenEvent,
    NavUpdateEvent,
    OrderEvent,
    RiskEvent,
)


# ---------------------------------------------------------------------------
# 事件模型测试
# ---------------------------------------------------------------------------


class TestEventModels:
    """事件 Pydantic 模型测试。"""

    def test_base_event_auto_event_type(self) -> None:
        """基类事件应自动设置 event_type 为类名。"""
        event = Event(timestamp=datetime(2024, 1, 2, 9, 30))
        assert event.event_type == "Event"

    def test_market_open_event(self) -> None:
        """MarketOpenEvent 创建与字段验证。"""
        ts = datetime(2024, 1, 2, 9, 30)
        event = MarketOpenEvent(timestamp=ts)
        assert event.timestamp == ts
        assert event.event_type == "MarketOpenEvent"

    def test_market_close_event(self) -> None:
        """MarketCloseEvent 创建与字段验证。"""
        ts = datetime(2024, 1, 2, 15, 0)
        event = MarketCloseEvent(timestamp=ts)
        assert event.timestamp == ts
        assert event.event_type == "MarketCloseEvent"

    def test_nav_update_event(self) -> None:
        """NavUpdateEvent 创建与字段验证。"""
        event = NavUpdateEvent(
            timestamp=datetime(2024, 1, 2, 20, 0),
            fund_code="000001",
            nav=Decimal("1.5432"),
        )
        assert event.fund_code == "000001"
        assert event.nav == Decimal("1.5432")
        assert event.event_type == "NavUpdateEvent"

    def test_order_event_subscribe(self) -> None:
        """OrderEvent 申购场景。"""
        event = OrderEvent(
            timestamp=datetime(2024, 1, 2, 10, 0),
            fund_code="110011",
            direction="subscribe",
            amount=Decimal("10000.00"),
            shares=None,
        )
        assert event.direction == "subscribe"
        assert event.amount == Decimal("10000.00")
        assert event.shares is None
        assert event.event_type == "OrderEvent"

    def test_order_event_redeem(self) -> None:
        """OrderEvent 赎回场景。"""
        event = OrderEvent(
            timestamp=datetime(2024, 1, 2, 10, 0),
            fund_code="110011",
            direction="redeem",
            amount=None,
            shares=Decimal("5000.00"),
        )
        assert event.direction == "redeem"
        assert event.amount is None
        assert event.shares == Decimal("5000.00")

    def test_confirm_event(self) -> None:
        """ConfirmEvent 创建与字段验证。"""
        event = ConfirmEvent(
            timestamp=datetime(2024, 1, 3, 15, 0),
            order_id="ORD-20240102-001",
            confirmed_shares=Decimal("6543.21"),
            confirmed_amount=Decimal("10000.00"),
            fee=Decimal("15.00"),
        )
        assert event.order_id == "ORD-20240102-001"
        assert event.confirmed_shares == Decimal("6543.21")
        assert event.confirmed_amount == Decimal("10000.00")
        assert event.fee == Decimal("15.00")
        assert event.event_type == "ConfirmEvent"

    def test_dividend_event_cash(self) -> None:
        """DividendEvent 现金分红。"""
        event = DividendEvent(
            timestamp=datetime(2024, 3, 15, 0, 0),
            fund_code="000001",
            dividend_per_share=Decimal("0.05"),
            reinvest=False,
        )
        assert event.fund_code == "000001"
        assert event.dividend_per_share == Decimal("0.05")
        assert event.reinvest is False
        assert event.event_type == "DividendEvent"

    def test_dividend_event_reinvest(self) -> None:
        """DividendEvent 红利再投。"""
        event = DividendEvent(
            timestamp=datetime(2024, 3, 15, 0, 0),
            fund_code="000001",
            dividend_per_share=Decimal("0.05"),
            reinvest=True,
        )
        assert event.reinvest is True

    def test_risk_event(self) -> None:
        """RiskEvent 创建与字段验证。"""
        event = RiskEvent(
            timestamp=datetime(2024, 1, 15, 14, 30),
            reason="最大回撤超过 20% 阈值",
        )
        assert event.reason == "最大回撤超过 20% 阈值"
        assert event.event_type == "RiskEvent"

    def test_event_is_frozen(self) -> None:
        """事件模型应为不可变（frozen）。"""
        event = MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30))
        with pytest.raises(Exception):  # ValidationError for frozen model
            event.timestamp = datetime(2024, 1, 3, 9, 30)  # type: ignore[misc]

    def test_event_serialization(self) -> None:
        """事件应支持 Pydantic 序列化。"""
        event = NavUpdateEvent(
            timestamp=datetime(2024, 1, 2, 20, 0),
            fund_code="000001",
            nav=Decimal("1.5432"),
        )
        data = event.model_dump()
        assert data["fund_code"] == "000001"
        assert data["nav"] == Decimal("1.5432")
        assert data["event_type"] == "NavUpdateEvent"

    def test_event_from_dict(self) -> None:
        """事件应支持从字典创建。"""
        data = {
            "timestamp": datetime(2024, 1, 2, 20, 0),
            "fund_code": "000001",
            "nav": Decimal("1.5432"),
        }
        event = NavUpdateEvent(**data)
        assert event.fund_code == "000001"
        assert event.nav == Decimal("1.5432")

    def test_order_event_invalid_direction(self) -> None:
        """OrderEvent 方向字段只接受 subscribe/redeem。"""
        with pytest.raises(Exception):  # ValidationError
            OrderEvent(
                timestamp=datetime(2024, 1, 2, 10, 0),
                fund_code="110011",
                direction="invalid",  # type: ignore[arg-type]
                amount=Decimal("10000"),
            )


# ---------------------------------------------------------------------------
# 事件总线测试
# ---------------------------------------------------------------------------


class TestEventBus:
    """EventBus 事件总线测试。"""

    def test_subscribe_and_emit(self) -> None:
        """基本订阅与分发。"""
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(MarketOpenEvent, received.append)

        event = MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30))
        bus.emit(event)

        assert len(received) == 1
        assert received[0] is event

    def test_emit_without_subscribers(self) -> None:
        """无订阅者时 emit 不应报错。"""
        bus = EventBus()
        event = MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30))
        # 不应抛出异常
        bus.emit(event)

    def test_multiple_handlers_called_in_order(self) -> None:
        """多个处理器应按订阅顺序调用。"""
        bus = EventBus()
        call_order: list[int] = []

        bus.subscribe(MarketOpenEvent, lambda _: call_order.append(1))
        bus.subscribe(MarketOpenEvent, lambda _: call_order.append(2))
        bus.subscribe(MarketOpenEvent, lambda _: call_order.append(3))

        bus.emit(MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30)))

        assert call_order == [1, 2, 3]

    def test_different_event_types_isolated(self) -> None:
        """不同事件类型的订阅互不影响。"""
        bus = EventBus()
        open_events: list[Event] = []
        close_events: list[Event] = []

        bus.subscribe(MarketOpenEvent, open_events.append)
        bus.subscribe(MarketCloseEvent, close_events.append)

        bus.emit(MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30)))
        bus.emit(MarketCloseEvent(timestamp=datetime(2024, 1, 2, 15, 0)))

        assert len(open_events) == 1
        assert len(close_events) == 1
        assert isinstance(open_events[0], MarketOpenEvent)
        assert isinstance(close_events[0], MarketCloseEvent)

    def test_handler_receives_correct_event_data(self) -> None:
        """处理器应接收到完整的事件数据。"""
        bus = EventBus()
        received: list[NavUpdateEvent] = []
        bus.subscribe(NavUpdateEvent, received.append)

        event = NavUpdateEvent(
            timestamp=datetime(2024, 1, 2, 20, 0),
            fund_code="000001",
            nav=Decimal("1.5432"),
        )
        bus.emit(event)

        assert received[0].fund_code == "000001"
        assert received[0].nav == Decimal("1.5432")

    def test_same_handler_subscribed_twice(self) -> None:
        """同一处理器订阅两次应被调用两次。"""
        bus = EventBus()
        count: list[int] = []
        handler = lambda _: count.append(1)  # noqa: E731

        bus.subscribe(MarketOpenEvent, handler)
        bus.subscribe(MarketOpenEvent, handler)

        bus.emit(MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30)))

        assert len(count) == 2


# ---------------------------------------------------------------------------
# 缓冲队列模式测试
# ---------------------------------------------------------------------------


class TestEventBusQueue:
    """EventBus 缓冲队列（enqueue + flush）测试。"""

    def test_enqueue_does_not_dispatch(self) -> None:
        """enqueue 不应立即分发事件。"""
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(MarketOpenEvent, received.append)

        bus.enqueue(MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30)))

        assert len(received) == 0
        assert bus.pending_count == 1

    def test_flush_dispatches_in_timestamp_order(self) -> None:
        """flush 应按时间戳升序分发事件。"""
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(MarketOpenEvent, received.append)
        bus.subscribe(MarketCloseEvent, received.append)
        bus.subscribe(NavUpdateEvent, received.append)

        # 故意乱序入队
        bus.enqueue(MarketCloseEvent(timestamp=datetime(2024, 1, 2, 15, 0)))
        bus.enqueue(MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30)))
        bus.enqueue(
            NavUpdateEvent(
                timestamp=datetime(2024, 1, 2, 20, 0),
                fund_code="000001",
                nav=Decimal("1.5"),
            )
        )

        bus.flush()

        assert len(received) == 3
        assert isinstance(received[0], MarketOpenEvent)
        assert isinstance(received[1], MarketCloseEvent)
        assert isinstance(received[2], NavUpdateEvent)

    def test_flush_stable_sort_same_timestamp(self) -> None:
        """相同时间戳的事件应按入队顺序分发（稳定排序）。"""
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(NavUpdateEvent, received.append)

        ts = datetime(2024, 1, 2, 20, 0)
        bus.enqueue(NavUpdateEvent(timestamp=ts, fund_code="000001", nav=Decimal("1.0")))
        bus.enqueue(NavUpdateEvent(timestamp=ts, fund_code="000002", nav=Decimal("2.0")))
        bus.enqueue(NavUpdateEvent(timestamp=ts, fund_code="000003", nav=Decimal("3.0")))

        bus.flush()

        assert len(received) == 3
        assert received[0].fund_code == "000001"
        assert received[1].fund_code == "000002"
        assert received[2].fund_code == "000003"

    def test_flush_clears_queue(self) -> None:
        """flush 后队列应为空。"""
        bus = EventBus()
        bus.subscribe(MarketOpenEvent, lambda _: None)

        bus.enqueue(MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30)))
        assert bus.pending_count == 1

        bus.flush()
        assert bus.pending_count == 0

    def test_flush_empty_queue(self) -> None:
        """空队列 flush 不应报错。"""
        bus = EventBus()
        bus.flush()  # 不应抛出异常
        assert bus.pending_count == 0

    def test_mixed_emit_and_enqueue(self) -> None:
        """emit 和 enqueue 可以混合使用。"""
        bus = EventBus()
        immediate: list[Event] = []
        queued: list[Event] = []

        bus.subscribe(MarketOpenEvent, immediate.append)
        bus.subscribe(MarketCloseEvent, queued.append)

        # 立即分发
        bus.emit(MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30)))
        assert len(immediate) == 1

        # 入队
        bus.enqueue(MarketCloseEvent(timestamp=datetime(2024, 1, 2, 15, 0)))
        assert len(queued) == 0

        # flush
        bus.flush()
        assert len(queued) == 1

    def test_pending_count(self) -> None:
        """pending_count 应正确反映队列长度。"""
        bus = EventBus()
        assert bus.pending_count == 0

        bus.enqueue(MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30)))
        assert bus.pending_count == 1

        bus.enqueue(MarketCloseEvent(timestamp=datetime(2024, 1, 2, 15, 0)))
        assert bus.pending_count == 2

        bus.flush()
        assert bus.pending_count == 0


# ---------------------------------------------------------------------------
# 清理方法测试
# ---------------------------------------------------------------------------


class TestEventBusClear:
    """EventBus 清理方法测试。"""

    def test_clear_removes_all(self) -> None:
        """clear 应清空订阅和队列。"""
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(MarketOpenEvent, received.append)
        bus.enqueue(MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30)))

        bus.clear()

        # 订阅已清空，emit 不应触发处理器
        bus.emit(MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30)))
        assert len(received) == 0
        assert bus.pending_count == 0

    def test_clear_queue_keeps_subscriptions(self) -> None:
        """clear_queue 应只清空队列，保留订阅。"""
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(MarketOpenEvent, received.append)
        bus.enqueue(MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30)))

        bus.clear_queue()

        assert bus.pending_count == 0
        # 订阅仍在，emit 应触发处理器
        bus.emit(MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30)))
        assert len(received) == 1


# ---------------------------------------------------------------------------
# 多事件类型综合场景
# ---------------------------------------------------------------------------


class TestEventBusIntegration:
    """事件总线综合场景测试。"""

    def test_full_trading_day_event_sequence(self) -> None:
        """模拟完整交易日事件序列。"""
        bus = EventBus()
        log: list[str] = []

        bus.subscribe(MarketOpenEvent, lambda e: log.append(f"open:{e.timestamp}"))
        bus.subscribe(NavUpdateEvent, lambda e: log.append(f"nav:{e.fund_code}={e.nav}"))
        bus.subscribe(OrderEvent, lambda e: log.append(f"order:{e.direction}"))
        bus.subscribe(ConfirmEvent, lambda e: log.append(f"confirm:{e.order_id}"))
        bus.subscribe(DividendEvent, lambda e: log.append(f"div:{e.fund_code}"))
        bus.subscribe(MarketCloseEvent, lambda e: log.append(f"close:{e.timestamp}"))

        # 模拟一天的事件流
        ts_base = datetime(2024, 1, 2)
        bus.enqueue(MarketOpenEvent(timestamp=ts_base.replace(hour=9, minute=30)))
        bus.enqueue(
            NavUpdateEvent(
                timestamp=ts_base.replace(hour=20, minute=0),
                fund_code="000001",
                nav=Decimal("1.5"),
            )
        )
        bus.enqueue(
            OrderEvent(
                timestamp=ts_base.replace(hour=10, minute=0),
                fund_code="000001",
                direction="subscribe",
                amount=Decimal("10000"),
            )
        )
        bus.enqueue(MarketCloseEvent(timestamp=ts_base.replace(hour=15, minute=0)))

        bus.flush()

        # 验证按时间顺序分发
        assert log[0].startswith("open:")
        assert log[1].startswith("order:")
        assert log[2].startswith("close:")
        assert log[3].startswith("nav:")

    def test_multiple_nav_updates_same_timestamp(self) -> None:
        """同一时间戳多只基金净值更新，保持入队顺序。"""
        bus = EventBus()
        codes: list[str] = []
        bus.subscribe(NavUpdateEvent, lambda e: codes.append(e.fund_code))

        ts = datetime(2024, 1, 2, 20, 0)
        for code in ["000001", "110011", "519003", "161725"]:
            bus.enqueue(NavUpdateEvent(timestamp=ts, fund_code=code, nav=Decimal("1.0")))

        bus.flush()

        assert codes == ["000001", "110011", "519003", "161725"]

    def test_subscribe_base_type_does_not_catch_subtypes(self) -> None:
        """订阅基类 Event 不会捕获子类事件（精确类型匹配）。"""
        bus = EventBus()
        base_events: list[Event] = []
        bus.subscribe(Event, base_events.append)

        bus.emit(MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30)))

        # 精确类型匹配，MarketOpenEvent 不会触发 Event 的处理器
        assert len(base_events) == 0
