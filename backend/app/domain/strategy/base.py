"""策略基类与上下文模块。

提供策略开发的基础抽象：
- BaseStrategy: 策略抽象基类，定义策略生命周期方法
- StrategyParams: 策略参数基类（Pydantic BaseModel）
- rebalance_to: 辅助函数，根据目标权重自动生成申赎意图

设计要点：
- 策略代码在回测与实盘下无需修改（需求 10.6）
- on_bar 返回 OrderIntent 列表，由引擎负责执行
- rebalance_to 根据当前持仓和目标权重计算差额，生成最小化调仓指令

需求: 5.9, 10.5, 10.6
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.domain.backtest.engine_event import BarContext
from app.domain.backtest.order import OrderIntent


# ---------------------------------------------------------------------------
# 策略参数基类
# ---------------------------------------------------------------------------


class StrategyParams(BaseModel):
    """策略参数基类。

    所有策略的参数应继承此类，利用 Pydantic 的类型校验和序列化能力。
    子类可自由添加字段，支持 JSON Schema 生成（用于前端表单）。

    Example::

        class MomentumParams(StrategyParams):
            lookback_months: int = 6
            top_n: int = 3
            rebalance_freq: str = "monthly"
    """

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 策略基类
# ---------------------------------------------------------------------------


class BaseStrategy(ABC):
    """策略抽象基类。

    所有策略必须继承此类并实现 on_bar 方法。
    策略代码在回测和实盘环境下应无需修改。

    Attributes:
        name: 策略名称
        params: 策略参数实例
        universe: 基金池（基金代码列表）

    Lifecycle:
        1. __init__(params, universe) - 构造
        2. on_init(context) - 回测/实盘启动时调用一次
        3. on_bar(context) - 每个交易日调用，返回调仓意图
        4. on_dividend(context, fund_code, amount) - 分红事件回调
        5. on_order_filled(context, fill) - 订单成交回调

    Example::

        class BuyAndHold(BaseStrategy):
            name = "buy_and_hold"

            def on_bar(self, context: BarContext) -> list[OrderIntent]:
                if not context.positions:
                    return [OrderIntent(
                        fund_code=self.universe[0],
                        direction="subscribe",
                        amount=context.cash,
                    )]
                return []
    """

    name: str = "unnamed_strategy"

    def __init__(
        self,
        params: StrategyParams | None = None,
        universe: list[str] | None = None,
    ) -> None:
        """初始化策略。

        Args:
            params: 策略参数，默认为空 StrategyParams
            universe: 基金池，默认为空列表
        """
        self.params = params or StrategyParams()
        self.universe = universe or []

    def on_init(self, context: BarContext) -> None:
        """回测/实盘启动时调用。

        可用于初始化策略内部状态、预计算指标等。
        默认不做任何操作。

        Args:
            context: 策略上下文
        """

    @abstractmethod
    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        """每个交易日调用，返回调仓意图列表。

        这是策略的核心决策方法。引擎在每个交易日调用此方法，
        策略根据 context 中的信息（历史净值、当前持仓、现金等）
        决定是否调仓。

        Args:
            context: 当日策略上下文（只能看到 T-1 及之前数据）

        Returns:
            OrderIntent 列表，空列表表示不操作
        """
        ...

    def on_dividend(
        self,
        context: BarContext,
        fund_code: str,
        dividend_per_share: Decimal,
    ) -> None:
        """分红事件回调。

        当持仓基金发生分红时调用。默认不做任何操作。

        Args:
            context: 当日策略上下文
            fund_code: 分红基金代码
            dividend_per_share: 每份分红金额
        """

    def on_order_filled(
        self,
        context: BarContext,
        order_id: str,
        fund_code: str,
        direction: str,
        shares: Decimal,
        amount: Decimal,
        fee: Decimal,
    ) -> None:
        """订单成交回调。

        当订单确认成交时调用。默认不做任何操作。

        Args:
            context: 当日策略上下文
            order_id: 订单 ID
            fund_code: 基金代码
            direction: 交易方向 (subscribe/redeem)
            shares: 成交份额
            amount: 成交金额
            fee: 交易费用
        """


# ---------------------------------------------------------------------------
# rebalance_to 辅助函数
# ---------------------------------------------------------------------------


def rebalance_to(
    context: BarContext,
    target_weights: dict[str, float],
    total_value: Decimal | None = None,
    min_trade_amount: Decimal = Decimal("100"),
    cost_threshold: Decimal | None = None,
    turnover_limit: float | None = None,
) -> list[OrderIntent]:
    """根据目标权重生成调仓意图。

    计算当前持仓与目标权重的差异，生成最小化的申赎指令。

    算法：
    1. 计算组合总市值（现金 + 持仓市值）
    2. 对每只基金计算目标金额 = 总市值 × 目标权重
    3. 计算当前金额 = 当前份额 × 最新净值
    4. **可选**：如果指定了 ``turnover_limit``，则按 L1 比例缩放调仓量
       使 ||w_target - w_current||_1 不超过该阈值
    5. 差额为正 → 申购，差额为负 → 赎回
    6. 忽略差额小于阈值的调仓

    Args:
        context: 当日策略上下文
        target_weights: 目标权重字典 {fund_code: weight}，权重和应 ≤ 1
        total_value: 组合总市值（可选，不传则自动计算）
        min_trade_amount: 最小调仓金额阈值（默认 100 元）
        cost_threshold: 交易成本阈值，低于此金额的调仓跳过（默认 None 使用 min_trade_amount）
        turnover_limit: 单次调仓的最大 L1 换手率（如 0.3 = 单边 30%）。
            None 表示不限制。如果原始计划换手超出，所有调仓量按比例缩放，
            优先保留方向不变；调仓后实际换手会接近且不超过该阈值。

    Returns:
        OrderIntent 列表

    Notes:
        - 权重为 0 的基金会生成全部赎回指令
        - 当前持有但不在 target_weights 中的基金也会被赎回
        - 最小调仓阈值避免产生过多小额交易
    """
    MIN_TRADE_AMOUNT = min_trade_amount

    # 计算组合总市值
    if total_value is None:
        # 使用 context 中的信息估算总市值
        position_value = Decimal("0")
        for code, shares in context.positions.items():
            nav = context.nav(code)
            if nav is not None:
                position_value += shares * nav
        total_value = context.cash + position_value

    if total_value <= Decimal("0"):
        return []

    # 收集所有涉及的基金代码
    all_codes = set(target_weights.keys()) | set(context.positions.keys())

    # 1) 先计算每只基金的目标金额、当前金额、原始差额
    diffs: dict[str, Decimal] = {}
    navs: dict[str, Decimal] = {}
    current_amounts: dict[str, Decimal] = {}
    target_amounts: dict[str, Decimal] = {}

    for code in all_codes:
        target_weight = Decimal(str(target_weights.get(code, 0)))
        target_amount = total_value * target_weight

        current_shares = context.positions.get(code, Decimal("0"))
        nav = context.nav(code)
        if nav is None or nav <= Decimal("0"):
            continue
        current_amount = current_shares * nav

        diffs[code] = target_amount - current_amount
        navs[code] = nav
        current_amounts[code] = current_amount
        target_amounts[code] = target_amount

    # 2) 应用换手率约束（如指定）
    # 计划换手率 = sum(|diff|) / total_value / 2  (单边)
    if turnover_limit is not None and turnover_limit > 0:
        total_abs_diff = sum((d.copy_abs() for d in diffs.values()), Decimal("0"))
        if total_abs_diff > Decimal("0"):
            # one-way turnover = sum(|target_w - current_w|) / 2
            one_way_turnover = total_abs_diff / total_value / Decimal("2")
            limit = Decimal(str(turnover_limit))
            if one_way_turnover > limit:
                scale = limit / one_way_turnover
                diffs = {code: d * scale for code, d in diffs.items()}

    orders: list[OrderIntent] = []

    for code, diff in diffs.items():
        nav = navs[code]
        target_weight = Decimal(str(target_weights.get(code, 0)))

        if diff > MIN_TRADE_AMOUNT:
            # 需要申购
            orders.append(
                OrderIntent(
                    fund_code=code,
                    direction="subscribe",
                    amount=diff,
                    target_weight=target_weight,
                )
            )
        elif diff < -MIN_TRADE_AMOUNT:
            # 需要赎回
            redeem_shares = (-diff / nav).quantize(Decimal("0.01"))
            current_shares = context.positions.get(code, Decimal("0"))
            redeem_shares = min(redeem_shares, current_shares)
            if redeem_shares > Decimal("0"):
                orders.append(
                    OrderIntent(
                        fund_code=code,
                        direction="redeem",
                        shares=redeem_shares,
                        target_weight=target_weight,
                    )
                )

    return orders


# ---------------------------------------------------------------------------
# 策略工厂函数
# ---------------------------------------------------------------------------


def create_strategy_from_config(
    strategy_type: str | None,
    params: dict[str, Any],
    universe: dict[str, Any] | list[str],
) -> BaseStrategy:
    """根据策略类型和参数配置创建策略实例。

    从全局策略注册表中查找对应的策略类，并用给定参数实例化。

    Args:
        strategy_type: 策略类型名称（如 'momentum', 'dca' 等）
        params: 策略参数字典
        universe: 基金池配置（列表或字典）

    Returns:
        策略实例

    Raises:
        ValueError: 如果策略类型未注册或参数无效
    """
    from app.domain.strategy.registry import get_strategy_registry

    if strategy_type is None:
        raise ValueError("strategy_type 不能为空")

    # 别名映射：用户友好名称 → 注册表名称
    _ALIASES = {
        "dca": "fixed_amount_dca",
        "momentum": "momentum_rotation",
    }

    # timing 类型需要根据 params.method 决定具体策略
    if strategy_type == "timing":
        method = params.get("method", "dual_ma") if isinstance(params, dict) else "dual_ma"
        _TIMING_METHOD_MAP = {
            "dual_ma": "dual_ma",
            "macd": "macd_timing",
            "macd_timing": "macd_timing",
            "valuation": "valuation_timing",
            "valuation_timing": "valuation_timing",
        }
        resolved_type = _TIMING_METHOD_MAP.get(method, "dual_ma")
    else:
        resolved_type = _ALIASES.get(strategy_type, strategy_type)

    registry = get_strategy_registry()
    strategy_cls = registry.get(resolved_type)

    if strategy_cls is None:
        available = registry.list_names()
        raise ValueError(
            f"未知策略类型: {strategy_type}。可用类型: {', '.join(available)}"
        )

    # Normalize universe to list of fund codes
    if isinstance(universe, dict):
        fund_codes = universe.get("fund_codes", [])
    else:
        fund_codes = list(universe)

    # 对于 DCA 策略，确保 fund_code 参数设置正确
    if fund_codes and "fund_code" not in params:
        params = dict(params)
        params["fund_code"] = fund_codes[0]

    # 将 dict 参数转换为策略对应的 Params 类型
    params_obj: Any = params
    if isinstance(params, dict):
        # 策略类型 → Params 类映射
        _PARAMS_MAP: dict[str, type] = {}
        try:
            from app.domain.strategy.dca import DCAParams, SmartDCAParams, ValueAveragingParams
            _PARAMS_MAP["fixed_amount_dca"] = DCAParams
            _PARAMS_MAP["value_averaging_dca"] = ValueAveragingParams
            _PARAMS_MAP["smart_dca"] = SmartDCAParams
        except ImportError:
            pass

        try:
            from app.domain.strategy.mean_variance import MeanVarianceParams
            _PARAMS_MAP["mean_variance"] = MeanVarianceParams
        except ImportError:
            pass

        try:
            from app.domain.strategy.momentum import MomentumParams
            _PARAMS_MAP["momentum_rotation"] = MomentumParams
        except ImportError:
            pass

        try:
            from app.domain.strategy.risk_parity import RiskParityParams
            _PARAMS_MAP["risk_parity"] = RiskParityParams
        except ImportError:
            pass

        try:
            from app.domain.strategy.timing import DualMAParams, MACDParams, ValuationParams
            _PARAMS_MAP["dual_ma"] = DualMAParams
            _PARAMS_MAP["macd_timing"] = MACDParams
            _PARAMS_MAP["valuation_timing"] = ValuationParams
        except ImportError:
            pass

        params_cls = _PARAMS_MAP.get(resolved_type)
        if params_cls:
            # 参数名兼容映射：处理前端/数据库中使用的别名
            _PARAM_ALIASES: dict[str, dict[str, str]] = {
                "dual_ma": {
                    "fast_window": "short_window",
                    "slow_window": "long_window",
                },
            }
            aliases = _PARAM_ALIASES.get(resolved_type, {})
            if aliases:
                params = dict(params)
                for old_key, new_key in aliases.items():
                    if old_key in params and new_key not in params:
                        params[new_key] = params.pop(old_key)

            try:
                # 过滤掉不认识的参数（如 'method'）
                valid_fields = set(params_cls.model_fields.keys())
                filtered = {k: v for k, v in params.items() if k in valid_fields}
                params_obj = params_cls(**filtered)
            except (TypeError, ValueError):
                # 再次尝试只用有效字段
                import inspect
                valid_fields = set(params_cls.model_fields.keys())
                filtered = {k: v for k, v in params.items() if k in valid_fields}
                params_obj = params_cls(**filtered)

    # 实例化策略
    try:
        instance = strategy_cls(params=params_obj, universe=fund_codes)
    except (TypeError, Exception):
        try:
            instance = strategy_cls(universe=fund_codes)
        except TypeError:
            instance = strategy_cls()
            instance.universe = fund_codes

    return instance
