"""风控引擎模块。

提供规则链式风控引擎，支持：
- MaxPositionRule: 单基金最大仓位限制
- MaxTypeExposureRule: 单类型基金最大仓位限制
- MinCashReserveRule: 最小现金保留
- MaxDrawdownCircuitBreaker: 最大回撤熔断（按比例缩仓）
- VolTargetRule: 波动率目标自适应杠杆

需求: 6.1, 6.2, 6.3
"""

from app.domain.risk.drawdown_control import MaxDrawdownCircuitBreaker
from app.domain.risk.limits import (
    MaxPositionRule,
    MaxTypeExposureRule,
    MinCashReserveRule,
    RiskRule,
    RuleChainRiskEngine,
)
from app.domain.risk.vol_target import VolTargetRule

__all__ = [
    "MaxPositionRule",
    "MaxTypeExposureRule",
    "MinCashReserveRule",
    "MaxDrawdownCircuitBreaker",
    "VolTargetRule",
    "RiskRule",
    "RuleChainRiskEngine",
]
