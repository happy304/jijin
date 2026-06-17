"""Broker 接口模块。

提供实盘交易接口的抽象与参考实现：
- Broker: Protocol 定义，策略层通过依赖注入使用
- PaperBroker: 纸面撮合实现，模拟 T+1 结算

需求: 10.3, 10.6
"""

from app.domain.broker.base import Broker
from app.domain.broker.paper import PaperBroker

__all__ = ["Broker", "PaperBroker"]
