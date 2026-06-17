"""回测引擎事件类型与事件总线。

定义回测引擎中使用的所有事件类型（基于 Pydantic v2），以及同步事件分发总线。

事件类型：
- MarketOpenEvent: 市场开盘
- MarketCloseEvent: 市场收盘
- NavUpdateEvent: 净值更新
- OrderEvent: 订单事件
- ConfirmEvent: 订单确认
- DividendEvent: 分红事件
- RiskEvent: 风控触发

事件总线：
- EventBus: 同步事件分发，按时间戳排序，处理器按订阅顺序调用

用法示例::

    from datetime import datetime
    from app.domain.backtest.events import EventBus, MarketOpenEvent, NavUpdateEvent

    bus = EventBus()
    bus.subscribe(MarketOpenEvent, lambda e: print(f"开盘: {e.timestamp}"))
    bus.emit(MarketOpenEvent(timestamp=datetime(2024, 1, 2, 9, 30)))
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# 事件基类
# ---------------------------------------------------------------------------


class Event(BaseModel):
    """回测事件基类。

    所有事件必须包含时间戳和事件类型标识。
    """

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    event_type: str = ""

    def model_post_init(self, __context: object) -> None:
        """自动设置 event_type 为类名（如果未显式指定）。"""
        if not self.event_type:
            object.__setattr__(self, "event_type", self.__class__.__name__)


# ---------------------------------------------------------------------------
# 具体事件类型
# ---------------------------------------------------------------------------


class MarketOpenEvent(Event):
    """市场开盘事件。"""

    event_type: str = "MarketOpenEvent"


class MarketCloseEvent(Event):
    """市场收盘事件。"""

    event_type: str = "MarketCloseEvent"


class NavUpdateEvent(Event):
    """净值更新事件。"""

    event_type: str = "NavUpdateEvent"
    fund_code: str
    nav: Decimal


class OrderEvent(Event):
    """订单事件。

    申购时使用 amount（金额），赎回时使用 shares（份额）。
    """

    event_type: str = "OrderEvent"
    fund_code: str
    direction: Literal["subscribe", "redeem"]
    amount: Decimal | None = None
    shares: Decimal | None = None


class ConfirmEvent(Event):
    """订单确认事件（T+1 确认后触发）。"""

    event_type: str = "ConfirmEvent"
    order_id: str
    confirmed_shares: Decimal
    confirmed_amount: Decimal
    fee: Decimal


class DividendEvent(Event):
    """分红事件。"""

    event_type: str = "DividendEvent"
    fund_code: str
    dividend_per_share: Decimal
    reinvest: bool


class RiskEvent(Event):
    """风控触发事件。"""

    event_type: str = "RiskEvent"
    reason: str


# ---------------------------------------------------------------------------
# 事件总线
# ---------------------------------------------------------------------------

# 事件处理器类型
EventHandler = Callable[[Event], None]


class EventBus:
    """同步事件总线。

    支持按事件类型订阅处理器，事件分发时按订阅顺序调用处理器。
    当多个事件同时发射时，可使用 flush() 按时间戳排序后统一分发。

    特性：
    - 同步分发，适用于回测场景
    - 处理器按订阅顺序调用
    - 支持缓冲模式：emit 入队列，flush 按时间排序后分发
    """

    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[EventHandler]] = defaultdict(list)
        self._queue: list[Event] = []

    def subscribe(self, event_type: type[Event], handler: EventHandler) -> None:
        """订阅指定事件类型。

        同一处理器可多次订阅同一事件类型（每次订阅都会被调用）。

        Args:
            event_type: 要订阅的事件类型（Event 的子类）
            handler: 事件处理函数，接收事件实例作为参数
        """
        self._handlers[event_type].append(handler)

    def emit(self, event: Event) -> None:
        """立即分发事件给所有已订阅的处理器。

        处理器按订阅顺序同步调用。

        Args:
            event: 要分发的事件实例
        """
        handlers = self._handlers.get(type(event), [])
        for handler in handlers:
            handler(event)

    def enqueue(self, event: Event) -> None:
        """将事件加入缓冲队列，等待 flush() 统一分发。

        适用于需要按时间戳排序后再分发的场景。

        Args:
            event: 要入队的事件实例
        """
        self._queue.append(event)

    def flush(self) -> None:
        """按时间戳排序后分发队列中的所有事件，然后清空队列。

        相同时间戳的事件按入队顺序分发（稳定排序）。
        """
        # 使用稳定排序，相同时间戳保持入队顺序
        sorted_events = sorted(self._queue, key=lambda e: e.timestamp)
        self._queue.clear()
        for event in sorted_events:
            self.emit(event)

    @property
    def pending_count(self) -> int:
        """返回缓冲队列中待分发的事件数量。"""
        return len(self._queue)

    def clear(self) -> None:
        """清空所有订阅和缓冲队列。"""
        self._handlers.clear()
        self._queue.clear()

    def clear_queue(self) -> None:
        """仅清空缓冲队列，保留订阅关系。"""
        self._queue.clear()
