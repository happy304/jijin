"""风控引擎单元测试。

覆盖：
- MaxPositionRule: 单基金最大仓位限制
- MaxTypeExposureRule: 单类型基金最大仓位限制
- MinCashReserveRule: 最小现金保留
- MaxDrawdownCircuitBreaker: 最大回撤熔断
- VolTargetRule: 波动率目标自适应杠杆
- RuleChainRiskEngine: 规则链组合

需求: 6.1, 6.2, 6.3
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.backtest.order import OrderIntent
from app.domain.backtest.portfolio import Portfolio
from app.domain.risk.drawdown_control import MaxDrawdownCircuitBreaker
from app.domain.risk.limits import (
    MaxPositionRule,
    MaxTypeExposureRule,
    MinCashReserveRule,
    RuleChainRiskEngine,
)
from app.domain.risk.vol_target import VolTargetRule, compute_realized_volatility


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------


def _make_portfolio(
    cash: Decimal = Decimal("100000"),
    positions: dict[str, Decimal] | None = None,
) -> Portfolio:
    """构建测试用 Portfolio。"""
    return Portfolio(cash=cash, positions=positions or {})


def _subscribe(fund_code: str, amount: Decimal) -> OrderIntent:
    """构建申购意图。"""
    return OrderIntent(fund_code=fund_code, direction="subscribe", amount=amount)


def _redeem(fund_code: str, shares: Decimal) -> OrderIntent:
    """构建赎回意图。"""
    return OrderIntent(fund_code=fund_code, direction="redeem", shares=shares)


# ---------------------------------------------------------------------------
# TestMaxPositionRule
# ---------------------------------------------------------------------------


class TestMaxPositionRule:
    """MaxPositionRule 单元测试。"""

    def test_order_within_limit_passes(self):
        """仓位未超限的订单正常通过。"""
        nav_dict = {"000001": Decimal("1.5")}
        rule = MaxPositionRule(
            max_weight=Decimal("0.3"),
            nav_provider=lambda code: nav_dict.get(code),
        )
        portfolio = _make_portfolio(cash=Decimal("100000"))
        orders = [_subscribe("000001", Decimal("20000"))]

        result = rule.apply(orders, portfolio)

        assert len(result) == 1
        assert result[0].amount == Decimal("20000")

    def test_order_exceeding_limit_is_reduced(self):
        """超限订单被缩减到安全线。"""
        nav_dict = {"000001": Decimal("2.0")}
        rule = MaxPositionRule(
            max_weight=Decimal("0.3"),
            nav_provider=lambda code: nav_dict.get(code),
        )
        # 组合：现金 70000，持仓 000001: 10000份 * 2.0 = 20000 -> 总市值 90000
        # 当前仓位 20000/90000 = 22.2%
        # 申购 50000 -> 新仓位 70000/140000 = 50% > 30%
        portfolio = _make_portfolio(
            cash=Decimal("70000"),
            positions={"000001": Decimal("10000")},
        )
        orders = [_subscribe("000001", Decimal("50000"))]

        result = rule.apply(orders, portfolio)

        assert len(result) == 1
        # allowed = (0.3 * 90000 - 20000) / (1 - 0.3) = 7000 / 0.7 = 10000
        assert result[0].amount == Decimal("10000.00")

    def test_already_at_limit_rejects_order(self):
        """已达上限时拒绝新增申购。"""
        nav_dict = {"000001": Decimal("3.0")}
        rule = MaxPositionRule(
            max_weight=Decimal("0.3"),
            nav_provider=lambda code: nav_dict.get(code),
        )
        # 组合：现金 70000，持仓 000001: 10000份 * 3.0 = 30000 -> 总市值 100000
        # 当前仓位 30000/100000 = 30% = 上限
        portfolio = _make_portfolio(
            cash=Decimal("70000"),
            positions={"000001": Decimal("10000")},
        )
        orders = [_subscribe("000001", Decimal("10000"))]

        result = rule.apply(orders, portfolio)

        assert len(result) == 0

    def test_redeem_orders_pass_through(self):
        """赎回订单不受限制。"""
        rule = MaxPositionRule(
            max_weight=Decimal("0.3"),
            nav_provider=lambda code: Decimal("1.0"),
        )
        portfolio = _make_portfolio(
            cash=Decimal("50000"),
            positions={"000001": Decimal("50000")},
        )
        orders = [_redeem("000001", Decimal("10000"))]

        result = rule.apply(orders, portfolio)

        assert len(result) == 1
        assert result[0].direction == "redeem"

    def test_empty_orders_returns_empty(self):
        """空订单列表直接返回。"""
        rule = MaxPositionRule(
            max_weight=Decimal("0.3"),
            nav_provider=lambda code: Decimal("1.0"),
        )
        portfolio = _make_portfolio()

        result = rule.apply([], portfolio)

        assert result == []

    def test_invalid_max_weight_raises(self):
        """无效的 max_weight 参数抛出异常。"""
        with pytest.raises(ValueError):
            MaxPositionRule(max_weight=Decimal("0"))
        with pytest.raises(ValueError):
            MaxPositionRule(max_weight=Decimal("1.5"))



# ---------------------------------------------------------------------------
# TestMaxTypeExposureRule
# ---------------------------------------------------------------------------


class TestMaxTypeExposureRule:
    """MaxTypeExposureRule 单元测试。"""

    def _fund_type(self, code: str) -> str:
        types = {
            "000001": "stock",
            "000002": "stock",
            "000003": "bond",
            "000004": "bond",
        }
        return types.get(code, "stock")

    def test_type_within_limit_passes(self):
        """类型仓位未超限的订单正常通过。"""
        nav_dict = {"000001": Decimal("1.0"), "000003": Decimal("1.0")}
        rule = MaxTypeExposureRule(
            max_type_weight=Decimal("0.6"),
            fund_type_provider=self._fund_type,
            nav_provider=lambda code: nav_dict.get(code),
        )
        portfolio = _make_portfolio(
            cash=Decimal("80000"),
            positions={"000001": Decimal("10000")},
        )
        orders = [_subscribe("000002", Decimal("30000"))]

        result = rule.apply(orders, portfolio)

        assert len(result) == 1
        assert result[0].amount == Decimal("30000")

    def test_type_exceeding_limit_is_reduced(self):
        """类型仓位超限的订单被缩减。"""
        nav_dict = {"000001": Decimal("1.0"), "000002": Decimal("1.0")}
        rule = MaxTypeExposureRule(
            max_type_weight=Decimal("0.5"),
            fund_type_provider=self._fund_type,
            nav_provider=lambda code: nav_dict.get(code),
        )
        # 组合：现金 60000，stock 持仓 000001: 40000份*1.0=40000 -> 总市值 100000
        # stock 仓位 40000/100000 = 40%
        # 申购 stock 000002 30000 -> 新 stock 70000/130000 = 53.8% > 50%
        portfolio = _make_portfolio(
            cash=Decimal("60000"),
            positions={"000001": Decimal("40000")},
        )
        orders = [_subscribe("000002", Decimal("30000"))]

        result = rule.apply(orders, portfolio)

        assert len(result) == 1
        # allowed = (0.5 * 100000 - 40000) / (1 - 0.5) = 10000/0.5 = 20000
        assert result[0].amount == Decimal("20000.00")

    def test_different_types_independent(self):
        """不同类型的仓位独立计算。"""
        nav_dict = {
            "000001": Decimal("1.0"),
            "000003": Decimal("1.0"),
        }
        rule = MaxTypeExposureRule(
            max_type_weight=Decimal("0.5"),
            fund_type_provider=self._fund_type,
            nav_provider=lambda code: nav_dict.get(code),
        )
        portfolio = _make_portfolio(
            cash=Decimal("60000"),
            positions={"000001": Decimal("40000")},
        )
        orders = [_subscribe("000003", Decimal("20000"))]

        result = rule.apply(orders, portfolio)

        assert len(result) == 1
        assert result[0].amount == Decimal("20000")

    def test_invalid_max_type_weight_raises(self):
        """无效参数抛出异常。"""
        with pytest.raises(ValueError):
            MaxTypeExposureRule(
                max_type_weight=Decimal("0"),
                fund_type_provider=lambda c: "stock",
            )



# ---------------------------------------------------------------------------
# TestMinCashReserveRule
# ---------------------------------------------------------------------------


class TestMinCashReserveRule:
    """MinCashReserveRule 单元测试。"""

    def test_sufficient_cash_passes(self):
        """现金充足时订单正常通过。"""
        rule = MinCashReserveRule(min_cash_ratio=Decimal("0.05"))
        portfolio = _make_portfolio(cash=Decimal("100000"))
        orders = [_subscribe("000001", Decimal("90000"))]

        result = rule.apply(orders, portfolio)

        assert len(result) == 1
        assert result[0].amount == Decimal("90000")

    def test_insufficient_cash_reduces_order(self):
        """现金不足时缩减订单金额。"""
        rule = MinCashReserveRule(min_cash_ratio=Decimal("0.10"))
        portfolio = _make_portfolio(cash=Decimal("100000"))
        orders = [_subscribe("000001", Decimal("95000"))]

        result = rule.apply(orders, portfolio)

        assert len(result) == 1
        assert result[0].amount == Decimal("90000.00")

    def test_multiple_orders_sequential_cash_check(self):
        """多笔订单按顺序检查现金。"""
        rule = MinCashReserveRule(min_cash_ratio=Decimal("0.10"))
        portfolio = _make_portfolio(cash=Decimal("100000"))
        orders = [
            _subscribe("000001", Decimal("50000")),
            _subscribe("000002", Decimal("50000")),
        ]

        result = rule.apply(orders, portfolio)

        # 第一笔 50000 通过，剩余 50000
        # 第二笔 50000 -> 剩余 0 < 0.10*50000=5000 -> 缩减到 50000-5000=45000
        assert len(result) == 2
        assert result[0].amount == Decimal("50000")
        assert result[1].amount == Decimal("45000.00")

    def test_redeem_passes_through(self):
        """赎回订单不受限制。"""
        rule = MinCashReserveRule(min_cash_ratio=Decimal("0.50"))
        portfolio = _make_portfolio(cash=Decimal("1000"))
        orders = [_redeem("000001", Decimal("5000"))]

        result = rule.apply(orders, portfolio)

        assert len(result) == 1

    def test_invalid_ratio_raises(self):
        """无效参数抛出异常。"""
        with pytest.raises(ValueError):
            MinCashReserveRule(min_cash_ratio=Decimal("-0.1"))
        with pytest.raises(ValueError):
            MinCashReserveRule(min_cash_ratio=Decimal("1.0"))



# ---------------------------------------------------------------------------
# TestMaxDrawdownCircuitBreaker
# ---------------------------------------------------------------------------


class TestMaxDrawdownCircuitBreaker:
    """MaxDrawdownCircuitBreaker 单元测试。"""

    def test_no_drawdown_passes_all(self):
        """无回撤时所有订单正常通过。"""
        breaker = MaxDrawdownCircuitBreaker(
            max_drawdown=Decimal("0.15"),
            target_position_ratio=Decimal("0.5"),
            equity_peak_provider=lambda: Decimal("100000"),
            current_equity_provider=lambda: Decimal("100000"),
        )
        breaker.update_peak(Decimal("100000"))
        portfolio = _make_portfolio(
            cash=Decimal("50000"),
            positions={"000001": Decimal("10000")},
        )
        orders = [_subscribe("000002", Decimal("10000"))]

        result = breaker.apply(orders, portfolio)

        assert len(result) == 1
        assert result[0].direction == "subscribe"

    def test_drawdown_triggers_circuit_breaker(self):
        """回撤超过阈值触发熔断，生成缩仓订单。"""
        breaker = MaxDrawdownCircuitBreaker(
            max_drawdown=Decimal("0.10"),
            target_position_ratio=Decimal("0.5"),
            equity_peak_provider=lambda: Decimal("100000"),
            current_equity_provider=lambda: Decimal("85000"),  # 15% drawdown
            current_date_provider=lambda: date(2024, 3, 1),
        )
        breaker.update_peak(Decimal("100000"))
        portfolio = _make_portfolio(
            cash=Decimal("35000"),
            positions={
                "000001": Decimal("20000"),
                "000002": Decimal("10000"),
            },
        )
        orders = [_subscribe("000003", Decimal("5000"))]

        result = breaker.apply(orders, portfolio)

        assert breaker.is_triggered
        assert all(o.direction == "redeem" for o in result)
        assert len(result) == 2
        redeem_shares = {o.fund_code: o.shares for o in result}
        assert redeem_shares["000001"] == Decimal("10000.00")
        assert redeem_shares["000002"] == Decimal("5000.00")

    def test_cooldown_blocks_subscribe(self):
        """冷却期内阻止申购。"""
        breaker = MaxDrawdownCircuitBreaker(
            max_drawdown=Decimal("0.10"),
            target_position_ratio=Decimal("0.5"),
            cooldown_days=5,
            equity_peak_provider=lambda: Decimal("100000"),
            current_equity_provider=lambda: Decimal("95000"),
            current_date_provider=lambda: date(2024, 3, 3),
        )
        breaker._is_triggered = True
        breaker._trigger_date = date(2024, 3, 1)
        breaker._peak_equity = Decimal("100000")

        portfolio = _make_portfolio(cash=Decimal("50000"))
        orders = [
            _subscribe("000001", Decimal("10000")),
            _redeem("000002", Decimal("5000")),
        ]

        result = breaker.apply(orders, portfolio)

        assert len(result) == 1
        assert result[0].direction == "redeem"

    def test_cooldown_expires_allows_subscribe(self):
        """冷却期结束后恢复正常。"""
        breaker = MaxDrawdownCircuitBreaker(
            max_drawdown=Decimal("0.10"),
            target_position_ratio=Decimal("0.5"),
            cooldown_days=5,
            equity_peak_provider=lambda: Decimal("100000"),
            current_equity_provider=lambda: Decimal("98000"),
            current_date_provider=lambda: date(2024, 3, 10),
        )
        breaker._is_triggered = True
        breaker._trigger_date = date(2024, 3, 1)
        breaker._peak_equity = Decimal("100000")

        portfolio = _make_portfolio(cash=Decimal("50000"))
        orders = [_subscribe("000001", Decimal("10000"))]

        result = breaker.apply(orders, portfolio)

        assert not breaker.is_triggered
        assert len(result) == 1
        assert result[0].direction == "subscribe"

    def test_reset_clears_state(self):
        """reset 清除所有状态。"""
        breaker = MaxDrawdownCircuitBreaker(
            max_drawdown=Decimal("0.15"),
        )
        breaker._is_triggered = True
        breaker._trigger_date = date(2024, 1, 1)
        breaker._peak_equity = Decimal("200000")

        breaker.reset()

        assert not breaker.is_triggered
        assert breaker.trigger_date is None
        assert breaker._peak_equity == Decimal("0")

    def test_invalid_params_raise(self):
        """无效参数抛出异常。"""
        with pytest.raises(ValueError):
            MaxDrawdownCircuitBreaker(max_drawdown=Decimal("0"))
        with pytest.raises(ValueError):
            MaxDrawdownCircuitBreaker(max_drawdown=Decimal("1.0"))
        with pytest.raises(ValueError):
            MaxDrawdownCircuitBreaker(
                max_drawdown=Decimal("0.1"),
                target_position_ratio=Decimal("1.5"),
            )



# ---------------------------------------------------------------------------
# TestVolTargetRule
# ---------------------------------------------------------------------------


class TestVolTargetRule:
    """VolTargetRule 单元测试。"""

    def test_low_vol_no_restriction(self):
        """波动率低于目标时不限制。"""
        returns = [Decimal("0.001")] * 10 + [Decimal("-0.001")] * 10
        rule = VolTargetRule(
            target_vol=Decimal("0.20"),
            lookback_days=20,
            returns_provider=lambda: returns,
        )
        portfolio = _make_portfolio(cash=Decimal("100000"))
        orders = [_subscribe("000001", Decimal("50000"))]

        result = rule.apply(orders, portfolio)

        assert len(result) == 1
        assert result[0].amount == Decimal("50000")
        assert rule.last_leverage >= Decimal("1.0")

    def test_high_vol_reduces_orders(self):
        """波动率高于目标时缩减订单。"""
        returns = [Decimal("0.05"), Decimal("-0.05")] * 15
        rule = VolTargetRule(
            target_vol=Decimal("0.10"),
            lookback_days=20,
            returns_provider=lambda: returns,
        )
        portfolio = _make_portfolio(cash=Decimal("100000"))
        orders = [_subscribe("000001", Decimal("50000"))]

        result = rule.apply(orders, portfolio)

        assert len(result) == 1
        assert result[0].amount < Decimal("50000")
        assert rule.last_leverage < Decimal("1.0")

    def test_redeem_not_affected(self):
        """赎回订单不受波动率限制。"""
        returns = [Decimal("0.05"), Decimal("-0.05")] * 15
        rule = VolTargetRule(
            target_vol=Decimal("0.10"),
            lookback_days=20,
            returns_provider=lambda: returns,
        )
        portfolio = _make_portfolio(
            cash=Decimal("50000"),
            positions={"000001": Decimal("10000")},
        )
        orders = [_redeem("000001", Decimal("5000"))]

        result = rule.apply(orders, portfolio)

        assert len(result) == 1
        assert result[0].shares == Decimal("5000")

    def test_insufficient_data_no_restriction(self):
        """数据不足时不限制。"""
        returns = [Decimal("0.01")]
        rule = VolTargetRule(
            target_vol=Decimal("0.10"),
            lookback_days=20,
            returns_provider=lambda: returns,
        )
        portfolio = _make_portfolio(cash=Decimal("100000"))
        orders = [_subscribe("000001", Decimal("50000"))]

        result = rule.apply(orders, portfolio)

        assert len(result) == 1
        assert result[0].amount == Decimal("50000")

    def test_leverage_clipped_to_bounds(self):
        """杠杆因子被限制在上下限内。"""
        returns = [Decimal("0.0001")] * 20
        rule = VolTargetRule(
            target_vol=Decimal("0.50"),
            lookback_days=20,
            max_leverage=Decimal("2.0"),
            min_leverage=Decimal("0.1"),
            returns_provider=lambda: returns,
        )

        leverage = rule.compute_leverage()

        assert leverage <= Decimal("2.0")

    def test_invalid_params_raise(self):
        """无效参数抛出异常。"""
        with pytest.raises(ValueError):
            VolTargetRule(target_vol=Decimal("0"))
        with pytest.raises(ValueError):
            VolTargetRule(target_vol=Decimal("0.1"), lookback_days=1)
        with pytest.raises(ValueError):
            VolTargetRule(
                target_vol=Decimal("0.1"),
                max_leverage=Decimal("0.5"),
                min_leverage=Decimal("1.0"),
            )



# ---------------------------------------------------------------------------
# TestComputeRealizedVolatility
# ---------------------------------------------------------------------------


class TestComputeRealizedVolatility:
    """compute_realized_volatility 单元测试。"""

    def test_zero_returns(self):
        """全零收益率 -> 波动率为 0。"""
        returns = [Decimal("0")] * 20
        vol = compute_realized_volatility(returns)
        assert vol == Decimal("0")

    def test_constant_returns(self):
        """恒定收益率 -> 波动率为 0。"""
        returns = [Decimal("0.01")] * 20
        vol = compute_realized_volatility(returns)
        assert vol == Decimal("0")

    def test_insufficient_data(self):
        """数据不足 -> 返回 0。"""
        vol = compute_realized_volatility([Decimal("0.01")])
        assert vol == Decimal("0")

    def test_positive_volatility(self):
        """正常收益序列 -> 正波动率。"""
        returns = [Decimal("0.02"), Decimal("-0.01"), Decimal("0.03"),
                   Decimal("-0.02"), Decimal("0.01")] * 4
        vol = compute_realized_volatility(returns)
        assert vol > Decimal("0")


# ---------------------------------------------------------------------------
# TestRuleChainRiskEngine
# ---------------------------------------------------------------------------


class TestRuleChainRiskEngine:
    """RuleChainRiskEngine 规则链组合测试。"""

    def test_empty_rules_passes_all(self):
        """无规则时所有订单通过。"""
        engine = RuleChainRiskEngine(rules=[])
        portfolio = _make_portfolio(cash=Decimal("100000"))
        orders = [_subscribe("000001", Decimal("50000"))]

        result = engine.validate(orders, portfolio)

        assert len(result) == 1
        assert result[0].amount == Decimal("50000")

    def test_single_rule(self):
        """单规则正常工作。"""
        engine = RuleChainRiskEngine(rules=[
            MinCashReserveRule(min_cash_ratio=Decimal("0.10")),
        ])
        portfolio = _make_portfolio(cash=Decimal("100000"))
        orders = [_subscribe("000001", Decimal("95000"))]

        result = engine.validate(orders, portfolio)

        assert len(result) == 1
        assert result[0].amount == Decimal("90000.00")

    def test_multiple_rules_chain(self):
        """多规则链式执行。"""
        nav_dict = {"000001": Decimal("1.0"), "000002": Decimal("1.0")}
        engine = RuleChainRiskEngine(rules=[
            MaxPositionRule(
                max_weight=Decimal("0.4"),
                nav_provider=lambda code: nav_dict.get(code),
            ),
            MinCashReserveRule(min_cash_ratio=Decimal("0.05")),
        ])
        portfolio = _make_portfolio(cash=Decimal("100000"))
        orders = [_subscribe("000001", Decimal("80000"))]

        result = engine.validate(orders, portfolio)

        assert len(result) == 1
        assert result[0].amount < Decimal("80000")

    def test_add_rule(self):
        """动态添加规则。"""
        engine = RuleChainRiskEngine()
        engine.add_rule(MinCashReserveRule(min_cash_ratio=Decimal("0.10")))

        assert len(engine.rules) == 1

    def test_all_orders_filtered_stops_chain(self):
        """所有订单被过滤后停止链式执行。"""
        nav_dict = {"000001": Decimal("1.0")}
        engine = RuleChainRiskEngine(rules=[
            MaxPositionRule(
                max_weight=Decimal("0.01"),
                nav_provider=lambda code: nav_dict.get(code),
            ),
            MinCashReserveRule(min_cash_ratio=Decimal("0.99")),
        ])
        portfolio = _make_portfolio(
            cash=Decimal("50000"),
            positions={"000001": Decimal("50000")},
        )
        orders = [_subscribe("000001", Decimal("10000"))]

        result = engine.validate(orders, portfolio)

        assert len(result) == 0

    def test_conforms_to_risk_engine_protocol(self):
        """RuleChainRiskEngine 符合 RiskEngine Protocol。"""
        engine = RuleChainRiskEngine()
        assert hasattr(engine, "validate")
        portfolio = _make_portfolio()
        result = engine.validate([], portfolio)
        assert result == []
