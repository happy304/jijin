"""限购与状态检查模块测试。

覆盖场景：
1. 赎回订单始终放行
2. 基金暂停申购时拒绝申购
3. 超过限购额度时拒绝申购
4. 无限购限制时放行
5. 正常申购放行
6. 集成到引擎的 _queue_orders 流程验证

需求: 4.8
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.backtest.purchase_check import check_purchase_eligibility
from app.domain.backtest.engine_event import (
    EventDrivenEngine,
    FundMeta,
    BarContext,
)
from app.domain.backtest.order import OrderIntent


# ---------------------------------------------------------------------------
# check_purchase_eligibility 单元测试
# ---------------------------------------------------------------------------


class TestCheckPurchaseEligibility:
    """check_purchase_eligibility 函数测试。"""

    def test_redeem_always_allowed(self):
        """赎回订单始终放行，即使基金暂停申购。"""
        eligible, reason = check_purchase_eligibility(
            fund_code="000001",
            direction="redeem",
            amount=None,
            is_purchasable=False,
            purchase_limit=Decimal("0"),
        )
        assert eligible is True
        assert reason is None

    def test_redeem_allowed_with_purchase_limit(self):
        """赎回订单不受限购额度影响。"""
        eligible, reason = check_purchase_eligibility(
            fund_code="000001",
            direction="redeem",
            amount=Decimal("999999"),
            is_purchasable=True,
            purchase_limit=Decimal("1000"),
        )
        assert eligible is True
        assert reason is None

    def test_subscribe_rejected_when_not_purchasable(self):
        """基金暂停申购时拒绝申购订单。"""
        eligible, reason = check_purchase_eligibility(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
            is_purchasable=False,
            purchase_limit=None,
        )
        assert eligible is False
        assert reason == "基金暂停申购"

    def test_subscribe_rejected_when_exceeds_limit(self):
        """申购金额超过限购额度时拒绝。"""
        eligible, reason = check_purchase_eligibility(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("50000"),
            is_purchasable=True,
            purchase_limit=Decimal("10000"),
        )
        assert eligible is False
        assert reason == "超过限购额度"

    def test_subscribe_allowed_within_limit(self):
        """申购金额在限购额度内时放行。"""
        eligible, reason = check_purchase_eligibility(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("5000"),
            is_purchasable=True,
            purchase_limit=Decimal("10000"),
        )
        assert eligible is True
        assert reason is None

    def test_subscribe_allowed_at_exact_limit(self):
        """申购金额恰好等于限购额度时放行。"""
        eligible, reason = check_purchase_eligibility(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
            is_purchasable=True,
            purchase_limit=Decimal("10000"),
        )
        assert eligible is True
        assert reason is None

    def test_subscribe_allowed_no_limit(self):
        """无限购限制时放行。"""
        eligible, reason = check_purchase_eligibility(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("999999"),
            is_purchasable=True,
            purchase_limit=None,
        )
        assert eligible is True
        assert reason is None

    def test_subscribe_allowed_no_amount_no_limit(self):
        """金额为 None 且无限购限制时放行。"""
        eligible, reason = check_purchase_eligibility(
            fund_code="000001",
            direction="subscribe",
            amount=None,
            is_purchasable=True,
            purchase_limit=None,
        )
        assert eligible is True
        assert reason is None

    def test_subscribe_allowed_no_amount_with_limit(self):
        """金额为 None 但有限购限制时放行（无法比较）。"""
        eligible, reason = check_purchase_eligibility(
            fund_code="000001",
            direction="subscribe",
            amount=None,
            is_purchasable=True,
            purchase_limit=Decimal("10000"),
        )
        assert eligible is True
        assert reason is None

    def test_not_purchasable_takes_priority_over_limit(self):
        """暂停申购优先于限购额度检查。"""
        eligible, reason = check_purchase_eligibility(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("5000"),
            is_purchasable=False,
            purchase_limit=Decimal("10000"),
        )
        assert eligible is False
        assert reason == "基金暂停申购"


# ---------------------------------------------------------------------------
# FundMeta 新字段测试
# ---------------------------------------------------------------------------


class TestFundMetaFields:
    """FundMeta 新增字段测试。"""

    def test_default_is_purchasable(self):
        """默认 is_purchasable 为 True。"""
        meta = FundMeta(code="000001")
        assert meta.is_purchasable is True

    def test_default_purchase_limit_none(self):
        """默认 purchase_limit 为 None。"""
        meta = FundMeta(code="000001")
        assert meta.purchase_limit is None

    def test_custom_is_purchasable(self):
        """可设置 is_purchasable 为 False。"""
        meta = FundMeta(code="000001", is_purchasable=False)
        assert meta.is_purchasable is False

    def test_custom_purchase_limit(self):
        """可设置 purchase_limit。"""
        meta = FundMeta(code="000001", purchase_limit=Decimal("50000"))
        assert meta.purchase_limit == Decimal("50000")


# ---------------------------------------------------------------------------
# 引擎集成测试 - 限购检查在 _queue_orders 中的行为
# ---------------------------------------------------------------------------


class SubscribeStrategy:
    """申购策略：在第一天申购指定金额。"""

    def __init__(self, fund_code: str, amount: Decimal) -> None:
        self._fund_code = fund_code
        self._amount = amount
        self._done = False

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        if not self._done:
            self._done = True
            return [
                OrderIntent(
                    fund_code=self._fund_code,
                    direction="subscribe",
                    amount=self._amount,
                )
            ]
        return []


class RedeemStrategy:
    """赎回策略：在第一天赎回指定份额。"""

    def __init__(self, fund_code: str, shares: Decimal) -> None:
        self._fund_code = fund_code
        self._shares = shares
        self._done = False

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        if not self._done:
            self._done = True
            return [
                OrderIntent(
                    fund_code=self._fund_code,
                    direction="redeem",
                    shares=self._shares,
                )
            ]
        return []


class TestEngineIntegrationPurchaseCheck:
    """引擎集成测试：限购检查在 _queue_orders 中的行为。"""

    def _make_nav_data(self) -> dict[str, dict[date, Decimal]]:
        """构造简单的净值数据。"""
        return {
            "000001": {
                date(2024, 1, 2): Decimal("1.0000"),
                date(2024, 1, 3): Decimal("1.0100"),
                date(2024, 1, 4): Decimal("1.0200"),
                date(2024, 1, 5): Decimal("1.0300"),
            }
        }

    def test_subscribe_rejected_when_fund_suspended(self):
        """引擎中暂停申购的基金，申购订单被拒绝。"""
        engine = EventDrivenEngine()
        nav_data = self._make_nav_data()

        fund_meta = {
            "000001": FundMeta(
                code="000001",
                fund_type="stock",
                is_purchasable=False,
            )
        }

        strategy = SubscribeStrategy("000001", Decimal("10000"))
        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 5),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=fund_meta,
        )

        # 订单被拒绝，无成交
        assert len(result.trades) == 0
        # 现金未被冻结
        assert result.final_portfolio.cash == Decimal("100000")

    def test_subscribe_rejected_when_exceeds_limit(self):
        """引擎中申购金额超过限购额度，订单被拒绝。"""
        engine = EventDrivenEngine()
        nav_data = self._make_nav_data()

        fund_meta = {
            "000001": FundMeta(
                code="000001",
                fund_type="stock",
                is_purchasable=True,
                purchase_limit=Decimal("5000"),
            )
        }

        strategy = SubscribeStrategy("000001", Decimal("10000"))
        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 5),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=fund_meta,
        )

        # 订单被拒绝，无成交
        assert len(result.trades) == 0
        assert result.final_portfolio.cash == Decimal("100000")

    def test_subscribe_allowed_within_limit(self):
        """引擎中申购金额在限购额度内，订单正常处理。"""
        engine = EventDrivenEngine()
        nav_data = self._make_nav_data()

        fund_meta = {
            "000001": FundMeta(
                code="000001",
                fund_type="stock",
                is_purchasable=True,
                purchase_limit=Decimal("50000"),
            )
        }

        strategy = SubscribeStrategy("000001", Decimal("10000"))
        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 5),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=fund_meta,
        )

        # 订单成功确认
        assert len(result.trades) == 1
        assert result.trades[0].direction == "subscribe"

    def test_redeem_allowed_even_when_suspended(self):
        """引擎中即使基金暂停申购，赎回订单仍然放行。

        通过直接调用 _queue_orders 验证赎回不受限购影响。
        """
        engine = EventDrivenEngine()
        engine._init(Decimal("50000"))
        # 手动设置持仓
        engine._portfolio.positions["000001"] = Decimal("100")
        engine._portfolio.holding_days["000001"] = 30

        fund_meta = {
            "000001": FundMeta(
                code="000001",
                fund_type="stock",
                is_purchasable=False,
                purchase_limit=Decimal("0"),
            )
        }

        # 直接调用 _queue_orders 验证赎回订单入队
        intents = [
            OrderIntent(
                fund_code="000001",
                direction="redeem",
                shares=Decimal("50"),
            )
        ]
        engine._queue_orders(intents, date(2024, 1, 2), fund_meta)

        # 赎回订单应成功入队
        assert len(engine._portfolio.pending_orders) == 1
        assert engine._portfolio.pending_orders[0].direction == "redeem"

    def test_subscribe_allowed_no_meta(self):
        """引擎中无 fund_meta 时，申购订单默认放行。"""
        engine = EventDrivenEngine()
        nav_data = self._make_nav_data()

        strategy = SubscribeStrategy("000001", Decimal("10000"))
        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 5),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta={},
        )

        # 无 meta 时默认放行
        assert len(result.trades) == 1
        assert result.trades[0].direction == "subscribe"
