"""交易建议引擎 — 综合多维度分析生成买卖建议（v5 智能增强版）。

核心功能：
1. 技术指标分析（MA/EMA/MACD/RSI/布林带）— 仅对 ETF/LOF/指数基金有效
2. 动量/均值回复分析（替代原有的"估值分位数"伪估值）
3. 策略信号整合（已有策略的 on_bar 输出）
4. 截面因子选基信号（v4 新增，替代 Bootstrap 预测）
5. Bootstrap 预测（v5 恢复参与决策，降低但非零权重）
6. 风险预算仓位计算（替代 Kelly，适用于非独立收益）
7. 市场 regime 检测（v5 改进：温和调整，不过度压制信号）
8. 组合相关性分析（避免同质化持仓）
9. 信号冷却与去重（避免重复操作）
10. 自适应权重重分配（v5 新增：信号源不可用时动态调整）
11. 动态阈值（v5 新增：根据可用信号强度自适应调整买卖阈值）

v5 智能增强要点：
- 自适应权重重分配：当策略信号/截面因子不可用时，权重自动分配给可用信号源
- 动态阈值：根据有效信号源数量和强度自动降低买卖阈值，避免"永远 hold"
- Bootstrap 预测恢复参与：权重 0.10~0.15，提供概率视角补充
- Regime 调整温和化：最低乘数从 0.3 提升到 0.5，hold_bias 上限降低
- 动量折扣温和化：最低折扣从 0.3 提升到 0.5，减少过度压制
- 信号新鲜度半衰期延长：从 7 天延长到 14 天，适配低频策略
- 技术分析对所有基金启用（非 ETF 降权但不归零）
- 非线性信号增强：极端一致信号获得额外加成

v4 截面因子增强要点（保留）：
- 引入截面因子选基模型：从"单基金时序预测"转向"多基金截面排序"
- 截面因子包括：Alpha持续性、Sharpe持续性、规模、费率、回撤恢复、一致性
- 学术依据：Carhart(1997), Berk&Green(2004), Fama&French(2010)

v3 增强要点（保留）：
- 修正风险预算模型：引入基金间相关性假设
- 市场 regime 检测
- 信号冷却机制
- 组合相关性检查
- 动量因子 A 股有效性自适应折扣

v2 修正要点（保留）：
- 动量/均值回复分析
- 风险预算模型
- Block Bootstrap
- 按基金类型区分分析逻辑和权重
- 交易费用估算
- Wilder 平滑 RSI

设计原则：
- 有效信号优先：确保可用信号源能充分发挥作用
- 动态适应：根据数据可用性自动调整策略
- 多信号融合 + 非线性增强
- 风险预算控制仓位，避免过度集中
- 区分基金类型，不同产品不同策略
- 市场 regime 温和自适应（不过度压制信号）
- 所有建议附带详细理由和局限性说明
- 明确标注"不构成投资建议"
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

logger = logging.getLogger(__name__)


async def _rollback_session_if_possible(session: Any) -> None:
    """Clear an aborted DB transaction before Advisor continues with fallbacks."""
    rollback = getattr(session, "rollback", None)
    if rollback is None:
        return
    try:
        await rollback()
    except Exception:
        pass


_DIRECTION_ALIASES: dict[str, str] = {
    "buy": "subscribe",
    "subscribe": "subscribe",
    "sell": "redeem",
    "redeem": "redeem",
    "hold": "hold",
}


# ---------------------------------------------------------------------------
# 基金类型分类与配置
# ---------------------------------------------------------------------------

# 基金类型到分析模式的映射
# v5: 所有类型启用技术分析（非 ETF 降权但不归零），恢复 Bootstrap 权重
FUND_TYPE_PROFILES: dict[str, dict[str, Any]] = {
    "stock": {
        "label": "股票型",
        "technical_applicable": True,  # v5: 启用但降权
        "weight_technical": 0.10,  # v5: 非零，提供趋势参考
        "weight_momentum": 0.25,  # v5: 提升，动量是最可靠的时序信号
        "weight_strategy": 0.25,  # v5: 降低对策略信号的依赖
        "weight_prediction": 0.10,  # v5: 恢复 Bootstrap 概率视角
        "weight_cross_sectional": 0.30,  # v5: 仍为主力但不过度依赖
        "typical_subscribe_fee": 0.0015,
        "typical_redeem_fee": 0.005,
        "settlement_days": 2,
    },
    "index": {
        "label": "指数型",
        "technical_applicable": True,
        "weight_technical": 0.25,  # 指数基金技术分析最有效
        "weight_momentum": 0.30,  # 指数动量比主动基金更稳定
        "weight_strategy": 0.20,
        "weight_prediction": 0.15,  # v5: Bootstrap 对指数基金较有效
        "weight_cross_sectional": 0.10,
        "typical_subscribe_fee": 0.0012,
        "typical_redeem_fee": 0.005,
        "settlement_days": 2,
    },
    "bond": {
        "label": "债券型",
        "technical_applicable": True,  # v5: 启用但降权
        "weight_technical": 0.05,
        "weight_momentum": 0.25,  # v5: 债券动量相对稳定
        "weight_strategy": 0.30,
        "weight_prediction": 0.10,
        "weight_cross_sectional": 0.30,
        "typical_subscribe_fee": 0.0008,
        "typical_redeem_fee": 0.0015,
        "settlement_days": 1,
    },
    "mixed": {
        "label": "混合型",
        "technical_applicable": True,  # v5: 启用但降权
        "weight_technical": 0.10,
        "weight_momentum": 0.20,
        "weight_strategy": 0.25,
        "weight_prediction": 0.10,
        "weight_cross_sectional": 0.35,
        "typical_subscribe_fee": 0.0015,
        "typical_redeem_fee": 0.005,
        "settlement_days": 2,
    },
    "money": {
        "label": "货币型",
        "technical_applicable": False,
        "weight_technical": 0.0,
        "weight_momentum": 0.0,
        "weight_strategy": 1.00,  # 货币基金完全依赖策略信号
        "weight_prediction": 0.0,
        "weight_cross_sectional": 0.0,
        "typical_subscribe_fee": 0.0,
        "typical_redeem_fee": 0.0,
        "settlement_days": 0,
    },
    "qdii": {
        "label": "QDII",
        "technical_applicable": True,  # v5: 启用但降权
        "weight_technical": 0.10,
        "weight_momentum": 0.30,  # v5: QDII 动量因子有效性较高（海外市场）
        "weight_strategy": 0.30,
        "weight_prediction": 0.15,  # v5: 海外市场 Bootstrap 更有效
        "weight_cross_sectional": 0.15,
        "typical_subscribe_fee": 0.0015,
        "typical_redeem_fee": 0.005,
        "settlement_days": 7,
    },
    "fof": {
        "label": "FOF",
        "technical_applicable": True,  # v5: 启用但降权
        "weight_technical": 0.05,
        "weight_momentum": 0.20,
        "weight_strategy": 0.35,
        "weight_prediction": 0.10,
        "weight_cross_sectional": 0.30,
        "typical_subscribe_fee": 0.0,
        "typical_redeem_fee": 0.005,
        "settlement_days": 2,
    },
}

# ETF/LOF 子类型关键词（这些适用技术分析）
ETF_LOF_KEYWORDS = ("etf", "lof", "交易型", "上市型")

DEFAULT_PROFILE = FUND_TYPE_PROFILES["mixed"]  # 未知类型使用混合型配置


# ---------------------------------------------------------------------------
# 数据结构定义
# ---------------------------------------------------------------------------


@dataclass
class TechnicalIndicators:
    """技术指标计算结果。"""

    # 均线系统
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    ma120: float | None = None
    ma250: float | None = None

    # MACD
    macd_dif: float | None = None
    macd_dea: float | None = None
    macd_histogram: float | None = None
    macd_signal: str = "neutral"  # bullish/bearish/neutral

    # RSI (Wilder 平滑)
    rsi_6: float | None = None
    rsi_14: float | None = None
    rsi_signal: str = "neutral"  # overbought/oversold/neutral

    # 布林带
    boll_upper: float | None = None
    boll_middle: float | None = None
    boll_lower: float | None = None
    boll_position: float | None = None  # 当前价格在布林带中的位置 (0~1)

    # 趋势强度
    trend_score: float = 0.0  # -1 到 1，正为上升趋势

    # 适用性标记
    applicable: bool = True  # 技术分析是否适用于该基金类型


@dataclass
class MomentumAnalysis:
    """动量/均值回复分析结果（替代原有的伪估值分析）。

    说明：
    - 不再使用"净值百分位=估值"的错误逻辑
    - 改为分析收益率的动量和均值回复特征
    - 短期动量 + 长期均值回复 = 更合理的信号
    """

    # 动量指标
    return_5d: float | None = None   # 5日收益率
    return_20d: float | None = None  # 20日收益率
    return_60d: float | None = None  # 60日收益率
    return_120d: float | None = None  # 120日收益率

    # 均值回复指标
    zscore_20d: float | None = None  # 20日收益率 z-score
    zscore_60d: float | None = None  # 60日收益率 z-score

    # 波动率状态
    current_vol: float | None = None  # 当前20日年化波动率
    vol_percentile: float | None = None  # 波动率在历史中的百分位

    # 综合评分
    momentum_score: float = 0.0  # -1 到 1
    regime: str = "normal"  # trending_up/trending_down/mean_reverting/normal


@dataclass
class RiskBudgetPosition:
    """风险预算仓位计算结果（替代 Kelly Criterion）。

    说明：
    - Kelly 公式要求独立重复博弈，基金日收益率不满足此假设
    - 改用风险预算模型：基于波动率和相关性分配仓位
    - 目标：控制单只基金对组合风险的贡献
    """

    annualized_vol: float = 0.0  # 年化波动率
    max_drawdown_1y: float = 0.0  # 近1年最大回撤
    risk_budget_pct: float = 0.0  # 风险预算分配比例
    suggested_position_pct: float = 0.0  # 建议仓位占比
    suggested_amount: float = 0.0  # 建议交易金额
    risk_contribution: float = 0.0  # 预期风险贡献


@dataclass
class PredictionRef:
    """Bootstrap 预测参考（替代 GBM，不假设正态分布）。

    说明：
    - 不再假设收益率服从正态分布
    - 使用 block bootstrap 保留自相关结构
    - 所有预测结果均为条件概率估计，不是确定性预测
    - 适用条件：未来市场环境与历史样本期相似
    - 失效条件：市场 regime 切换、黑天鹅事件、政策突变
    """

    expected_return_30d: float | None = None  # 30日预期收益（中位数）
    expected_return_90d: float | None = None  # 90日预期收益（中位数）
    prob_positive_30d: float | None = None  # 30日正收益概率
    prob_positive_90d: float | None = None  # 90日正收益概率
    var_95_30d: float | None = None  # 30日 95% VaR
    cvar_95_30d: float | None = None  # 30日 95% CVaR (Expected Shortfall)
    prediction_score: float = 0.0  # -1 到 1
    confidence_band_width: float | None = None  # 90%置信区间宽度（衡量不确定性）
    sample_size: int = 0  # 用于估计的样本量


@dataclass
class FeeEstimate:
    """交易费用估算。"""

    subscribe_fee_rate: float = 0.0  # 申购费率
    redeem_fee_rate: float = 0.0  # 赎回费率
    estimated_fee: float = 0.0  # 预估费用（元）
    fee_impact_pct: float = 0.0  # 费用对收益的影响（百分比）
    net_trade_amount: float | None = None  # 扣费后实际申购/到账金额
    fee_source: str | None = None  # 费用来源：tiered/db/profile


@dataclass
class ReasonFactor:
    """结构化建议因子，用于前端解释决策链。"""

    name: str
    impact: str = "neutral"  # positive/negative/neutral
    score: float | None = None
    explanation: str = ""


@dataclass
class AdviceReasoning:
    """结构化建议解释。"""

    summary: str = ""
    confidence_level: str = "medium"  # high/medium/low
    factors: list[ReasonFactor] = field(default_factory=list)


@dataclass
class TradePlanTrigger:
    """交易计划中的条件触发规则。"""

    trigger_type: str = "review"  # pause_buy/stop_buy/reduce_position/review/refresh
    condition: str = ""
    action: str = ""
    reason: str = ""
    severity: str = "info"  # info/warning/high


@dataclass
class TradePlan:
    """交易执行计划。"""

    execution_type: str = "hold"  # one_time/batch/fixed_investment/hold
    suggested_amount: float = 0.0
    min_amount: float = 0.0
    max_amount: float = 0.0
    current_weight: float = 0.0
    target_weight: float = 0.0
    batch_count: int | None = None
    batch_interval_days: int | None = None
    explanation: str = ""
    triggers: list[TradePlanTrigger] = field(default_factory=list)


@dataclass
class PortfolioImpact:
    """单条建议对组合的影响估算。"""

    before_weight: float = 0.0
    after_weight: float = 0.0
    position_change: float = 0.0
    risk_change: str = "unchanged"  # increase/decrease/unchanged
    concentration_warning: str | None = None
    explanation: str = ""


@dataclass
class SuitabilityCheck:
    """投资者适当性匹配结果。"""

    user_risk_level: str = "moderate"
    fund_risk_level: str = "R3"
    matched: bool = True
    action_adjusted: bool = False
    warning: str | None = None


@dataclass
class ProfileConstraint:
    """用户投资画像对建议的约束或调整。"""

    name: str
    triggered: bool = False
    effect: str = "none"  # none/reduce_amount/hold/warning
    explanation: str = ""


@dataclass
class ReliabilityAdjustment:
    """基于样本外/健康度的防过拟合可靠性调整。"""

    status: str = "not_evaluated"  # healthy/degraded/unhealthy/insufficient_data/unknown/not_evaluated
    multiplier: float = 1.0
    confidence_multiplier: float = 1.0
    amount_multiplier: float = 1.0
    reason: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdviceValidity:
    """建议有效期与失效条件。"""

    generated_at: str = ""
    data_as_of: str = ""
    valid_until: str = ""
    invalidation_rules: list[str] = field(default_factory=list)


@dataclass
class AdvisorDataQualityReport:
    """单只基金 Advisor 输入数据质量报告。"""

    status: str = "unknown"  # good/warning/poor/unknown
    score: float = 0.0
    nav_count: int = 0
    data_start: str | None = None
    data_end: str | None = None
    sample_sufficient: bool = False
    prediction_sample_size: int = 0
    coverage_ratio: float | None = None
    max_gap_days: int = 0
    spike_count: int = 0
    spike_dates: list[str] = field(default_factory=list)
    freshness_days: int | None = None
    warnings: list[str] = field(default_factory=list)
    current_volatility: float | None = None
    volatility_percentile: float | None = None
    source_consistency: dict[str, Any] = field(default_factory=dict)
    adjustment_consistency: dict[str, Any] = field(default_factory=dict)
    cross_source_consistency: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "score": round(float(self.score), 4),
            "nav_count": self.nav_count,
            "data_start": self.data_start,
            "data_end": self.data_end,
            "sample_sufficient": self.sample_sufficient,
            "prediction_sample_size": self.prediction_sample_size,
            "coverage_ratio": round(self.coverage_ratio, 4) if self.coverage_ratio is not None else None,
            "max_gap_days": self.max_gap_days,
            "spike_count": self.spike_count,
            "spike_dates": self.spike_dates[:10],
            "freshness_days": self.freshness_days,
            "warnings": self.warnings,
            "current_volatility": self.current_volatility,
            "volatility_percentile": self.volatility_percentile,
            "source_consistency": self.source_consistency,
            "adjustment_consistency": self.adjustment_consistency,
            "cross_source_consistency": self.cross_source_consistency,
        }


@dataclass
class AdvisorOverfitRisk:
    """Advisor 级过拟合风险评估。"""

    level: str = "unknown"  # low/medium/high/unknown
    score: float = 0.0
    pbo: float | None = None
    cpcv_n_paths: int = 0
    cpcv_avg_oos_sharpe: float | None = None
    cpcv_std_oos_sharpe: float | None = None
    cpcv_avg_is_sharpe: float | None = None
    oos_ic: float | None = None
    ic_degradation: float | None = None
    oos_signal_count: int = 0
    engine_health_status: str | None = None
    rolling_ic_samples: int = 0
    reasons: list[str] = field(default_factory=list)
    gate_action: str = "allow"  # allow/reduce/hold

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "score": round(float(self.score), 4),
            "pbo": self.pbo,
            "cpcv_n_paths": self.cpcv_n_paths,
            "cpcv_avg_oos_sharpe": self.cpcv_avg_oos_sharpe,
            "cpcv_std_oos_sharpe": self.cpcv_std_oos_sharpe,
            "cpcv_avg_is_sharpe": self.cpcv_avg_is_sharpe,
            "oos_ic": self.oos_ic,
            "ic_degradation": self.ic_degradation,
            "oos_signal_count": self.oos_signal_count,
            "engine_health_status": self.engine_health_status,
            "rolling_ic_samples": self.rolling_ic_samples,
            "reasons": self.reasons,
            "gate_action": self.gate_action,
        }


@dataclass
class DecisionAudit:
    """单条建议的决策审计信息，便于追溯模型如何得到当前结论。"""

    effective_buy_threshold: float = 0.0
    effective_sell_threshold: float = 0.0
    threshold_state: str = "within_hold_band"
    threshold_margin: float = 0.0
    missing_sources: int = 0
    signal_weights: dict[str, float] = field(default_factory=dict)
    signal_availability: dict[str, bool] = field(default_factory=dict)
    signal_contributions: list[dict[str, Any]] = field(default_factory=list)
    dominant_signal: dict[str, Any] | None = None
    data_quality: dict[str, Any] = field(default_factory=dict)
    overfit_risk: dict[str, Any] = field(default_factory=dict)
    market_regime: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class FundTradeTiming:
    """基金申赎交易时点估算。"""

    request_time: str = ""  # 北京时间 ISO 字符串
    timezone: str = "Asia/Shanghai"
    cutoff_time: str = "15:00:00"
    is_trading_day: bool = False
    is_after_cutoff: bool = False
    accepted_trade_date: str = ""  # 实际受理 T 日
    nav_date: str = ""  # 净值归属日
    expected_confirm_date: str | None = None  # 预计份额/赎回确认日
    expected_settlement_date: str | None = None  # 预计清算/到账日
    expected_available_date: str | None = None  # 预计份额可用/资金可用日
    fund_type: str | None = None
    trade_intent: str = "hold"  # subscribe/redeem/hold
    rule_basis: str = ""
    calendar_source: str = "A股交易日历"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 API 响应格式。"""
        return {
            "request_time": self.request_time,
            "timezone": self.timezone,
            "cutoff_time": self.cutoff_time,
            "is_trading_day": self.is_trading_day,
            "is_after_cutoff": self.is_after_cutoff,
            "accepted_trade_date": self.accepted_trade_date,
            "nav_date": self.nav_date,
            "expected_confirm_date": self.expected_confirm_date,
            "expected_settlement_date": self.expected_settlement_date,
            "expected_available_date": self.expected_available_date,
            "fund_type": self.fund_type,
            "trade_intent": self.trade_intent,
            "rule_basis": self.rule_basis,
            "calendar_source": self.calendar_source,
            "warnings": self.warnings,
        }


@dataclass
class TradingAdvice:
    """单只基金的交易建议。"""

    fund_code: str
    fund_name: str | None = None
    fund_type: str | None = None
    advice_date: str = ""

    # 核心建议
    action: str = "hold"  # buy/sell/hold/watch；兼容旧接口
    support_action: str = "hold_review"  # increase_watch/reduce_watch/hold_review/risk_alert
    support_label: str = "持有复核"
    decision_support_only: bool = True
    not_investment_advice_disclaimer: str = "仅供个人研究与决策辅助，不构成投资建议、收益承诺或自动下单指令。"
    confidence_calibration_status: str = "uncalibrated"  # calibrated/weak/uncalibrated
    oos_validation_status: str = "not_available"  # available/weak/not_available
    worst_case_note: str | None = None
    strength: str = "weak"  # weak/medium/strong 建议强度
    confidence: float = 0.0  # 0~1 置信度
    urgency: str = "normal"  # high/normal/low 紧迫程度

    # 金额/份额建议
    suggested_amount: float = 0.0  # 建议交易金额（元）
    suggested_shares: float | None = None  # 建议赎回份额（份）
    estimated_gross_amount: float | None = None  # 预计赎回总额（未扣费）
    estimated_net_amount: float | None = None  # 预计到账金额（扣费后）
    suggested_pct: float = 0.0  # 建议占总资金比例
    position_after: float = 0.0  # 操作后目标仓位比例

    # 费用估算
    fee_estimate: FeeEstimate | None = None

    # 交易时点估算
    trade_timing: FundTradeTiming | None = None

    # 综合评分（各维度 -1 到 1）
    technical_score: float = 0.0
    momentum_score: float = 0.0
    strategy_score: float = 0.0
    prediction_score: float = 0.0
    cross_sectional_score: float = 0.0  # v4: 截面因子信号
    composite_score: float = 0.0  # 加权综合分

    # 详细分析
    technical: TechnicalIndicators | None = None
    momentum: MomentumAnalysis | None = None
    risk_position: RiskBudgetPosition | None = None
    prediction: PredictionRef | None = None

    # 理由
    reasons: list[str] = field(default_factory=list)
    risk_warnings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)  # 模型局限性说明

    # 结构化专业解释（向后兼容：不替代上面的字符串列表）
    reasoning: AdviceReasoning | None = None
    trade_plan: TradePlan | None = None
    portfolio_impact: PortfolioImpact | None = None
    suitability: SuitabilityCheck | None = None
    profile_constraints: list[ProfileConstraint] = field(default_factory=list)
    reliability_adjustment: ReliabilityAdjustment | None = None
    validity: AdviceValidity | None = None
    decision_audit: DecisionAudit | None = None
    data_quality: AdvisorDataQualityReport | None = None
    overfit_risk: AdvisorOverfitRisk | None = None
    risk_constraints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 API 响应格式。"""
        trade_intent = "hold"
        if self.action == "buy":
            trade_intent = "subscribe"
        elif self.action == "sell":
            trade_intent = "redeem"

        trade_amount_min = self.trade_plan.min_amount if self.trade_plan is not None else self.suggested_amount
        trade_amount_max = self.trade_plan.max_amount if self.trade_plan is not None else self.suggested_amount

        execution_notes: list[str] = []
        if self.trade_plan is not None and self.trade_plan.explanation:
            execution_notes.append(self.trade_plan.explanation)
        if self.validity is not None:
            for rule in self.validity.invalidation_rules:
                if rule and rule not in execution_notes:
                    execution_notes.append(rule)
        for warning in self.risk_warnings:
            if warning and warning not in execution_notes:
                execution_notes.append(warning)

        self._sync_decision_support_fields()

        result: dict[str, Any] = {
            "fund_code": self.fund_code,
            "fund_name": self.fund_name,
            "fund_type": self.fund_type,
            "advice_date": self.advice_date,
            "action": self.action,
            "support_action": self.support_action,
            "support_label": self.support_label,
            "decision_support_only": self.decision_support_only,
            "not_investment_advice_disclaimer": self.not_investment_advice_disclaimer,
            "confidence_calibration_status": self.confidence_calibration_status,
            "oos_validation_status": self.oos_validation_status,
            "worst_case_note": self.worst_case_note,
            "strength": self.strength,
            "trade_intent": trade_intent,
            "confidence": round(self.confidence, 4),
            "urgency": self.urgency,
            "suggested_amount": round(self.suggested_amount, 2),
            "suggested_shares": (
                round(self.suggested_shares, 4)
                if self.suggested_shares is not None else None
            ),
            "estimated_gross_amount": (
                round(self.estimated_gross_amount, 2)
                if self.estimated_gross_amount is not None else None
            ),
            "estimated_net_amount": (
                round(self.estimated_net_amount, 2)
                if self.estimated_net_amount is not None else None
            ),
            "suggested_pct": round(self.suggested_pct, 4),
            "position_after": round(self.position_after, 4),
            "trade_amount_min": round(trade_amount_min, 2),
            "trade_amount_max": round(trade_amount_max, 2),
            "execution_notes": execution_notes,
            "scores": {
                "technical": round(self.technical_score, 4),
                "momentum": round(self.momentum_score, 4),
                "strategy": round(self.strategy_score, 4),
                "prediction": round(self.prediction_score, 4),
                "cross_sectional": round(self.cross_sectional_score, 4),
                "composite": round(self.composite_score, 4),
            },
            "reasons": self.reasons,
            "risk_warnings": self.risk_warnings,
            "limitations": self.limitations,
        }
        if self.data_quality:
            result["data_quality"] = self.data_quality.to_dict()
        if self.overfit_risk:
            result["overfit_risk"] = self.overfit_risk.to_dict()
        if self.risk_constraints:
            result["risk_constraints"] = self.risk_constraints
        if self.reasoning:
            result["reasoning"] = {
                "summary": self.reasoning.summary,
                "confidence_level": self.reasoning.confidence_level,
                "factors": [
                    {
                        "name": f.name,
                        "impact": f.impact,
                        "score": round(f.score, 4) if f.score is not None else None,
                        "explanation": f.explanation,
                    }
                    for f in self.reasoning.factors
                ],
            }
        if self.trade_plan:
            result["trade_plan"] = {
                "execution_type": self.trade_plan.execution_type,
                "suggested_amount": round(self.trade_plan.suggested_amount, 2),
                "min_amount": round(self.trade_plan.min_amount, 2),
                "max_amount": round(self.trade_plan.max_amount, 2),
                "current_weight": round(self.trade_plan.current_weight, 4),
                "target_weight": round(self.trade_plan.target_weight, 4),
                "batch_count": self.trade_plan.batch_count,
                "batch_interval_days": self.trade_plan.batch_interval_days,
                "explanation": self.trade_plan.explanation,
                "triggers": [
                    {
                        "trigger_type": trigger.trigger_type,
                        "condition": trigger.condition,
                        "action": trigger.action,
                        "reason": trigger.reason,
                        "severity": trigger.severity,
                    }
                    for trigger in self.trade_plan.triggers
                ],
            }
        if self.portfolio_impact:
            result["portfolio_impact"] = {
                "before_weight": round(self.portfolio_impact.before_weight, 4),
                "after_weight": round(self.portfolio_impact.after_weight, 4),
                "position_change": round(self.portfolio_impact.position_change, 4),
                "risk_change": self.portfolio_impact.risk_change,
                "concentration_warning": self.portfolio_impact.concentration_warning,
                "explanation": self.portfolio_impact.explanation,
            }
        if self.suitability:
            result["suitability"] = {
                "user_risk_level": self.suitability.user_risk_level,
                "fund_risk_level": self.suitability.fund_risk_level,
                "matched": self.suitability.matched,
                "action_adjusted": self.suitability.action_adjusted,
                "warning": self.suitability.warning,
            }
        if self.profile_constraints:
            result["profile_constraints"] = [
                {
                    "name": c.name,
                    "triggered": c.triggered,
                    "effect": c.effect,
                    "explanation": c.explanation,
                }
                for c in self.profile_constraints
            ]
        if self.reliability_adjustment:
            result["reliability_adjustment"] = {
                "status": self.reliability_adjustment.status,
                "multiplier": round(self.reliability_adjustment.multiplier, 4),
                "confidence_multiplier": round(self.reliability_adjustment.confidence_multiplier, 4),
                "amount_multiplier": round(self.reliability_adjustment.amount_multiplier, 4),
                "reason": self.reliability_adjustment.reason,
                "metrics": self.reliability_adjustment.metrics,
            }
        if self.validity:
            result["validity"] = {
                "generated_at": self.validity.generated_at,
                "data_as_of": self.validity.data_as_of,
                "valid_until": self.validity.valid_until,
                "invalidation_rules": self.validity.invalidation_rules,
            }
        if self.decision_audit:
            result["decision_audit"] = {
                "effective_buy_threshold": round(self.decision_audit.effective_buy_threshold, 4),
                "effective_sell_threshold": round(self.decision_audit.effective_sell_threshold, 4),
                "threshold_state": self.decision_audit.threshold_state,
                "threshold_margin": round(self.decision_audit.threshold_margin, 4),
                "missing_sources": self.decision_audit.missing_sources,
                "signal_weights": {
                    k: round(v, 4) for k, v in self.decision_audit.signal_weights.items()
                },
                "signal_availability": self.decision_audit.signal_availability,
                "signal_contributions": self.decision_audit.signal_contributions,
                "dominant_signal": self.decision_audit.dominant_signal,
                "data_quality": self.decision_audit.data_quality,
                "overfit_risk": self.decision_audit.overfit_risk,
                "market_regime": self.decision_audit.market_regime,
                "notes": self.decision_audit.notes,
            }
        if self.trade_timing:
            result["trade_timing"] = self.trade_timing.to_dict()
        if self.fee_estimate:
            result["fee_estimate"] = {
                "subscribe_fee_rate": round(self.fee_estimate.subscribe_fee_rate, 6),
                "redeem_fee_rate": round(self.fee_estimate.redeem_fee_rate, 6),
                "estimated_fee": round(self.fee_estimate.estimated_fee, 2),
                "fee_impact_pct": round(self.fee_estimate.fee_impact_pct, 4),
                "net_trade_amount": (
                    round(self.fee_estimate.net_trade_amount, 2)
                    if self.fee_estimate.net_trade_amount is not None else None
                ),
                "fee_source": self.fee_estimate.fee_source,
            }
        if self.technical and self.technical.applicable:
            result["technical_indicators"] = {
                "ma5": self.technical.ma5,
                "ma20": self.technical.ma20,
                "ma60": self.technical.ma60,
                "macd_signal": self.technical.macd_signal,
                "rsi_14": self.technical.rsi_14,
                "rsi_signal": self.technical.rsi_signal,
                "boll_position": self.technical.boll_position,
                "trend_score": round(self.technical.trend_score, 4),
            }
        if self.momentum:
            result["momentum_analysis"] = {
                "return_5d": self.momentum.return_5d,
                "return_20d": self.momentum.return_20d,
                "return_60d": self.momentum.return_60d,
                "zscore_20d": self.momentum.zscore_20d,
                "current_vol": self.momentum.current_vol,
                "vol_percentile": self.momentum.vol_percentile,
                "regime": self.momentum.regime,
            }
        if self.risk_position:
            result["risk_position"] = {
                "annualized_vol": round(self.risk_position.annualized_vol, 4),
                "max_drawdown_1y": round(self.risk_position.max_drawdown_1y, 4),
                "risk_budget_pct": round(self.risk_position.risk_budget_pct, 4),
                "suggested_position_pct": round(self.risk_position.suggested_position_pct, 4),
                "suggested_amount": round(self.risk_position.suggested_amount, 2),
            }
        if self.prediction:
            result["prediction"] = {
                "expected_return_30d": self.prediction.expected_return_30d,
                "expected_return_90d": self.prediction.expected_return_90d,
                "prob_positive_30d": self.prediction.prob_positive_30d,
                "prob_positive_90d": self.prediction.prob_positive_90d,
                "var_95_30d": self.prediction.var_95_30d,
                "cvar_95_30d": self.prediction.cvar_95_30d,
                "confidence_band_width": self.prediction.confidence_band_width,
                "sample_size": self.prediction.sample_size,
                "note": "基于历史数据的条件概率估计，假设未来市场环境与样本期相似",
            }
        return result

    def _sync_decision_support_fields(self) -> None:
        """将旧 action 字段映射为降调后的决策支持语义。"""
        oos_metrics = self.reliability_adjustment.metrics if self.reliability_adjustment else {}
        oos_signals = int(oos_metrics.get("oos_total_signals") or 0) if isinstance(oos_metrics, dict) else 0
        oos_ic = oos_metrics.get("oos_avg_ic") if isinstance(oos_metrics, dict) else None
        if oos_signals > 0:
            if oos_signals >= 20 and oos_ic is not None and float(oos_ic) >= 0.05:
                self.oos_validation_status = "available"
            elif oos_signals >= 20:
                self.oos_validation_status = "weak"
            else:
                self.oos_validation_status = "not_available"
        self.confidence_calibration_status = "calibrated" if self.oos_validation_status == "available" else "weak" if self.oos_validation_status == "weak" else "uncalibrated"

        if self.prediction and self.prediction.cvar_95_30d is not None:
            self.worst_case_note = f"历史 Bootstrap 条件估计下，30日 95% CVaR 约 {self.prediction.cvar_95_30d:.1%}，极端市场可能更差。"
        elif self.risk_position and self.risk_position.max_drawdown_1y:
            self.worst_case_note = f"近一年最大回撤约 {abs(self.risk_position.max_drawdown_1y):.1%}，未来极端回撤可能超过历史。"
        else:
            self.worst_case_note = "缺少足够极端损失样本，需自行预设可承受亏损边界。"

        if self.data_quality and self.data_quality.status == "poor":
            self.support_action = "risk_alert"
            self.support_label = "数据风险警示"
            return
        if self.overfit_risk and self.overfit_risk.level == "high":
            self.support_action = "risk_alert"
            self.support_label = "样本外风险警示"
            return
        if self.action == "buy":
            self.support_action = "increase_watch"
            self.support_label = "增配观察候选"
        elif self.action == "sell":
            self.support_action = "reduce_watch"
            self.support_label = "减配观察候选"
        else:
            self.support_action = "hold_review"
            self.support_label = "持有复核"


@dataclass
class MarketRegime:
    """市场 regime 检测结果。

    基于宽基指数或基金自身的波动率和趋势双维度判断市场状态。
    用于自适应调整各模块的信号权重。
    """

    regime: str = "normal"  # bull/bear/crisis/volatile/normal
    volatility_state: str = "normal"  # low/normal/high/extreme
    trend_state: str = "neutral"  # strong_up/up/neutral/down/strong_down
    regime_confidence: float = 0.5  # regime 判断的置信度
    signal_weight_multiplier: float = 1.0  # 信号权重乘数（crisis 时降低）
    hold_bias: float = 0.0  # 倾向 hold 的偏移量（高不确定性时增加）


@dataclass
class AdvisorConfig:
    """交易建议引擎配置（v5 增强）。"""

    # 交易阈值（v5: 这些是基础阈值，实际阈值会根据信号可用性动态调整）
    buy_threshold: float = 0.20  # 综合分 > 此值触发买入建议
    sell_threshold: float = -0.20  # 综合分 < 此值触发卖出建议
    high_confidence_threshold: float = 0.5  # 高置信度阈值
    min_trade_amount: float = 100.0  # 最小交易金额

    # v5: 动态阈值参数
    adaptive_threshold: bool = True  # 是否启用动态阈值
    min_threshold: float = 0.08  # 动态阈值下限（进一步降低）
    threshold_decay_per_missing_source: float = 0.07  # 每缺少一个信号源，阈值降低多少

    # 风控参数
    max_single_position: float = 0.30  # 单只基金最大仓位
    max_daily_trade_pct: float = 0.20  # 单日最大交易占比
    target_portfolio_vol: float = 0.10  # 目标组合年化波动率
    avg_fund_correlation: float = 0.6  # 基金间平均相关性假设（A股基金通常0.5~0.8）

    # 技术指标参数
    rsi_oversold: float = 30.0  # RSI 超卖阈值
    rsi_overbought: float = 70.0  # RSI 超买阈值
    lookback_days: int = 750  # 历史回看天数（约3年）

    # Bootstrap 参数
    bootstrap_n_simulations: int = 5000  # 模拟次数
    bootstrap_block_size: int = 0  # 块大小，0=自适应选择
    bootstrap_min_block: int = 3  # 自适应块大小下限
    bootstrap_max_block: int = 15  # 自适应块大小上限

    # 信号冷却参数
    signal_cooldown_days: int = 5  # 同方向信号冷却期（天）
    cooldown_decay_factor: float = 0.7  # v5: 从 0.5 提升到 0.7，冷却更温和

    # 组合相关性控制
    correlation_threshold: float = 0.8  # 高相关性阈值
    max_correlated_buys: int = 2  # v5: 从 1 提升到 2，允许更多操作

    # 动量因子 A 股衰减
    momentum_ashare_discount: float = 0.7  # A股动量因子有效性折扣（2017年后）

    # 费用相关
    include_fee_estimate: bool = True  # 是否估算费用

    # v5: 信号新鲜度
    signal_freshness_half_life: float = 14.0  # 策略信号半衰期（天），从 7 延长到 14

    # v5: 非线性信号增强
    signal_consensus_boost: float = 1.3  # 多信号一致时的加成系数
    signal_consensus_threshold: int = 3  # 至少 N 个信号同方向才触发加成

    # 防过拟合：使用历史跟踪/样本外健康度对信号做可靠性折扣
    enable_reliability_adjustment: bool = True
    reliability_min_samples: int = 30
    reliability_insufficient_data_multiplier: float = 0.85
    reliability_degraded_multiplier: float = 0.75
    reliability_unhealthy_multiplier: float = 0.45
    reliability_unknown_multiplier: float = 0.70
    reliability_critical_trend_multiplier: float = 0.85
    reliability_declining_trend_multiplier: float = 0.92
    oos_reliability_enabled: bool = True
    oos_min_signals: int = 20
    oos_good_ic: float = 0.05
    oos_weak_ic: float = 0.02
    oos_bad_degradation: float = 0.50
    oos_critical_degradation: float = 0.30
    oos_weak_multiplier: float = 0.80
    oos_bad_multiplier: float = 0.65
    oos_critical_multiplier: float = 0.45


USER_RISK_LEVEL_MAP = {
    "conservative": 2,
    "moderate": 3,
    "aggressive": 5,
}

FUND_TYPE_RISK_LEVEL_MAP = {
    "money": 1,
    "bond": 2,
    "fof": 3,
    "mixed": 3,
    "index": 4,
    "stock": 4,
    "qdii": 4,
}

RISK_ASSET_TYPES = {"stock", "mixed", "index", "qdii"}


# ---------------------------------------------------------------------------
# 技术指标计算
# ---------------------------------------------------------------------------


def compute_technical_indicators(
    nav_values: list[float],
    config: AdvisorConfig | None = None,
    fund_type: str | None = None,
    sub_type: str | None = None,
) -> TechnicalIndicators:
    """计算技术指标。

    注意：技术分析对开放式基金的适用性有限。
    仅对 ETF、LOF、指数基金等与市场指数高度相关的品种有较好参考价值。
    对主动管理型基金，技术指标的信号可靠性较低。

    Args:
        nav_values: 按时间升序排列的净值序列
        config: 配置参数
        fund_type: 基金类型
        sub_type: 基金子类型

    Returns:
        TechnicalIndicators 实例
    """
    if not config:
        config = AdvisorConfig()

    indicators = TechnicalIndicators()
    n = len(nav_values)

    # 判断技术分析是否适用
    profile = _get_fund_profile(fund_type, sub_type)
    indicators.applicable = profile["technical_applicable"]

    if n < 5:
        return indicators

    arr = np.array(nav_values, dtype=np.float64)
    current = arr[-1]

    # --- 均线系统 ---
    if n >= 5:
        indicators.ma5 = float(np.mean(arr[-5:]))
    if n >= 10:
        indicators.ma10 = float(np.mean(arr[-10:]))
    if n >= 20:
        indicators.ma20 = float(np.mean(arr[-20:]))
    if n >= 60:
        indicators.ma60 = float(np.mean(arr[-60:]))
    if n >= 120:
        indicators.ma120 = float(np.mean(arr[-120:]))
    if n >= 250:
        indicators.ma250 = float(np.mean(arr[-250:]))

    # --- MACD (12, 26, 9) ---
    if n >= 35:
        # 使用完整序列计算 EMA，确保收敛
        ema12 = _ema(arr, 12)
        ema26 = _ema(arr, 26)
        # EMA26 从 index 25 开始有效，EMA12 从 index 11 开始
        # DIF = EMA12 - EMA26，取 EMA26 有效范围
        offset = 26 - 12  # EMA26 比 EMA12 晚 14 个点开始
        dif = ema12[offset:] - ema26
        if len(dif) >= 9:
            dea = _ema(dif, 9)
            if len(dea) > 0:
                indicators.macd_dif = float(dif[-1])
                indicators.macd_dea = float(dea[-1])
                indicators.macd_histogram = float((dif[-1] - dea[-1]) * 2)

                # MACD 信号判断
                if len(dif) > 1 and len(dea) > 1:
                    # 金叉/死叉判断
                    dif_curr, dif_prev = dif[-1], dif[-2]
                    dea_curr = dea[-1]
                    dea_prev = dea[-2] if len(dea) > 1 else dea[-1]
                    if dif_curr > dea_curr and dif_prev <= dea_prev:
                        indicators.macd_signal = "bullish"
                    elif dif_curr < dea_curr and dif_prev >= dea_prev:
                        indicators.macd_signal = "bearish"
                    elif dif_curr > dea_curr:
                        indicators.macd_signal = "bullish"
                    else:
                        indicators.macd_signal = "bearish"

    # --- RSI (Wilder 平滑，非简单平均) ---
    if n >= 15:
        indicators.rsi_6 = _rsi_wilder(arr, 6)
        indicators.rsi_14 = _rsi_wilder(arr, 14)

        if indicators.rsi_14 is not None:
            if indicators.rsi_14 < config.rsi_oversold:
                indicators.rsi_signal = "oversold"
            elif indicators.rsi_14 > config.rsi_overbought:
                indicators.rsi_signal = "overbought"

    # --- 布林带 (20, 2) ---
    if n >= 20:
        ma20 = np.mean(arr[-20:])
        std20 = np.std(arr[-20:], ddof=1)
        indicators.boll_upper = float(ma20 + 2 * std20)
        indicators.boll_middle = float(ma20)
        indicators.boll_lower = float(ma20 - 2 * std20)

        band_width = indicators.boll_upper - indicators.boll_lower
        if band_width > 0:
            indicators.boll_position = float(
                (current - indicators.boll_lower) / band_width
            )

    # --- 趋势评分 ---
    trend_signals = []

    # 短期均线多头排列
    if indicators.ma5 and indicators.ma20:
        if current > indicators.ma5 > indicators.ma20:
            trend_signals.append(0.5)
        elif current < indicators.ma5 < indicators.ma20:
            trend_signals.append(-0.5)
        else:
            trend_signals.append(0.0)

    # 中期趋势
    if indicators.ma20 and indicators.ma60:
        if indicators.ma20 > indicators.ma60:
            trend_signals.append(0.3)
        else:
            trend_signals.append(-0.3)

    # 长期趋势
    if indicators.ma60 and indicators.ma250:
        if indicators.ma60 > indicators.ma250:
            trend_signals.append(0.2)
        else:
            trend_signals.append(-0.2)

    if trend_signals:
        indicators.trend_score = float(np.clip(sum(trend_signals), -1.0, 1.0))

    return indicators


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """计算指数移动平均线 (EMA)。

    使用标准 EMA 公式：EMA_t = α * price_t + (1-α) * EMA_{t-1}
    初始值使用前 period 个数据的简单平均。
    """
    if len(data) < period:
        return np.array([])
    alpha = 2.0 / (period + 1)
    result = np.zeros(len(data) - period + 1)
    result[0] = np.mean(data[:period])
    for i in range(1, len(result)):
        result[i] = alpha * data[period - 1 + i] + (1 - alpha) * result[i - 1]
    return result


def _rsi_wilder(data: np.ndarray, period: int) -> float | None:
    """计算 Wilder 平滑 RSI（业界标准实现）。

    使用 Wilder 的指数平滑方法（等价于 2*period-1 的 EMA），
    而非简单平均。这是 MetaStock、TradingView 等平台的标准实现。

    Args:
        data: 价格序列
        period: RSI 周期

    Returns:
        RSI 值 (0~100)，数据不足返回 None
    """
    if len(data) < period + 1:
        return None

    deltas = np.diff(data)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Wilder 平滑：第一个值用简单平均，后续用指数平滑
    # 需要足够的数据来让平滑收敛
    if len(gains) < period:
        return None

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    # 继续用 Wilder 平滑迭代
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


# ---------------------------------------------------------------------------
# 动量/均值回复分析（替代原有的伪估值分析）
# ---------------------------------------------------------------------------


def compute_momentum_score(
    nav_values: list[float],
    lookback_days: int = 750,
) -> MomentumAnalysis:
    """计算动量和均值回复指标。

    替代原有的"净值百分位=估值"逻辑。
    分析收益率的动量特征和波动率状态，而非净值绝对水平。

    逻辑：
    - 短期动量（5-20日）：趋势跟随信号
    - 中期动量（60日）：趋势确认
    - 长期均值回复（120日+）：极端收益后的回归
    - 波动率状态：高波动时降低信号权重

    Args:
        nav_values: 历史净值序列（升序）
        lookback_days: 回看天数

    Returns:
        MomentumAnalysis 实例
    """
    analysis = MomentumAnalysis()

    if len(nav_values) < 60:
        return analysis

    arr = np.array(nav_values[-lookback_days:] if len(nav_values) > lookback_days else nav_values,
                   dtype=np.float64)
    n = len(arr)

    # 计算各期收益率
    if n >= 6:
        analysis.return_5d = float((arr[-1] / arr[-6]) - 1)
    if n >= 21:
        analysis.return_20d = float((arr[-1] / arr[-21]) - 1)
    if n >= 61:
        analysis.return_60d = float((arr[-1] / arr[-61]) - 1)
    if n >= 121:
        analysis.return_120d = float((arr[-1] / arr[-121]) - 1)

    # 计算日收益率序列
    returns = np.diff(arr) / arr[:-1]
    if len(returns) < 30:
        return analysis

    # 20日滚动收益率的 z-score（均值回复信号）
    if n >= 40:
        rolling_20d_returns = []
        for i in range(20, n):
            r = (arr[i] / arr[i - 20]) - 1
            rolling_20d_returns.append(r)
        if len(rolling_20d_returns) >= 20:
            roll_arr = np.array(rolling_20d_returns)
            mean_r = np.mean(roll_arr)
            std_r = np.std(roll_arr, ddof=1)
            if std_r > 0:
                analysis.zscore_20d = float((roll_arr[-1] - mean_r) / std_r)

    # 60日滚动收益率的 z-score
    if n >= 120:
        rolling_60d_returns = []
        for i in range(60, n):
            r = (arr[i] / arr[i - 60]) - 1
            rolling_60d_returns.append(r)
        if len(rolling_60d_returns) >= 20:
            roll_arr = np.array(rolling_60d_returns)
            mean_r = np.mean(roll_arr)
            std_r = np.std(roll_arr, ddof=1)
            if std_r > 0:
                analysis.zscore_60d = float((roll_arr[-1] - mean_r) / std_r)

    # 当前波动率（20日年化）
    recent_returns = returns[-20:]
    analysis.current_vol = float(np.std(recent_returns, ddof=1) * np.sqrt(252))

    # 波动率百分位（当前波动率在历史中的位置）
    if len(returns) >= 60:
        rolling_vols = []
        for i in range(20, len(returns)):
            vol = np.std(returns[i-20:i], ddof=1) * np.sqrt(252)
            rolling_vols.append(vol)
        if rolling_vols:
            count_below = sum(1 for v in rolling_vols if v < analysis.current_vol)
            analysis.vol_percentile = float(count_below / len(rolling_vols))

    # 判断市场 regime
    if analysis.return_60d is not None and analysis.zscore_60d is not None:
        if analysis.return_60d > 0.10 and analysis.zscore_60d > 1.5:
            analysis.regime = "trending_up"
        elif analysis.return_60d < -0.10 and analysis.zscore_60d < -1.5:
            analysis.regime = "trending_down"
        elif abs(analysis.zscore_60d) > 2.0:
            analysis.regime = "mean_reverting"  # 极端偏离，可能回归
        else:
            analysis.regime = "normal"

    # 综合动量评分
    # v5: 增强信号强度，减少 tanh 过度压缩
    scores = []
    weights = []

    # 短期动量（20日）— 趋势跟随
    if analysis.return_20d is not None:
        # v5: 将 tanh 系数从 10 降低到 7，减少压缩，保留更多信号区分度
        momentum_signal = float(np.tanh(analysis.return_20d * 7))
        scores.append(momentum_signal)
        weights.append(0.35)  # v5: 从 0.3 提升

    # 中期动量（60日）— 趋势确认
    if analysis.return_60d is not None:
        # v5: 将 tanh 系数从 5 降低到 3.5
        momentum_signal = float(np.tanh(analysis.return_60d * 3.5))
        scores.append(momentum_signal)
        weights.append(0.35)  # v5: 从 0.3 提升

    # 均值回复信号（z-score 极端时反转）
    if analysis.zscore_20d is not None:
        # z-score > 2 → 过度上涨，可能回调（负分）
        # z-score < -2 → 过度下跌，可能反弹（正分）
        if abs(analysis.zscore_20d) > 2.0:
            reversion_signal = float(-np.tanh(analysis.zscore_20d * 0.3))
            scores.append(reversion_signal)
            weights.append(0.20)
        elif abs(analysis.zscore_20d) > 1.5:
            # v5: 1.5~2.0 区间也给出温和的均值回复信号
            reversion_signal = float(-np.tanh(analysis.zscore_20d * 0.15))
            scores.append(reversion_signal)
            weights.append(0.10)
        else:
            scores.append(0.0)
            weights.append(0.10)

    # 波动率调整：高波动时降低信号强度（v5: 温和化）
    vol_discount = 1.0
    if analysis.vol_percentile is not None and analysis.vol_percentile > 0.8:
        vol_discount = 0.75  # v5: 从 0.6 提升到 0.75
    elif analysis.vol_percentile is not None and analysis.vol_percentile > 0.6:
        vol_discount = 0.9  # v5: 从 0.8 提升到 0.9

    if scores and weights:
        total_w = sum(weights)
        raw_score = sum(s * w for s, w in zip(scores, weights)) / total_w
        analysis.momentum_score = float(np.clip(raw_score * vol_discount, -1.0, 1.0))

    return analysis


# ---------------------------------------------------------------------------
# 动量因子自适应折扣
# ---------------------------------------------------------------------------


def _compute_adaptive_momentum_discount(
    nav_values: list[float],
    base_discount: float = 0.7,
) -> float:
    """基于近期动量信号有效性自适应调整折扣系数。

    方法：
    - 计算过去 120 天中，20 日动量方向与后续 20 日收益方向的一致率
    - 一致率高（>60%）→ 动量有效，折扣接近 1.0
    - 一致率低（<50%）→ 动量失效/反转，折扣低于 base_discount
    - 数据不足时回退到固定 base_discount

    Args:
        nav_values: 净值序列
        base_discount: 基础折扣系数（默认 0.7）

    Returns:
        自适应折扣系数，范围 [0.3, 1.0]
    """
    n = len(nav_values)
    # 需要至少 160 天数据（120 天回看 + 20 天前瞻 + 20 天动量窗口）
    if n < 160:
        return base_discount

    arr = np.array(nav_values, dtype=np.float64)

    # 在 [n-140, n-20] 范围内采样，检查动量方向与后续收益的一致性
    # 每隔 5 天采样一次，避免重叠过多
    hits = 0
    total = 0
    for i in range(n - 140, n - 20, 5):
        if i < 20:
            continue
        # 20 日动量方向
        momentum_return = (arr[i] / arr[i - 20]) - 1
        # 后续 20 日实际收益
        future_return = (arr[i + 20] / arr[i]) - 1

        # 方向一致 = 命中
        if (momentum_return > 0 and future_return > 0) or \
           (momentum_return < 0 and future_return < 0):
            hits += 1
        total += 1

    if total < 10:
        return base_discount

    hit_rate = hits / total

    # v5: 温和化映射，最低折扣从 0.3 提升到 0.5
    # hit_rate = 0.65+ → discount = 1.0（动量强有效）
    # hit_rate = 0.55  → discount ≈ 0.85
    # hit_rate = 0.50  → discount = base_discount (0.7)
    # hit_rate = 0.40  → discount ≈ 0.6
    # hit_rate < 0.35  → discount = 0.5（动量失效但不过度惩罚）
    if hit_rate >= 0.65:
        discount = 1.0
    elif hit_rate >= 0.50:
        # 线性插值：0.50 → base_discount, 0.65 → 1.0
        discount = base_discount + (1.0 - base_discount) * (hit_rate - 0.50) / 0.15
    elif hit_rate >= 0.35:
        # 线性插值：0.35 → 0.5, 0.50 → base_discount
        discount = 0.5 + (base_discount - 0.5) * (hit_rate - 0.35) / 0.15
    else:
        discount = 0.5

    return float(np.clip(discount, 0.5, 1.0))


# ---------------------------------------------------------------------------
# 风险预算仓位计算（替代 Kelly Criterion）
# ---------------------------------------------------------------------------


def compute_risk_budget_position(
    nav_values: list[float],
    total_capital: float,
    current_position_value: float = 0.0,
    config: AdvisorConfig | None = None,
    n_funds_in_portfolio: int = 1,
) -> RiskBudgetPosition:
    """基于风险预算模型计算建议仓位（v3 修正：考虑基金间相关性）。

    替代 Kelly Criterion 的原因：
    - Kelly 要求独立重复博弈，基金日收益率有自相关和波动聚集
    - Kelly 对参数估计误差极其敏感，小样本下容易过度集中
    - 风险预算模型更适合组合管理场景

    v3 修正：
    - 引入基金间平均相关性假设（默认 0.6，A股基金通常 0.5~0.8）
    - 使用正确的分散化因子：sqrt((1 + (n-1)*rho) / n)
    - 旧版假设零相关性（sqrt(n)），会高估分散化效果 30~50%
    - 增加最大回撤惩罚：回撤越大，仓位越保守

    方法：
    - 基于目标组合波动率，反推单只基金的最大仓位
    - 考虑基金自身波动率、最大回撤和基金间相关性
    - 波动率越高的基金，分配越少仓位

    Args:
        nav_values: 历史净值序列
        total_capital: 总可用资金
        current_position_value: 当前持仓市值
        config: 配置参数
        n_funds_in_portfolio: 组合中基金数量

    Returns:
        RiskBudgetPosition 实例
    """
    if not config:
        config = AdvisorConfig()

    position = RiskBudgetPosition()

    if len(nav_values) < 60 or total_capital <= 0:
        return position

    arr = np.array(nav_values[-252:], dtype=np.float64)
    returns = np.diff(arr) / arr[:-1]

    if len(returns) < 30:
        return position

    # 年化波动率
    ann_vol = float(np.std(returns, ddof=1) * np.sqrt(252))
    position.annualized_vol = ann_vol

    # 最大回撤（近1年）
    cumulative = np.cumprod(1 + returns)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = (cumulative - running_max) / running_max
    position.max_drawdown_1y = float(np.min(drawdowns))

    # 风险预算分配（v3 修正：考虑相关性）
    # 目标：组合波动率 = target_portfolio_vol
    # 等权组合波动率公式：
    #   σ_p = w * σ_i * sqrt(n * (1 + (n-1)*ρ))
    # 其中 ρ 为基金间平均相关性，n 为基金数量
    # 反推 w = target_vol / (σ_i * sqrt(n * (1 + (n-1)*ρ)))
    # 当 ρ=0 时退化为 target_vol / (σ_i * sqrt(n))（旧公式）
    # 当 ρ>0 时，分散化效果减弱，建议仓位更低
    if ann_vol > 0 and n_funds_in_portfolio > 0:
        target_vol = config.target_portfolio_vol
        n = n_funds_in_portfolio
        rho = config.avg_fund_correlation

        # 分散化因子：考虑相关性后的实际分散效果
        # 当 rho=0 时 = sqrt(n)（完全分散，与旧公式一致）
        # 当 rho=1 时 = n（无分散效果）
        # 当 rho=0.6, n=5 时 = sqrt(5*(1+4*0.6)) = sqrt(17) ≈ 4.12 > sqrt(5) ≈ 2.24
        diversification_factor = math.sqrt(n * (1 + (n - 1) * rho))

        # 基于波动率的基础仓位
        risk_budget = target_vol / (ann_vol * diversification_factor)

        # 最大回撤惩罚：回撤超过 20% 时进一步降低仓位
        mdd = abs(position.max_drawdown_1y)
        if mdd > 0.30:
            drawdown_penalty = 0.5  # 回撤>30%，仓位减半
        elif mdd > 0.20:
            drawdown_penalty = 0.7  # 回撤>20%，仓位打7折
        elif mdd > 0.10:
            drawdown_penalty = 0.85  # 回撤>10%，仓位打85折
        else:
            drawdown_penalty = 1.0

        risk_budget *= drawdown_penalty

        # 限制在合理范围
        risk_budget = max(0.02, min(risk_budget, config.max_single_position))
        position.risk_budget_pct = risk_budget
        position.suggested_position_pct = risk_budget

        # 计算建议金额
        target_value = total_capital * risk_budget
        diff = target_value - current_position_value
        position.suggested_amount = max(0.0, diff)

        # 风险贡献
        position.risk_contribution = ann_vol * risk_budget
    else:
        position.risk_budget_pct = 0.0
        position.suggested_position_pct = 0.0

    return position


# ---------------------------------------------------------------------------
# Bootstrap 预测参考（替代 GBM）
# ---------------------------------------------------------------------------


def compute_prediction_score(
    nav_values: list[float],
    config: AdvisorConfig | None = None,
    random_seed: int | None = None,
) -> PredictionRef:
    """基于 Block Bootstrap 的预测参考（v3 增强：自适应块大小）。

    替代 GBM 的原因：
    - GBM 假设收益率正态独立同分布，实际不成立
    - 基金收益率存在尖峰厚尾、波动聚集、自相关
    - Block Bootstrap 保留了短期自相关结构
    - 不对分布形态做参数化假设

    v3 增强：
    - 自适应块大小：基于收益率自相关结构自动选择最优块大小
    - 提升模拟次数至 5000，改善 5% VaR 估计的稳定性
    - 块大小选择方法：找到自相关函数首次不显著的滞后阶数

    方法：
    - 将历史日收益率切成自适应长度的块（block）
    - 随机抽取块拼接成模拟路径
    - 统计模拟路径的收益分布

    局限性：
    - 假设未来市场环境与历史样本期相似
    - 无法预测 regime 切换和黑天鹅事件
    - 样本期越短，估计越不稳定

    Args:
        nav_values: 历史净值序列
        config: 配置参数
        random_seed: 随机种子。None 表示使用随机种子（每次结果不同），
            指定整数则保证可复现。默认 None。

    Returns:
        PredictionRef 实例
    """
    if not config:
        config = AdvisorConfig()

    pred = PredictionRef()

    if len(nav_values) < 120:
        return pred

    arr = np.array(nav_values[-504:], dtype=np.float64)  # 最近2年
    returns = np.diff(arr) / arr[:-1]

    if len(returns) < 60:
        return pred

    pred.sample_size = len(returns)
    n_sims = config.bootstrap_n_simulations

    # 自适应块大小选择
    block_size = config.bootstrap_block_size
    if block_size <= 0:
        # 自适应：基于自相关结构选择块大小
        block_size = _select_block_size(
            returns,
            min_block=config.bootstrap_min_block,
            max_block=config.bootstrap_max_block,
        )

    # Block Bootstrap 模拟
    # 使用可配置种子：None = 随机（每次不同），整数 = 可复现
    rng = np.random.default_rng(random_seed)
    n_returns = len(returns)

    # 确保 block_size 不超过可用数据
    block_size = min(block_size, n_returns - 1)
    if block_size < 2:
        block_size = 2

    n_blocks_30 = math.ceil(30 / block_size)  # 30日模拟需要的块数
    n_blocks_90 = math.ceil(90 / block_size)  # 90日模拟需要的块数

    # 向量化 Block Bootstrap：批量生成所有起始索引，避免 Python 循环
    # 30日模拟
    start_indices_30 = rng.integers(
        0, n_returns - block_size, size=(n_sims, n_blocks_30)
    )
    # 构建所有路径的收益率矩阵（n_sims × 30）
    paths_30 = np.empty((n_sims, n_blocks_30 * block_size))
    for b in range(n_blocks_30):
        for offset in range(block_size):
            paths_30[:, b * block_size + offset] = returns[start_indices_30[:, b] + offset]
    paths_30 = paths_30[:, :30]  # 截取前30天
    sim_returns_30d = np.prod(1 + paths_30, axis=1) - 1

    # 90日模拟
    start_indices_90 = rng.integers(
        0, n_returns - block_size, size=(n_sims, n_blocks_90)
    )
    paths_90 = np.empty((n_sims, n_blocks_90 * block_size))
    for b in range(n_blocks_90):
        for offset in range(block_size):
            paths_90[:, b * block_size + offset] = returns[start_indices_90[:, b] + offset]
    paths_90 = paths_90[:, :90]  # 截取前90天
    sim_returns_90d = np.prod(1 + paths_90, axis=1) - 1

    # 统计结果
    pred.expected_return_30d = round(float(np.median(sim_returns_30d)), 6)
    pred.expected_return_90d = round(float(np.median(sim_returns_90d)), 6)
    pred.prob_positive_30d = round(float(np.mean(sim_returns_30d > 0)), 4)
    pred.prob_positive_90d = round(float(np.mean(sim_returns_90d > 0)), 4)

    # VaR 和 CVaR (Expected Shortfall)
    sorted_30d = np.sort(sim_returns_30d)
    var_idx = max(1, int(0.05 * n_sims))  # 5th percentile，确保至少1个样本
    pred.var_95_30d = round(float(sorted_30d[var_idx]), 6)
    # CVaR = 尾部所有损失的均值（含 VaR 分位点），即 sorted[:var_idx+1]
    pred.cvar_95_30d = round(float(np.mean(sorted_30d[:var_idx + 1])), 6)

    # 90% 置信区间宽度（衡量不确定性）
    p5 = np.percentile(sim_returns_30d, 5)
    p95 = np.percentile(sim_returns_30d, 95)
    pred.confidence_band_width = round(float(p95 - p5), 6)

    # 预测评分
    # 综合正收益概率和预期收益方向
    if pred.prob_positive_30d is not None:
        # 正收益概率映射到 [-1, 1]
        prob_score = (pred.prob_positive_30d - 0.5) * 2

        # 如果置信区间很宽（不确定性高），降低评分权重
        uncertainty_discount = 1.0
        if pred.confidence_band_width is not None and pred.confidence_band_width > 0.15:
            uncertainty_discount = 0.5  # 不确定性太高，信号打折
        elif pred.confidence_band_width is not None and pred.confidence_band_width > 0.10:
            uncertainty_discount = 0.7

        pred.prediction_score = float(
            np.clip(prob_score * uncertainty_discount, -1.0, 1.0)
        )

    return pred


def _select_block_size(
    returns: np.ndarray,
    min_block: int = 3,
    max_block: int = 15,
) -> int:
    """基于自相关结构自适应选择 Bootstrap 块大小。

    方法：计算收益率的自相关函数（ACF），找到首次不显著的滞后阶数。
    使用 Bartlett 近似的 95% 置信带判断显著性。

    Args:
        returns: 日收益率序列
        min_block: 最小块大小
        max_block: 最大块大小

    Returns:
        最优块大小
    """
    n = len(returns)
    if n < 50:
        return min_block

    # 计算自相关函数
    mean_r = np.mean(returns)
    var_r = np.var(returns)
    if var_r == 0:
        return min_block

    # Bartlett 95% 置信带
    significance_threshold = 1.96 / math.sqrt(n)

    # 找到首次不显著的滞后阶数
    optimal_lag = min_block
    for lag in range(1, max_block + 1):
        if lag >= n:
            break
        acf_val = np.mean(
            (returns[:n - lag] - mean_r) * (returns[lag:] - mean_r)
        ) / var_r
        if abs(acf_val) < significance_threshold:
            optimal_lag = max(min_block, lag)
            break
    else:
        # 所有滞后都显著，使用最大块
        optimal_lag = max_block

    return optimal_lag


# ---------------------------------------------------------------------------
# 市场 Regime 检测
# ---------------------------------------------------------------------------


def detect_market_regime(
    nav_values: list[float],
    config: AdvisorConfig | None = None,
) -> MarketRegime:
    """基于波动率和趋势双维度检测市场 regime。

    用于自适应调整各模块的信号权重：
    - bull（牛市）：正常权重，动量信号有效
    - bear（熊市）：降低买入倾向，提高卖出敏感度
    - crisis（危机）：大幅降低所有信号权重，倾向 hold
    - volatile（高波动震荡）：降低信号权重，增加不确定性折扣
    - normal（正常）：标准权重

    方法：
    - 波动率维度：当前20日年化波动率在历史中的百分位
    - 趋势维度：60日收益率方向和强度
    - 双维度交叉判断 regime

    Args:
        nav_values: 历史净值序列（至少120天）
        config: 配置参数

    Returns:
        MarketRegime 实例
    """
    if not config:
        config = AdvisorConfig()

    regime = MarketRegime()

    if len(nav_values) < 120:
        return regime

    arr = np.array(nav_values[-504:], dtype=np.float64)
    returns = np.diff(arr) / arr[:-1]

    if len(returns) < 60:
        return regime

    # --- 波动率维度 ---
    current_vol = float(np.std(returns[-20:], ddof=1) * np.sqrt(252))

    # 历史波动率分布
    rolling_vols = []
    for i in range(20, len(returns)):
        vol = float(np.std(returns[i - 20:i], ddof=1) * np.sqrt(252))
        rolling_vols.append(vol)

    if not rolling_vols:
        return regime

    vol_percentile = sum(1 for v in rolling_vols if v < current_vol) / len(rolling_vols)

    if vol_percentile > 0.95:
        regime.volatility_state = "extreme"
    elif vol_percentile > 0.80:
        regime.volatility_state = "high"
    elif vol_percentile < 0.20:
        regime.volatility_state = "low"
    else:
        regime.volatility_state = "normal"

    # --- 趋势维度 ---
    n = len(arr)
    if n >= 61:
        return_60d = float((arr[-1] / arr[-61]) - 1)
    else:
        return_60d = 0.0

    if n >= 21:
        return_20d = float((arr[-1] / arr[-21]) - 1)
    else:
        return_20d = 0.0

    # 趋势强度判断
    if return_60d > 0.15:
        regime.trend_state = "strong_up"
    elif return_60d > 0.05:
        regime.trend_state = "up"
    elif return_60d < -0.15:
        regime.trend_state = "strong_down"
    elif return_60d < -0.05:
        regime.trend_state = "down"
    else:
        regime.trend_state = "neutral"

    # --- 双维度交叉判断 regime ---
    if regime.volatility_state == "extreme" and regime.trend_state in ("strong_down", "down"):
        regime.regime = "crisis"
        regime.signal_weight_multiplier = 0.5  # v5: 从 0.3 提升到 0.5，不过度压制
        regime.hold_bias = 0.15  # v5: 从 0.3 降低到 0.15
        regime.regime_confidence = 0.8
    elif regime.volatility_state in ("extreme", "high") and regime.trend_state == "neutral":
        regime.regime = "volatile"
        regime.signal_weight_multiplier = 0.75  # v5: 从 0.6 提升到 0.75
        regime.hold_bias = 0.08  # v5: 从 0.15 降低到 0.08
        regime.regime_confidence = 0.6
    elif regime.volatility_state in ("low", "normal") and regime.trend_state in ("strong_up", "up"):
        regime.regime = "bull"
        regime.signal_weight_multiplier = 1.1  # v5: 牛市轻微加成
        regime.hold_bias = 0.0
        regime.regime_confidence = 0.7
    elif regime.trend_state in ("strong_down", "down") and regime.volatility_state != "extreme":
        regime.regime = "bear"
        regime.signal_weight_multiplier = 0.85  # v5: 从 0.7 提升到 0.85
        regime.hold_bias = 0.05  # v5: 从 0.1 降低到 0.05
        regime.regime_confidence = 0.6
    else:
        regime.regime = "normal"
        regime.signal_weight_multiplier = 1.0
        regime.hold_bias = 0.0
        regime.regime_confidence = 0.5

    return regime


# ---------------------------------------------------------------------------
# 组合相关性分析
# ---------------------------------------------------------------------------


def compute_correlation_matrix(
    nav_data: dict[str, list[tuple[str, float]]],
    min_overlap_days: int = 60,
) -> dict[tuple[str, str], float]:
    """计算基金间的相关性矩阵。

    用于识别高度同质化的基金，避免组合中多只高相关基金同时买入。

    Args:
        nav_data: 净值数据 {fund_code: [(date_str, nav), ...]}
        min_overlap_days: 最少重叠交易日数

    Returns:
        相关性字典 {(code_a, code_b): correlation}
    """
    correlations: dict[tuple[str, str], float] = {}
    codes = list(nav_data.keys())

    if len(codes) < 2:
        return correlations

    # 构建日期对齐的收益率矩阵
    # 先将每只基金的数据转为 {date: nav} 字典
    fund_date_nav: dict[str, dict[str, float]] = {}
    for code, records in nav_data.items():
        fund_date_nav[code] = {d: nav for d, nav in records}

    # 计算两两相关性
    for i in range(len(codes)):
        for j in range(i + 1, len(codes)):
            code_a, code_b = codes[i], codes[j]
            dates_a = set(fund_date_nav[code_a].keys())
            dates_b = set(fund_date_nav[code_b].keys())
            common_dates = sorted(dates_a & dates_b)

            if len(common_dates) < min_overlap_days + 1:
                continue

            # 取最近的重叠数据
            recent_dates = common_dates[-252:]  # 最近1年
            if len(recent_dates) < min_overlap_days + 1:
                continue

            navs_a = np.array([fund_date_nav[code_a][d] for d in recent_dates])
            navs_b = np.array([fund_date_nav[code_b][d] for d in recent_dates])

            # 计算日收益率
            returns_a = np.diff(navs_a) / navs_a[:-1]
            returns_b = np.diff(navs_b) / navs_b[:-1]

            if len(returns_a) < 30:
                continue

            # Spearman 秩相关系数（对厚尾分布更稳健，不受极端值影响）
            if np.std(returns_a) > 0 and np.std(returns_b) > 0:
                from scipy.stats import spearmanr
                corr_val, _ = spearmanr(returns_a, returns_b)
                corr = float(corr_val) if not np.isnan(corr_val) else 0.0
                correlations[(code_a, code_b)] = corr
                correlations[(code_b, code_a)] = corr

    return correlations


def filter_correlated_advices(
    advices: list,
    correlations: dict[tuple[str, str], float],
    threshold: float = 0.8,
    max_correlated_buys: int = 1,
) -> list:
    """过滤高相关基金的重复买入建议。

    当多只高相关基金（相关性 > threshold）同时给出买入建议时，
    只保留评分最高的，其余降级为 hold。

    Args:
        advices: TradingAdvice 列表
        correlations: 相关性字典
        threshold: 高相关性阈值
        max_correlated_buys: 高相关组中最多同时买入数

    Returns:
        过滤后的 TradingAdvice 列表（原地修改）
    """
    buy_advices = [a for a in advices if a.action == "buy"]
    if len(buy_advices) < 2:
        return advices

    # 构建高相关性分组（Union-Find 简化版）
    groups: dict[str, set[str]] = {}  # group_leader -> {members}
    leader_of: dict[str, str] = {}  # code -> leader

    for a in buy_advices:
        if a.fund_code not in leader_of:
            leader_of[a.fund_code] = a.fund_code
            groups[a.fund_code] = {a.fund_code}

    for a1 in buy_advices:
        for a2 in buy_advices:
            if a1.fund_code >= a2.fund_code:
                continue
            corr = correlations.get((a1.fund_code, a2.fund_code))
            if corr is not None and corr > threshold:
                # 合并两个组
                leader1 = leader_of[a1.fund_code]
                leader2 = leader_of[a2.fund_code]
                if leader1 != leader2:
                    # 合并到较大的组
                    if len(groups[leader1]) >= len(groups[leader2]):
                        groups[leader1].update(groups[leader2])
                        for code in groups[leader2]:
                            leader_of[code] = leader1
                        del groups[leader2]
                    else:
                        groups[leader2].update(groups[leader1])
                        for code in groups[leader1]:
                            leader_of[code] = leader2
                        del groups[leader1]

    # 在每个高相关组中，只保留评分最高的 max_correlated_buys 个买入建议
    for leader, members in groups.items():
        if len(members) <= max_correlated_buys:
            continue

        # 按综合评分排序
        group_buys = [
            a for a in buy_advices if a.fund_code in members
        ]
        group_buys.sort(key=lambda a: a.composite_score, reverse=True)

        # 降级多余的
        for a in group_buys[max_correlated_buys:]:
            a.action = "hold"
            a.suggested_amount = 0.0
            a.suggested_pct = 0.0
            a.reasons.append(
                f"与 {group_buys[0].fund_code} 高度相关"
                f"（相关系数>{threshold:.1f}），已降级为观望以避免同质化持仓"
            )
            a.risk_warnings.append(
                "组合中已有高相关基金的买入建议，分散化效果有限"
            )

    return advices


# ---------------------------------------------------------------------------
# 信号冷却机制
# ---------------------------------------------------------------------------


def apply_signal_cooldown(
    advice: "TradingAdvice",
    last_advice_action: str | None,
    last_advice_date: str | None,
    current_date: str,
    config: AdvisorConfig,
) -> "TradingAdvice":
    """应用信号冷却机制，避免短期内重复给出同方向建议。

    如果上次建议与本次方向相同，且在冷却期内：
    - 降低置信度
    - 如果降低后低于阈值，改为 hold

    Args:
        advice: 当前建议
        last_advice_action: 上次建议的方向
        last_advice_date: 上次建议的日期 (YYYY-MM-DD)
        current_date: 当前日期 (YYYY-MM-DD)
        config: 配置参数

    Returns:
        修改后的 advice（原地修改）
    """
    if not last_advice_action or not last_advice_date:
        return advice

    if advice.action == "hold":
        return advice

    # 只对同方向信号应用冷却
    if advice.action != last_advice_action:
        return advice

    try:
        last_date = date.fromisoformat(last_advice_date)
        curr_date = date.fromisoformat(current_date)
        days_since = (curr_date - last_date).days
    except (ValueError, TypeError):
        return advice

    if days_since >= config.signal_cooldown_days:
        return advice

    # 在冷却期内，应用衰减
    decay = config.cooldown_decay_factor
    advice.composite_score *= decay
    advice.confidence *= decay

    # 如果衰减后低于阈值，改为 hold
    if advice.action == "buy" and advice.composite_score < config.buy_threshold:
        advice.action = "hold"
        advice.suggested_amount = 0.0
        advice.reasons.append(
            f"距上次同方向建议仅 {days_since} 天（冷却期 {config.signal_cooldown_days} 天），"
            f"信号已衰减，建议观望"
        )
    elif advice.action == "sell" and advice.composite_score > config.sell_threshold:
        advice.action = "hold"
        advice.suggested_amount = 0.0
        advice.reasons.append(
            f"距上次同方向建议仅 {days_since} 天（冷却期 {config.signal_cooldown_days} 天），"
            f"信号已衰减，建议观望"
        )
    else:
        advice.risk_warnings.append(
            f"距上次同方向建议仅 {days_since} 天，信号强度已折扣 {(1-decay)*100:.0f}%"
        )

    return advice


# ---------------------------------------------------------------------------
# 技术指标评分
# ---------------------------------------------------------------------------


def score_technical(indicators: TechnicalIndicators) -> float:
    """将技术指标转换为综合评分 (-1 到 1)。

    正分表示看多（利于买入），负分表示看空（利于卖出）。
    v5: 即使技术分析"不适用"，也返回弱化的信号（而非 0）。
    """
    if not indicators.applicable:
        # v5: 不适用时仍返回趋势信号的 30%（作为辅助参考）
        return indicators.trend_score * 0.3

    scores: list[float] = []
    weights: list[float] = []

    # 1. 趋势评分 (权重 0.3)
    scores.append(indicators.trend_score)
    weights.append(0.3)

    # 2. MACD 信号 (权重 0.25)
    macd_score = 0.0
    if indicators.macd_signal == "bullish":
        macd_score = 0.7  # v5: 从 0.6 提升
    elif indicators.macd_signal == "bearish":
        macd_score = -0.7
    if indicators.macd_histogram is not None:
        if indicators.macd_histogram > 0:
            macd_score = min(1.0, macd_score + 0.2)
        else:
            macd_score = max(-1.0, macd_score - 0.2)
    scores.append(macd_score)
    weights.append(0.25)

    # 3. RSI 信号 (权重 0.25)
    rsi_score = 0.0
    if indicators.rsi_14 is not None:
        if indicators.rsi_14 < 25:
            rsi_score = 1.0  # v5: 极端超卖，满分
        elif indicators.rsi_14 < 30:
            rsi_score = 0.8
        elif indicators.rsi_14 < 40:
            rsi_score = 0.3
        elif indicators.rsi_14 > 75:
            rsi_score = -1.0  # v5: 极端超买，满分
        elif indicators.rsi_14 > 70:
            rsi_score = -0.8
        elif indicators.rsi_14 > 60:
            rsi_score = -0.3
    scores.append(rsi_score)
    weights.append(0.25)

    # 4. 布林带位置 (权重 0.2)
    boll_score = 0.0
    if indicators.boll_position is not None:
        if indicators.boll_position < 0.05:
            boll_score = 0.9  # v5: 极端位置给更强信号
        elif indicators.boll_position < 0.1:
            boll_score = 0.7
        elif indicators.boll_position < 0.3:
            boll_score = 0.3
        elif indicators.boll_position > 0.95:
            boll_score = -0.9
        elif indicators.boll_position > 0.9:
            boll_score = -0.7
        elif indicators.boll_position > 0.7:
            boll_score = -0.3
    scores.append(boll_score)
    weights.append(0.2)

    # 加权平均
    total_weight = sum(weights)
    if total_weight > 0:
        weighted_score = sum(s * w for s, w in zip(scores, weights)) / total_weight
        return float(np.clip(weighted_score, -1.0, 1.0))
    return 0.0


# ---------------------------------------------------------------------------
# 费用估算
# ---------------------------------------------------------------------------


def estimate_trading_fee(
    amount: float,
    action: str,
    fund_type: str | None = None,
    sub_type: str | None = None,
    fee_data: dict[str, Any] | None = None,
    *,
    holding_days: int | None = None,
) -> FeeEstimate:
    """估算交易费用。

    优先使用数据库中的完整费率梯度；缺失时回退到基金类型的典型费率。

    Args:
        amount: 交易金额
        action: buy/sell
        fund_type: 基金类型
        sub_type: 子类型
        fee_data: 数据库中的费率与费率梯度
        holding_days: 持有天数（卖出时用于匹配赎回费率档位）

    Returns:
        FeeEstimate 实例
    """
    from app.domain.backtest.fees import FeeTier, calc_subscribe_fee, find_redeem_tier

    fee = FeeEstimate()
    profile = _get_fund_profile(fund_type, sub_type)

    subscribe_rate = 0.0
    redeem_rate = 0.0
    subscribe_tiers: list[dict[str, Any]] = []
    redeem_tiers: list[dict[str, Any]] = []

    if fee_data:
        subscribe_rate = float(fee_data.get("subscribe_rate", 0.0) or 0.0)
        redeem_rate = float(fee_data.get("redeem_rate", 0.0) or 0.0)
        subscribe_tiers = list(fee_data.get("subscribe_tiers", []) or [])
        redeem_tiers = list(fee_data.get("redeem_tiers", []) or [])
    else:
        subscribe_rate = float(profile["typical_subscribe_fee"])
        redeem_rate = float(profile["typical_redeem_fee"])

    fee.subscribe_fee_rate = subscribe_rate
    fee.redeem_fee_rate = redeem_rate

    if action == "buy":
        if amount > 0 and subscribe_tiers:
            try:
                tiers = [
                    FeeTier(
                        min_amount=Decimal(str(t.get("min_amount", 0))),
                        max_amount=(
                            Decimal(str(t["max_amount"]))
                            if t.get("max_amount") is not None else None
                        ),
                        rate=Decimal(str(t.get("rate", 0))),
                    )
                    for t in subscribe_tiers
                ]
                result = calc_subscribe_fee(Decimal(str(amount)), tiers)
                fee.subscribe_fee_rate = float(result.rate)
                fee.estimated_fee = float(result.fee)
                fee.net_trade_amount = float(result.net_amount)
                fee.fee_source = "tiered"
            except Exception:
                fee.estimated_fee = amount * subscribe_rate
                fee.net_trade_amount = amount - fee.estimated_fee
                fee.fee_source = "db" if fee_data else "profile"
        else:
            fee.estimated_fee = amount * subscribe_rate
            fee.net_trade_amount = amount - fee.estimated_fee
            fee.fee_source = "db" if fee_data else "profile"
    elif action == "sell":
        matched_rate = None
        if amount > 0 and redeem_tiers and holding_days is not None and holding_days >= 0:
            try:
                tiers = [
                    FeeTier(
                        min_holding_days=int(t.get("min_days", 0)),
                        max_holding_days=(
                            int(t["max_days"]) if t.get("max_days") is not None else None
                        ),
                        rate=Decimal(str(t.get("rate", 0))),
                    )
                    for t in redeem_tiers
                ]
                matched = find_redeem_tier(holding_days, tiers)
                if matched is not None:
                    matched_rate = float(matched.rate)
            except Exception:
                matched_rate = None

        fee.redeem_fee_rate = matched_rate if matched_rate is not None else redeem_rate
        fee.estimated_fee = amount * fee.redeem_fee_rate
        fee.net_trade_amount = amount - fee.estimated_fee
        fee.fee_source = (
            "tiered"
            if matched_rate is not None
            else ("db" if fee_data else "profile")
        )
    else:
        fee.estimated_fee = 0.0
        fee.net_trade_amount = amount
        fee.fee_source = "none"

    if amount > 0:
        fee.fee_impact_pct = fee.estimated_fee / amount
    return fee


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _estimate_fund_risk_level(
    fund_type: str | None,
    risk_position: RiskBudgetPosition | None = None,
) -> tuple[str, int]:
    """基于基金类型、波动率和回撤估算产品风险等级。"""
    base = FUND_TYPE_RISK_LEVEL_MAP.get((fund_type or "mixed").lower(), 3)
    if risk_position:
        if risk_position.annualized_vol > 0.30 or abs(risk_position.max_drawdown_1y) > 0.30:
            base = max(base, 5)
        elif risk_position.annualized_vol > 0.20 or abs(risk_position.max_drawdown_1y) > 0.20:
            base = max(base, 4)
        elif risk_position.annualized_vol < 0.04 and abs(risk_position.max_drawdown_1y) < 0.03:
            base = min(base, 2)
    base = int(np.clip(base, 1, 5))
    return f"R{base}", base


def _get_fund_profile(
    fund_type: str | None, sub_type: str | None = None
) -> dict[str, Any]:
    """获取基金类型对应的分析配置。"""
    if fund_type and fund_type.lower() in FUND_TYPE_PROFILES:
        profile = FUND_TYPE_PROFILES[fund_type.lower()].copy()
    else:
        profile = DEFAULT_PROFILE.copy()

    # ETF/LOF 子类型覆盖：启用技术分析
    if sub_type:
        sub_lower = sub_type.lower()
        if any(kw in sub_lower for kw in ETF_LOF_KEYWORDS):
            profile["technical_applicable"] = True
            profile["weight_technical"] = 0.25

    return profile


# ---------------------------------------------------------------------------
# 交易时间规则
# ---------------------------------------------------------------------------


def _is_trading_day_safe(d: date) -> bool:
    """安全判断交易日，交易日历不可用时回退到工作日判断。"""
    try:
        from app.domain.backtest.calendar import is_trading_day
        return bool(is_trading_day(d))
    except Exception:
        return d.weekday() < 5


def _next_trading_day_safe(d: date) -> date:
    """安全获取下一交易日，交易日历不可用时回退到工作日推进。"""
    try:
        from app.domain.backtest.calendar import next_trading_day
        return next_trading_day(d)
    except Exception:
        current = d + timedelta(days=1)
        while current.weekday() >= 5:
            current += timedelta(days=1)
        return current


def _first_trading_day_on_or_after(d: date) -> date:
    """返回 d 当日或之后的第一个交易日。"""
    if _is_trading_day_safe(d):
        return d
    return _next_trading_day_safe(d)


def _add_trading_days(start: date, days: int) -> date:
    """从 start 作为 T 日开始推进 N 个交易日。"""
    if days <= 0:
        return start
    current = start
    for _ in range(days):
        current = _next_trading_day_safe(current)
    return current


def _calendar_source_note() -> str:
    """返回交易日历来源说明。"""
    try:
        from app.domain.backtest.calendar import _xcal_available  # type: ignore[attr-defined]
        if _xcal_available:
            return "exchange_calendars XSHG/A股交易日历"
    except Exception:
        pass
    return "A股交易日历（节假日表/工作日兜底）"


def _format_trade_date(d: str | None) -> str:
    """格式化日期并附中文星期。"""
    if not d:
        return "-"
    try:
        parsed = date.fromisoformat(d)
    except (TypeError, ValueError):
        return str(d)
    return f"{parsed} ({_weekday_name(parsed.weekday())})"


def _timing_rule_days(action: str, fund_type: str | None) -> tuple[int | None, int | None, int | None]:
    """返回确认日、清算/到账日、可用日相对 T 日的交易日偏移。"""
    intent = normalize_trade_direction(action)
    ft = (fund_type or "").lower()

    if intent == "hold":
        return (None, None, None)

    if ft == "qdii":
        if intent == "subscribe":
            return (2, None, 2)
        return (2, 7, 7)

    if ft == "money":
        if intent == "subscribe":
            return (1, None, 1)
        return (0, 1, 1)

    if intent == "subscribe":
        return (1, None, 1)

    # 普通开放式基金赎回：确认通常 T+1，到账常见 T+1~T+3；结构字段取偏保守 T+2。
    return (1, 2, 2)


def calculate_fund_trade_timing(
    action: str,
    fund_type: str | None = None,
    request_time: datetime | None = None,
) -> FundTradeTiming:
    """计算基金申赎的受理 T 日、净值日、预计确认日和到账/可用日。

    说明：
    - 使用北京时间和 15:00:00 截止规则；15:00:00 及之后视为下一交易日。
    - 交易日历基于 A 股交易日历；QDII 未建模境外市场假期，输出保守估算和风险提示。
    - 本函数只用于建议展示，不代表销售机构最终确认或到账承诺。
    """
    tz = ZoneInfo("Asia/Shanghai")
    if request_time is None:
        now = datetime.now(tz)
    elif request_time.tzinfo is None:
        now = request_time.replace(tzinfo=tz)
    else:
        now = request_time.astimezone(tz)

    today = now.date()
    intent = normalize_trade_direction(action)
    is_td = _is_trading_day_safe(today)
    cutoff = now.replace(hour=15, minute=0, second=0, microsecond=0)
    is_after_cutoff = now >= cutoff

    if intent == "hold":
        accepted = _first_trading_day_on_or_after(today)
    elif is_td and not is_after_cutoff:
        accepted = today
    else:
        accepted = _next_trading_day_safe(today)

    confirm_offset, settlement_offset, available_offset = _timing_rule_days(intent, fund_type)
    confirm_date = (
        _add_trading_days(accepted, confirm_offset).isoformat()
        if confirm_offset is not None else None
    )
    settlement_date = (
        _add_trading_days(accepted, settlement_offset).isoformat()
        if settlement_offset is not None else None
    )
    available_date = (
        _add_trading_days(accepted, available_offset).isoformat()
        if available_offset is not None else None
    )

    timing = FundTradeTiming(
        request_time=now.isoformat(),
        timezone="Asia/Shanghai",
        cutoff_time="15:00:00",
        is_trading_day=is_td,
        is_after_cutoff=is_after_cutoff,
        accepted_trade_date=accepted.isoformat(),
        nav_date=accepted.isoformat(),
        expected_confirm_date=confirm_date,
        expected_settlement_date=settlement_date,
        expected_available_date=available_date,
        fund_type=fund_type,
        trade_intent=intent,
        calendar_source=_calendar_source_note(),
    )

    if intent == "subscribe":
        timing.rule_basis = (
            "交易日15:00前提交按当日T日净值，15:00:00及之后或非交易日提交顺延至下一交易日；"
            "申购份额通常T+1确认。"
        )
    elif intent == "redeem":
        timing.rule_basis = (
            "交易日15:00前提交按当日T日净值，15:00:00及之后或非交易日提交顺延至下一交易日；"
            "普通赎回通常T+1确认，资金T+1至T+3到账。"
        )
    else:
        timing.rule_basis = "当前建议为持有，仅展示如提交交易时可能适用的下一受理日。"

    if intent != "hold":
        if not is_td:
            timing.warnings.append(
                f"当前为非交易日，若提交交易将顺延至 {_format_trade_date(timing.accepted_trade_date)} 作为 T 日"
            )
        elif is_after_cutoff:
            timing.warnings.append(
                f"当前已到或超过 15:00 截止，若提交交易将顺延至 {_format_trade_date(timing.accepted_trade_date)} 作为 T 日"
            )
        else:
            timing.warnings.append(
                f"当前在交易日 15:00 前，若提交交易将以 {_format_trade_date(timing.accepted_trade_date)} 作为 T 日"
            )
        timing.warnings.append(
            "注意：当日基金净值通常在下一交易日晚间更新，实际成交净值存在未知价风险"
        )

    ft = (fund_type or "").lower()
    if ft == "qdii" and intent != "hold":
        timing.warnings.append(
            "QDII 受境外市场时差、境外节假日和外汇结算影响，本系统仅按境内受理日做保守估算"
        )
    if ft == "money" and intent == "redeem":
        timing.warnings.append(
            "货币基金部分快速赎回通道可 T+0 到账但通常有额度和渠道限制，普通赎回仍以销售机构确认为准"
        )

    return timing


def _attach_trade_timing(advice: "TradingAdvice") -> "TradingAdvice":
    """为建议附加与最终 action 一致的交易时点估算。"""
    advice.trade_timing = calculate_fund_trade_timing(advice.action, advice.fund_type)
    return advice


def _generate_trading_time_warnings(
    action: str,
    settlement_days: int,
    fund_type: str | None = None,
) -> list[str]:
    """生成基金交易时间规则相关的提示信息。"""
    timing = calculate_fund_trade_timing(action, fund_type)
    intent = timing.trade_intent
    warnings = list(timing.warnings)

    if intent == "subscribe":
        if timing.expected_confirm_date:
            warnings.append(
                f"申购预计确认日：{_format_trade_date(timing.expected_confirm_date)}"
                f"（按 T 日 {_format_trade_date(timing.nav_date)} 净值计算）"
            )
        if timing.expected_available_date:
            warnings.append(
                f"确认后份额预计可用日：{_format_trade_date(timing.expected_available_date)}"
            )
    elif intent == "redeem":
        if timing.expected_confirm_date:
            warnings.append(
                f"赎回预计确认日：{_format_trade_date(timing.expected_confirm_date)}"
                f"（按 T 日 {_format_trade_date(timing.nav_date)} 净值计算）"
            )
        if timing.expected_settlement_date:
            warnings.append(
                f"赎回资金预计到账/可用日：{_format_trade_date(timing.expected_settlement_date)}"
            )
        elif settlement_days > 0:
            estimated = _add_trading_days(date.fromisoformat(timing.nav_date), settlement_days)
            warnings.append(f"赎回资金预计 T+{settlement_days} 左右到账（约 {estimated}）")

    return warnings


def get_next_effective_trading_date() -> tuple[str, str]:
    """获取下一个有效交易日和截止时间信息。

    Returns:
        (effective_date_desc, cutoff_info) 元组
        例如: ("2026-05-21 (周四)", "今日 15:00 前提交有效")
    """
    timing = calculate_fund_trade_timing("subscribe")
    now = datetime.fromisoformat(timing.request_time)
    effective = _format_trade_date(timing.accepted_trade_date)

    if not timing.is_trading_day:
        cutoff_info = "当前为非交易日，提交后按上述日期净值确认"
    elif timing.is_after_cutoff:
        cutoff_info = "已到或超过今日 15:00 截止，提交后按上述日期净值确认"
    else:
        cutoff_info = f"今日 15:00 前提交有效（当前北京时间 {now.hour:02d}:{now.minute:02d}）"

    return effective, cutoff_info


def _weekday_name(weekday: int) -> str:
    """返回中文星期名。"""
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return names[weekday] if 0 <= weekday <= 6 else ""


def normalize_trade_direction(direction: str | None) -> str:
    """归一化交易方向语义。

    建议层使用 buy/sell/hold，交易层/信号层使用 subscribe/redeem/hold。
    这里统一转换到交易层语义，未知值回退为 hold。
    """
    if direction is None:
        return "hold"
    return _DIRECTION_ALIASES.get(str(direction).strip().lower(), "hold")


def _stable_seed(*parts: str) -> int:
    """基于稳定哈希生成可复现随机种子。"""
    payload = "|".join(parts).encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big") % (2**31)


# ---------------------------------------------------------------------------
# Advisor 数据质量 / 过拟合风险辅助
# ---------------------------------------------------------------------------


def _parse_nav_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def build_advisor_data_quality_report(
    fund_code: str,
    nav_records: list[tuple[str, float]],
    *,
    as_of_date: date,
    lookback_days: int,
    prediction_sample_size: int = 0,
    current_volatility: float | None = None,
    volatility_percentile: float | None = None,
    nav_diagnostics: dict[str, Any] | None = None,
) -> AdvisorDataQualityReport:
    """基于 Advisor 输入净值序列生成数据质量报告。"""
    del fund_code  # 保留参数，便于后续扩展跨源一致性检查
    nav_count = len(nav_records)
    report = AdvisorDataQualityReport(
        nav_count=nav_count,
        sample_sufficient=nav_count >= 120,
        prediction_sample_size=prediction_sample_size,
        current_volatility=current_volatility,
        volatility_percentile=volatility_percentile,
    )
    if not nav_records:
        report.status = "poor"
        report.score = 0.0
        report.warnings.append("无可用净值数据")
        return report

    parsed_dates = [_parse_nav_date(item[0]) for item in nav_records]
    valid_dates = [item for item in parsed_dates if item is not None]
    data_start = valid_dates[0] if valid_dates else None
    data_end = valid_dates[-1] if valid_dates else None
    report.data_start = data_start.isoformat() if data_start else str(nav_records[0][0])
    report.data_end = data_end.isoformat() if data_end else str(nav_records[-1][0])

    expected_days = 0
    if data_start and data_end:
        calendar_span = max(1, (data_end - data_start).days + 1)
        expected_days = max(1, min(lookback_days, calendar_span) * 5 // 7)
        report.coverage_ratio = min(1.0, nav_count / expected_days) if expected_days > 0 else None
        report.freshness_days = max(0, (as_of_date - data_end).days)

        max_gap = 0
        for prev, curr in zip(valid_dates, valid_dates[1:]):
            gap = max(0, (curr - prev).days - 1)
            max_gap = max(max_gap, gap)
        report.max_gap_days = max_gap

    spike_dates: list[str] = []
    for idx in range(1, len(nav_records)):
        prev_nav = nav_records[idx - 1][1]
        curr_nav = nav_records[idx][1]
        if prev_nav and prev_nav > 0:
            daily_change = abs((curr_nav - prev_nav) / prev_nav)
            if daily_change > 0.15:
                spike_dates.append(str(nav_records[idx][0]))
    report.spike_count = len(spike_dates)
    report.spike_dates = spike_dates[:10]

    score = 1.0
    if nav_count < 60:
        report.warnings.append("净值样本少于 60 条，无法形成可靠交易判断")
        score -= 0.65
    elif nav_count < 120:
        report.warnings.append("净值样本少于 120 条，统计估计稳定性较弱")
        score -= 0.25

    if report.coverage_ratio is not None:
        if report.coverage_ratio < 0.5:
            report.warnings.append(f"估算覆盖率仅 {report.coverage_ratio:.1%}")
            score -= 0.45
        elif report.coverage_ratio < 0.8:
            report.warnings.append(f"估算覆盖率偏低：{report.coverage_ratio:.1%}")
            score -= 0.20

    if report.max_gap_days > 30:
        report.warnings.append(f"净值最大连续缺失约 {report.max_gap_days} 天")
        score -= 0.35
    elif report.max_gap_days > 10:
        report.warnings.append(f"净值存在连续缺失：最大约 {report.max_gap_days} 天")
        score -= 0.15

    if report.spike_count > 5:
        report.warnings.append(f"净值跳变次数较多：{report.spike_count} 次")
        score -= 0.20
    elif report.spike_count > 0:
        report.warnings.append(f"检测到 {report.spike_count} 次单日净值跳变")
        score -= 0.08

    if report.freshness_days is not None:
        if report.freshness_days > 10:
            report.warnings.append(f"数据距离分析日已滞后 {report.freshness_days} 天")
            score -= 0.30
        elif report.freshness_days > 5:
            report.warnings.append(f"数据新鲜度一般，滞后 {report.freshness_days} 天")
            score -= 0.12

    diagnostics = nav_diagnostics or {}
    source_consistency = dict(diagnostics.get("source_consistency") or {})
    adjustment_consistency = dict(diagnostics.get("adjustment_consistency") or {})
    cross_source_consistency = dict(diagnostics.get("cross_source_consistency") or {})
    report.source_consistency = source_consistency
    report.adjustment_consistency = adjustment_consistency
    report.cross_source_consistency = cross_source_consistency

    if source_consistency:
        source_count = int(source_consistency.get("source_count") or 0)
        source_switch_count = int(source_consistency.get("source_switch_count") or 0)
        missing_source_count = int(source_consistency.get("missing_source_count") or 0)
        point_count = max(1, int(source_consistency.get("point_count") or nav_count or 1))
        switch_ratio = float(source_consistency.get("source_switch_ratio") or (source_switch_count / point_count))
        primary_source = source_consistency.get("primary_source")
        if missing_source_count:
            report.warnings.append(f"{missing_source_count} 条 NAV 缺少来源标识，跨源追溯不完整")
            score -= min(0.18, 0.04 + missing_source_count / point_count * 0.20)
        if source_count >= 2:
            if switch_ratio > 0.25 or source_switch_count >= 5:
                report.warnings.append(
                    f"NAV 数据源频繁切换（主来源 {primary_source or '未知'}，切换 {source_switch_count} 次），需关注跨源一致性"
                )
                score -= 0.18
            else:
                report.warnings.append(f"NAV 窗口内存在 {source_count} 个数据源，已纳入跨源一致性审计")
                score -= 0.06

    if cross_source_consistency:
        cross_status = str(cross_source_consistency.get("status") or "unknown")
        hard_gate = bool(cross_source_consistency.get("hard_gate"))
        alert_count = int(cross_source_consistency.get("alert_count") or 0)
        reason = str(cross_source_consistency.get("reason") or "")
        if hard_gate or cross_status == "fail":
            report.warnings.append(reason or f"多源 NAV 原始对照失败，存在 {alert_count} 个跨源冲突")
            score -= 0.70
        elif cross_status == "warning":
            report.warnings.append(reason or f"多源 NAV 原始对照发现 {alert_count} 个差异")
            score -= 0.18
        elif cross_status == "insufficient_sources":
            report.warnings.append(reason or "跨源 NAV 原始对照证据不足")
            score -= 0.04
        elif cross_status == "error":
            report.warnings.append(reason or "跨源 NAV 原始对照执行失败")
            score -= 0.08

    if adjustment_consistency:
        adjusted_coverage_ratio = adjustment_consistency.get("adjusted_coverage_ratio")
        adjusted_coverage = float(adjusted_coverage_ratio) if isinstance(adjusted_coverage_ratio, (int, float)) else None
        fallback_count = int(adjustment_consistency.get("fallback_to_unit_count") or 0)
        factor_jump_count = int(adjustment_consistency.get("factor_jump_count") or 0)
        missing_unit_count = int(adjustment_consistency.get("missing_unit_count") or 0)
        point_count = max(1, int(adjustment_consistency.get("point_count") or nav_count or 1))
        if adjusted_coverage is not None:
            if adjusted_coverage < 0.50:
                report.warnings.append(f"复权净值覆盖率仅 {adjusted_coverage:.1%}，收益序列可能受分红/拆分影响")
                score -= 0.30
            elif adjusted_coverage < 0.90:
                report.warnings.append(f"复权净值覆盖率偏低：{adjusted_coverage:.1%}")
                score -= 0.15
        if fallback_count:
            fallback_ratio = fallback_count / point_count
            report.warnings.append(f"{fallback_count} 条记录回退使用单位净值，复权口径不完全一致")
            score -= min(0.20, 0.06 + fallback_ratio * 0.18)
        if factor_jump_count:
            report.warnings.append(f"检测到 {factor_jump_count} 次复权因子异常跳变")
            score -= 0.25 if factor_jump_count >= 3 else 0.12
        if missing_unit_count:
            report.warnings.append(f"{missing_unit_count} 条记录缺少单位净值，无法校验复权因子")
            score -= min(0.12, missing_unit_count / point_count * 0.18)

    report.score = float(np.clip(score, 0.0, 1.0))
    if bool(cross_source_consistency.get("hard_gate")) or str(cross_source_consistency.get("status") or "") == "fail":
        report.status = "poor"
    elif report.score < 0.45 or nav_count < 60:
        report.status = "poor"
    elif report.score < 0.75 or report.warnings:
        report.status = "warning"
    else:
        report.status = "good"
    return report


def _build_signal_contributions(advice: "TradingAdvice") -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    scores = {
        "technical": advice.technical_score,
        "momentum": advice.momentum_score,
        "strategy": advice.strategy_score,
        "prediction": advice.prediction_score,
        "cross_sectional": advice.cross_sectional_score,
    }
    weights = dict(getattr(advice, "_signal_weights", {}) or {})
    availability = dict(getattr(advice, "_signal_availability", {}) or {})
    rows: list[dict[str, Any]] = []
    total_abs = 0.0
    for name, score in scores.items():
        weight = float(weights.get(name, 0.0) or 0.0)
        contribution = float(score) * weight
        total_abs += abs(contribution)
        rows.append({
            "source": name,
            "score": round(float(score), 4),
            "weight": round(weight, 4),
            "contribution": round(contribution, 4),
            "available": bool(availability.get(name, weight > 0)),
        })
    rows.sort(key=lambda item: abs(float(item["contribution"])), reverse=True)
    dominant = None
    if rows and total_abs > 0:
        top = rows[0]
        share = abs(float(top["contribution"])) / total_abs
        dominant = {
            "source": top["source"],
            "contribution_share": round(share, 4),
            "single_signal_dominant": share >= 0.65,
        }
    return rows, dominant


def build_advisor_overfit_risk(
    advice: "TradingAdvice",
    *,
    engine_health_status: str | None = None,
    rolling_ic_samples: int = 0,
) -> AdvisorOverfitRisk:
    """基于 OOS 与健康度信息合成 Advisor 级过拟合风险。"""
    metrics = advice.reliability_adjustment.metrics if advice.reliability_adjustment else {}
    pbo = metrics.get("oos_pbo")
    cpcv_n_paths = int(metrics.get("oos_cpcv_n_paths") or 0)
    cpcv_avg_oos_sharpe = metrics.get("oos_cpcv_avg_oos_sharpe")
    cpcv_std_oos_sharpe = metrics.get("oos_cpcv_std_oos_sharpe")
    cpcv_avg_is_sharpe = metrics.get("oos_cpcv_avg_is_sharpe")
    oos_ic = metrics.get("oos_avg_ic")
    ic_degradation = metrics.get("oos_ic_degradation")
    oos_signal_count = int(metrics.get("oos_total_signals") or 0)
    risk = AdvisorOverfitRisk(
        pbo=pbo if isinstance(pbo, (int, float)) else None,
        cpcv_n_paths=cpcv_n_paths,
        cpcv_avg_oos_sharpe=cpcv_avg_oos_sharpe if isinstance(cpcv_avg_oos_sharpe, (int, float)) else None,
        cpcv_std_oos_sharpe=cpcv_std_oos_sharpe if isinstance(cpcv_std_oos_sharpe, (int, float)) else None,
        cpcv_avg_is_sharpe=cpcv_avg_is_sharpe if isinstance(cpcv_avg_is_sharpe, (int, float)) else None,
        oos_ic=oos_ic if isinstance(oos_ic, (int, float)) else None,
        ic_degradation=ic_degradation if isinstance(ic_degradation, (int, float)) else None,
        oos_signal_count=oos_signal_count,
        engine_health_status=engine_health_status,
        rolling_ic_samples=rolling_ic_samples,
    )

    score = 0.0
    if risk.pbo is not None:
        if risk.pbo >= 0.70:
            score += 0.60
            risk.reasons.append(f"CPCV/PBO 过高（{risk.pbo:.0%}），历史调参过拟合概率很高")
        elif risk.pbo >= 0.50:
            score += 0.38
            risk.reasons.append(f"CPCV/PBO 偏高（{risk.pbo:.0%}），存在明显过拟合风险")
        elif risk.pbo >= 0.30:
            score += 0.12
            risk.reasons.append(f"CPCV/PBO 中等（{risk.pbo:.0%}），需结合 OOS 衰减观察")
    elif cpcv_n_paths > 0:
        score += 0.08
        risk.reasons.append("CPCV/PBO 诊断未给出有效 PBO 数值")

    if cpcv_n_paths and cpcv_n_paths < 10:
        score += 0.08
        risk.reasons.append(f"CPCV 路径数偏少（{cpcv_n_paths}），PBO 稳定性有限")

    if risk.cpcv_avg_oos_sharpe is not None and risk.cpcv_avg_oos_sharpe < 0:
        score += 0.15
        risk.reasons.append("CPCV 平均样本外 Sharpe 为负")

    multi_objective_score = metrics.get("oos_multi_objective_score")
    if isinstance(multi_objective_score, (int, float)):
        if multi_objective_score < -0.05:
            score += 0.25
            risk.reasons.append(f"多目标 OOS 稳健性分数偏低（{multi_objective_score:.2f}）")
        elif multi_objective_score < 0.05:
            score += 0.10
            risk.reasons.append(f"多目标 OOS 稳健性分数较弱（{multi_objective_score:.2f}）")
    if metrics.get("oos_multi_objective_eliminated"):
        score += 0.35
        reasons = metrics.get("oos_multi_objective_reasons") or []
        suffix = "：" + "；".join(str(item) for item in reasons[:2]) if reasons else ""
        risk.reasons.append("多目标门禁淘汰该样本外快照" + suffix)

    if oos_signal_count <= 0:
        score += 0.35
        risk.reasons.append("缺少 Advisor 样本外验证快照")
    elif oos_signal_count < 20:
        score += 0.25
        risk.reasons.append(f"样本外信号数偏少（{oos_signal_count}）")

    if risk.oos_ic is not None and risk.oos_ic < 0.02:
        score += 0.25
        risk.reasons.append("样本外 IC 偏弱")
    if risk.ic_degradation is not None:
        if risk.ic_degradation < 0.30:
            score += 0.40
            risk.reasons.append("样本外/样本内 IC 衰减严重")
        elif risk.ic_degradation < 0.50:
            score += 0.25
            risk.reasons.append("样本外/样本内 IC 衰减明显")

    if engine_health_status == "unhealthy":
        score += 0.30
        risk.reasons.append("引擎健康度不佳")
    elif engine_health_status == "degraded":
        score += 0.18
        risk.reasons.append("引擎健康度降级")
    elif engine_health_status in {"unknown", "not_evaluated", None}:
        score += 0.10
        risk.reasons.append("引擎健康度证据不足")

    if 0 < rolling_ic_samples < 30:
        score += 0.12
        risk.reasons.append(f"滚动跟踪样本少于 30（当前 {rolling_ic_samples}）")

    risk.score = float(np.clip(score, 0.0, 1.0))
    if risk.score >= 0.60:
        risk.level = "high"
        risk.gate_action = "hold"
    elif risk.score >= 0.30:
        risk.level = "medium"
        risk.gate_action = "reduce"
    else:
        risk.level = "low"
        risk.gate_action = "allow"
    if not risk.reasons:
        risk.reasons.append("样本外与健康度证据未显示明显过拟合风险")
    return risk


# ---------------------------------------------------------------------------
# 核心：交易建议引擎
# ---------------------------------------------------------------------------


class TradingAdvisor:
    """交易建议引擎（v5 智能增强版）。

    综合技术分析、动量分析、策略信号、Bootstrap 预测、截面因子和市场 regime 检测，
    为用户生成参考性的买卖建议。

    v5 核心改进：
    - 自适应权重重分配：信号源不可用时自动将权重分配给可用信号
    - 动态阈值：根据可用信号源数量自动降低买卖阈值
    - 非线性信号共识加成：多信号方向一致时额外加成
    - Bootstrap 预测恢复参与决策（降低但非零权重）
    - Regime 调整温和化：不再过度压制信号
    - 信号新鲜度半衰期延长到 14 天

    重要声明：
    - 本系统输出仅为量化信号的聚合参考，不构成投资建议
    - 所有预测基于历史数据，不保证未来有效
    - 建议结合自身风险承受能力和投资目标使用

    使用流程：
    1. 初始化引擎（传入配置和用户资金信息）
    2. 调用 generate_advice() 生成建议
    3. 返回结构化的 TradingAdvice 列表
    """

    def __init__(
        self,
        config: AdvisorConfig | None = None,
        total_capital: float = 100000.0,
        current_positions: dict[str, float] | None = None,
        positions_detail: dict[str, dict[str, Any]] | None = None,
        last_advices: dict[str, dict[str, str]] | None = None,
        cross_sectional_scores: dict[str, float] | None = None,
        macro_score: float | None = None,
        user_profile: dict[str, Any] | None = None,
        engine_health: Any | None = None,
        oos_snapshots: dict[str, Any] | None = None,
        learned_weights: Any | None = None,
        as_of_date: date | None = None,
        nav_quality_diagnostics: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.config = config or AdvisorConfig()
        self.total_capital = total_capital
        self.current_positions = current_positions or {}
        self.positions_detail = positions_detail or {}
        self.last_advices = last_advices or {}
        self.cross_sectional_scores = cross_sectional_scores or {}
        self.user_profile = user_profile or {"risk_level": "moderate"}
        self.engine_health = engine_health
        self.oos_snapshots = oos_snapshots or {}
        self.nav_quality_diagnostics = nav_quality_diagnostics or {}
        self.as_of_date = as_of_date or date.today()
        # v5: 宏观因子评分（由外部预计算传入，所有基金共享同一个宏观环境）
        self.macro_score = macro_score or 0.0

        # v5: 学习参数改为外部显式注入，避免历史回放使用未来参数
        self._learned_weights = learned_weights
        if self._learned_weights and self._learned_weights.confidence >= 0.3:
            logger.info(
                "advisor.loaded_learned_params: confidence=%.2f, samples=%d",
                self._learned_weights.confidence,
                self._learned_weights.sample_count,
            )

    def generate_advice(
        self,
        fund_codes: list[str],
        nav_data: dict[str, list[tuple[str, float]]],
        strategy_signals: dict[str, dict[str, Any]] | None = None,
        fund_names: dict[str, str] | None = None,
        fund_types: dict[str, tuple[str | None, str | None]] | None = None,
        fee_data: dict[str, dict[str, float]] | None = None,
        fund_rules: dict[str, "FundTradingRules"] | None = None,
    ) -> list[TradingAdvice]:
        """为指定基金生成交易建议（v5 智能增强）。

        Args:
            fund_codes: 需要分析的基金代码列表
            nav_data: 净值数据 {fund_code: [(date_str, nav), ...]}
            strategy_signals: 策略信号 {fund_code: {direction, strength, ...}}
            fund_names: 基金名称映射 {fund_code: name}
            fund_types: 基金类型映射 {fund_code: (fund_type, sub_type)}
            fee_data: 费率数据 {fund_code: {subscribe_rate, redeem_rate}}
            fund_rules: 基金交易规则 {fund_code: FundTradingRules}

        Returns:
            TradingAdvice 列表，按综合评分绝对值排序
        """
        if not fund_names:
            fund_names = {}
        if not strategy_signals:
            strategy_signals = {}
        if not fund_types:
            fund_types = {}
        if not fee_data:
            fee_data = {}
        if not fund_rules:
            fund_rules = {}

        advices: list[TradingAdvice] = []
        today_str = self.as_of_date.isoformat()
        n_funds = max(1, len(fund_codes))

        # v3: 检测市场 regime（使用组合中数据最多的基金作为代理）
        market_regime = self._detect_portfolio_regime(nav_data)

        for code in fund_codes:
            try:
                ft, st = fund_types.get(code, (None, None))
                advice = self._analyze_fund(
                    fund_code=code,
                    nav_records=nav_data.get(code, []),
                    signal=strategy_signals.get(code),
                    fund_name=fund_names.get(code),
                    fund_type=ft,
                    sub_type=st,
                    fee_info=fee_data.get(code),
                    today_str=today_str,
                    n_funds=n_funds,
                    market_regime=market_regime,
                )

                # v3: 应用信号冷却
                last = self.last_advices.get(code)
                if last:
                    advice = apply_signal_cooldown(
                        advice=advice,
                        last_advice_action=last.get("action"),
                        last_advice_date=last.get("date"),
                        current_date=today_str,
                        config=self.config,
                    )

                # v3: 应用基金交易规则约束
                if code in fund_rules:
                    # 计算持有天数（用于精确赎回费率）
                    _holding_days = None
                    detail = self.positions_detail.get(code, {})
                    buy_date_str = detail.get("buy_date")
                    if buy_date_str:
                        try:
                            buy_d = date.fromisoformat(str(buy_date_str))
                            _holding_days = (self.as_of_date - buy_d).days
                        except (ValueError, TypeError):
                            pass
                    advice = apply_fund_trading_rules(
                        advice, fund_rules[code], holding_days=_holding_days
                    )

                _attach_trade_timing(advice)
                advices.append(advice)
            except Exception as e:
                logger.warning(
                    "advisor.fund_analysis_error",
                    fund_code=code,
                    error=str(e),
                )
                fallback_advice = TradingAdvice(
                    fund_code=code,
                    fund_name=fund_names.get(code),
                    advice_date=today_str,
                    action="hold",
                    reasons=[f"分析异常: {str(e)}，建议观望"],
                )
                _attach_trade_timing(fallback_advice)
                advices.append(fallback_advice)

        # v3: 组合相关性过滤
        if len(fund_codes) >= 2:
            try:
                correlations = compute_correlation_matrix(nav_data)
                advices = filter_correlated_advices(
                    advices,
                    correlations,
                    threshold=self.config.correlation_threshold,
                    max_correlated_buys=self.config.max_correlated_buys,
                )
            except Exception as e:
                logger.warning("advisor.correlation_filter_error: %s", str(e))

        for advice in advices:
            self._finalize_advice_extensions(advice, nav_data.get(advice.fund_code, []))
            _attach_trade_timing(advice)

        # 按综合评分绝对值排序（信号最强的排前面）
        advices.sort(key=lambda a: abs(a.composite_score), reverse=True)
        return advices

    def _extract_engine_health_value(self, name: str, default: Any = None) -> Any:
        """兼容 dict / dataclass 的引擎健康度字段读取。"""
        health = self.engine_health
        if health is None:
            return default
        if isinstance(health, dict):
            return health.get(name, default)
        return getattr(health, name, default)

    def _get_oos_snapshot(self, fund_code: str) -> Any | None:
        """读取单只基金最近一次样本外验证快照。"""
        return self.oos_snapshots.get(fund_code)

    def _apply_reliability_adjustment(self, advice: TradingAdvice) -> None:
        """根据引擎健康度 + 样本外验证结果对综合分做防过拟合折扣。"""
        if not self.config.enable_reliability_adjustment:
            return

        status = str(self._extract_engine_health_value("status", "unknown") or "unknown")
        samples = int(self._extract_engine_health_value("rolling_ic_samples", 0) or 0)
        rolling_ic = self._extract_engine_health_value("rolling_ic_20d", None)
        ic_trend = str(self._extract_engine_health_value("ic_trend", "stable") or "stable")
        buy_hit = self._extract_engine_health_value("recent_buy_hit_rate", None)
        sell_hit = self._extract_engine_health_value("recent_sell_hit_rate", None)
        status_reason = str(self._extract_engine_health_value("status_reason", "") or "")

        multiplier = 1.0
        confidence_multiplier = 1.0
        amount_multiplier = 1.0
        reason_parts: list[str] = []
        metrics: dict[str, Any] = {
            "rolling_ic_20d": rolling_ic,
            "rolling_ic_samples": samples,
            "ic_trend": ic_trend,
            "recent_buy_hit_rate": buy_hit,
            "recent_sell_hit_rate": sell_hit,
            "status_reason": status_reason,
        }

        if self.engine_health is not None:
            if status == "healthy":
                reason_parts.append("引擎健康度正常，未触发跟踪层折扣")
            elif status == "degraded":
                multiplier *= self.config.reliability_degraded_multiplier
                confidence_multiplier *= 0.85
                amount_multiplier *= 0.70
                reason_parts.append("引擎健康度降级，历史跟踪信号偏弱")
            elif status == "unhealthy":
                multiplier *= self.config.reliability_unhealthy_multiplier
                confidence_multiplier *= 0.60
                amount_multiplier *= 0.35
                reason_parts.append("引擎健康度不佳，跟踪层提示信号可能失效")
            elif status == "insufficient_data":
                multiplier *= self.config.reliability_insufficient_data_multiplier
                confidence_multiplier *= 0.90
                amount_multiplier *= 0.80
                reason_parts.append("历史跟踪样本不足，按保守系数折扣")
            else:
                multiplier *= self.config.reliability_unknown_multiplier
                confidence_multiplier *= 0.80
                amount_multiplier *= 0.70
                reason_parts.append("引擎健康度未知，按保守系数折扣")

            if samples and samples < self.config.reliability_min_samples:
                multiplier *= self.config.reliability_insufficient_data_multiplier
                confidence_multiplier *= 0.92
                amount_multiplier *= 0.85
                reason_parts.append(f"跟踪样本量 {samples} 少于可靠阈值 {self.config.reliability_min_samples}")

            if ic_trend == "critical":
                multiplier *= self.config.reliability_critical_trend_multiplier
                confidence_multiplier *= 0.85
                amount_multiplier *= 0.85
                reason_parts.append("近期 IC 明显恶化")
            elif ic_trend == "declining":
                multiplier *= self.config.reliability_declining_trend_multiplier
                confidence_multiplier *= 0.92
                reason_parts.append("近期 IC 走弱")

        snapshot = self._get_oos_snapshot(advice.fund_code)
        if self.config.oos_reliability_enabled and snapshot is not None:
            oos_ic = getattr(snapshot, "avg_oos_ic", None)
            ic_degradation = getattr(snapshot, "ic_degradation", None)
            oos_signals = int(getattr(snapshot, "total_oos_signals", 0) or 0)
            oos_buy_hit = getattr(snapshot, "avg_oos_buy_hit_rate", None)
            oos_sell_hit = getattr(snapshot, "avg_oos_sell_hit_rate", None)
            metrics.update({
                "oos_avg_ic": oos_ic,
                "oos_ic_degradation": ic_degradation,
                "oos_total_signals": oos_signals,
                "oos_buy_hit_rate": oos_buy_hit,
                "oos_sell_hit_rate": oos_sell_hit,
                "oos_pbo": getattr(snapshot, "pbo", None),
                "oos_cpcv_n_paths": getattr(snapshot, "cpcv_n_paths", 0),
                "oos_cpcv_avg_oos_sharpe": getattr(snapshot, "cpcv_avg_oos_sharpe", None),
                "oos_cpcv_std_oos_sharpe": getattr(snapshot, "cpcv_std_oos_sharpe", None),
                "oos_cpcv_avg_is_sharpe": getattr(snapshot, "cpcv_avg_is_sharpe", None),
                "oos_multi_objective_score": getattr(snapshot, "multi_objective_score", None),
                "oos_multi_objective_components": getattr(snapshot, "multi_objective_components", None),
                "oos_multi_objective_eliminated": getattr(snapshot, "multi_objective_eliminated", None),
                "oos_multi_objective_reasons": getattr(snapshot, "multi_objective_reasons", None),
                "oos_snapshot_date": getattr(snapshot, "snapshot_date", None),
                "oos_config_hash": getattr(snapshot, "config_hash", None),
                "oos_data_version": getattr(snapshot, "data_version", None),
                "oos_validation_window": getattr(snapshot, "validation_window", None),
                "oos_updated_at": getattr(snapshot, "updated_at", None),
                "oos_risk_level": getattr(snapshot, "risk_level", None),
                "oos_selection_source": getattr(snapshot, "selection_source", None),
                "oos_requested_risk_level": getattr(snapshot, "requested_risk_level", None),
            })

            if oos_signals < self.config.oos_min_signals:
                multiplier *= self.config.reliability_insufficient_data_multiplier
                confidence_multiplier *= 0.92
                amount_multiplier *= 0.85
                reason_parts.append(f"样本外信号数 {oos_signals} 少于阈值 {self.config.oos_min_signals}")
            else:
                if oos_ic is not None and oos_ic < self.config.oos_weak_ic:
                    multiplier *= self.config.oos_weak_multiplier
                    confidence_multiplier *= 0.90
                    amount_multiplier *= 0.85
                    reason_parts.append("样本外 IC 偏弱")
                if ic_degradation is not None and ic_degradation < self.config.oos_bad_degradation:
                    multiplier *= self.config.oos_bad_multiplier
                    confidence_multiplier *= 0.85
                    amount_multiplier *= 0.75
                    reason_parts.append("样本外/样本内 IC 衰减明显")
                if ic_degradation is not None and ic_degradation < self.config.oos_critical_degradation:
                    multiplier *= self.config.oos_critical_multiplier
                    confidence_multiplier *= 0.80
                    amount_multiplier *= 0.70
                    reason_parts.append("样本外泛化能力很差，存在严重过拟合风险")
                if oos_ic is not None and oos_ic >= self.config.oos_good_ic and (ic_degradation is None or ic_degradation >= self.config.oos_bad_degradation):
                    reason_parts.append("样本外验证表现尚可")

        multiplier = float(np.clip(multiplier, 0.20, 1.0))
        confidence_multiplier = float(np.clip(confidence_multiplier, 0.20, 1.0))
        amount_multiplier = float(np.clip(amount_multiplier, 0.20, 1.0))
        if multiplier < 1.0:
            original_score = advice.composite_score
            advice.composite_score = float(np.clip(advice.composite_score * multiplier, -1.0, 1.0))
            advice.risk_warnings.append(
                f"防过拟合可靠性折扣已应用：综合评分由 {original_score:.2f} 调整为 {advice.composite_score:.2f}"
            )

        advice.reliability_adjustment = ReliabilityAdjustment(
            status=status if self.engine_health is not None else "not_evaluated",
            multiplier=multiplier,
            confidence_multiplier=confidence_multiplier,
            amount_multiplier=amount_multiplier,
            reason="；".join(reason_parts) if reason_parts else "未找到可用的可靠性评估数据",
            metrics=metrics,
        )

    def _reset_advice_to_hold(
        self,
        advice: TradingAdvice,
        reason: str,
        warning: str | None = None,
    ) -> None:
        """将建议重置为持有，并同步清理交易数量。"""
        advice.action = "hold"
        advice.confidence = min(advice.confidence, 0.35)
        advice.urgency = "low"
        advice.suggested_amount = 0.0
        advice.suggested_shares = None
        advice.estimated_gross_amount = None
        advice.estimated_net_amount = None
        advice.suggested_pct = 0.0
        advice.position_after = (
            self._current_position_value(advice.fund_code) / self.total_capital
            if self.total_capital > 0 else 0.0
        )
        if advice.fee_estimate:
            advice.fee_estimate.estimated_fee = 0.0
            advice.fee_estimate.net_trade_amount = None
            advice.fee_estimate.fee_impact_pct = 0.0
        if reason not in advice.reasons:
            advice.reasons.append(reason)
        if warning and warning not in advice.risk_warnings:
            advice.risk_warnings.append(warning)

    def _apply_quality_and_overfit_gates(self, advice: TradingAdvice) -> None:
        """根据数据质量和过拟合风险对最终建议做硬门禁或保守缩放。"""
        quality = advice.data_quality
        overfit = advice.overfit_risk

        if quality and quality.status == "poor" and advice.action in {"buy", "sell"}:
            self._reset_advice_to_hold(
                advice,
                "数据质量为 poor，禁止输出增配/减配候选，改为观察复核",
                "数据质量较差：" + "；".join(quality.warnings[:3]) if quality.warnings else "数据质量较差",
            )
            return

        if overfit and overfit.level == "high" and advice.action in {"buy", "sell"}:
            self._reset_advice_to_hold(
                advice,
                "过拟合风险较高，禁止输出增配/减配候选，改为观察复核",
                "过拟合风险高：" + "；".join(overfit.reasons[:3]),
            )
            return

        scale = 1.0
        reasons: list[str] = []
        if quality and quality.status == "warning":
            scale *= 0.75
            reasons.append("数据质量 warning")
        if overfit and overfit.level == "medium":
            scale *= 0.70
            reasons.append("过拟合风险 medium")

        if scale < 1.0:
            advice.confidence = float(np.clip(advice.confidence * (0.85 if scale >= 0.75 else 0.75), 0.0, 1.0))
            if advice.action == "buy" and advice.suggested_amount > 0:
                original = advice.suggested_amount
                advice.suggested_amount = round(max(0.0, advice.suggested_amount * scale), 2)
                advice.estimated_gross_amount = advice.suggested_amount
                advice.suggested_pct = advice.suggested_amount / self.total_capital if self.total_capital > 0 else 0.0
                advice.position_after = (
                    (self._current_position_value(advice.fund_code) + advice.suggested_amount) / self.total_capital
                    if self.total_capital > 0 else 0.0
                )
                if advice.fee_estimate:
                    advice.fee_estimate.estimated_fee = advice.suggested_amount * advice.fee_estimate.subscribe_fee_rate
                    advice.fee_estimate.net_trade_amount = advice.suggested_amount - advice.fee_estimate.estimated_fee
                    advice.estimated_net_amount = advice.fee_estimate.net_trade_amount
                else:
                    advice.estimated_net_amount = advice.suggested_amount
                advice.reasons.append(
                    f"{'、'.join(reasons)}，建议金额由 ¥{original:,.0f} 下调至 ¥{advice.suggested_amount:,.0f}"
                )
            if reasons:
                warning = f"{'、'.join(reasons)}，已降低置信度和交易金额"
                if warning not in advice.risk_warnings:
                    advice.risk_warnings.append(warning)

    def _current_position_value(self, fund_code: str) -> float:
        """读取当前持仓市值，优先使用 positions_detail.market_value。"""
        current_value = self.current_positions.get(fund_code, 0.0)
        detail = self.positions_detail.get(fund_code, {})
        if detail.get("market_value") is not None:
            try:
                current_value = float(detail["market_value"])
            except (TypeError, ValueError):
                pass
        return float(current_value or 0.0)

    def _finalize_advice_extensions(
        self,
        advice: TradingAdvice,
        nav_records: list[tuple[str, float]],
    ) -> None:
        """在最终 action 确定后补齐结构化专业字段。"""
        self._apply_suitability_check(advice)
        self._apply_profile_constraints(advice)
        advice._sync_decision_support_fields()
        advice.strength = self._classify_advice_strength(advice)
        advice.trade_plan = self._build_trade_plan(advice)
        advice.portfolio_impact = self._build_portfolio_impact(advice)
        advice.reasoning = self._build_reasoning(advice)
        advice.validity = self._build_validity(advice, nav_records)
        advice.decision_audit = self._build_decision_audit(advice, nav_records)

    def _add_profile_constraint(
        self,
        advice: TradingAdvice,
        name: str,
        effect: str,
        explanation: str,
        *,
        warning: bool = True,
    ) -> None:
        """记录投资画像约束，并同步到风险提示。"""
        constraint = ProfileConstraint(
            name=name,
            triggered=True,
            effect=effect,
            explanation=explanation,
        )
        advice.profile_constraints.append(constraint)
        if warning and explanation not in advice.risk_warnings:
            advice.risk_warnings.append(explanation)

    def _scale_buy_amount_for_profile(
        self,
        advice: TradingAdvice,
        factor: float,
        reason: str,
    ) -> None:
        """按投资画像约束缩放买入金额并同步相关金额字段。"""
        if advice.action != "buy" or advice.suggested_amount <= 0:
            return
        original = advice.suggested_amount
        advice.suggested_amount = round(max(0.0, original * factor), 2)
        advice.estimated_gross_amount = advice.suggested_amount
        advice.suggested_pct = (
            advice.suggested_amount / self.total_capital if self.total_capital > 0 else 0.0
        )
        advice.position_after = (
            (self._current_position_value(advice.fund_code) + advice.suggested_amount) / self.total_capital
            if self.total_capital > 0 else 0.0
        )
        if advice.fee_estimate:
            advice.fee_estimate.estimated_fee = advice.suggested_amount * advice.fee_estimate.subscribe_fee_rate
            advice.fee_estimate.net_trade_amount = advice.suggested_amount - advice.fee_estimate.estimated_fee
            advice.fee_estimate.fee_impact_pct = (
                advice.fee_estimate.estimated_fee / advice.suggested_amount
                if advice.suggested_amount > 0 else 0.0
            )
            advice.estimated_net_amount = advice.fee_estimate.net_trade_amount
        else:
            advice.estimated_net_amount = advice.suggested_amount
        advice.reasons.append(f"{reason}，买入金额由 ¥{original:,.0f} 调整为 ¥{advice.suggested_amount:,.0f}")
        if advice.suggested_amount < self.config.min_trade_amount:
            advice.action = "hold"
            advice.suggested_amount = 0.0
            advice.estimated_gross_amount = None
            advice.estimated_net_amount = None
            advice.suggested_pct = 0.0
            advice.position_after = self._current_position_value(advice.fund_code) / self.total_capital if self.total_capital > 0 else 0.0
            advice.reasons.append("画像约束后金额低于最小交易额，改为继续持有")

    def _apply_profile_constraints(self, advice: TradingAdvice) -> None:
        """让投资目标、期限、流动性和回撤承受力实际约束交易建议。"""
        profile = self.user_profile or {}
        goal = profile.get("investment_goal")
        horizon = profile.get("investment_horizon")
        liquidity_need = profile.get("liquidity_need")
        max_dd = profile.get("max_drawdown_tolerance")
        monthly_invest_amount = profile.get("monthly_invest_amount")
        industry_tolerance = str(profile.get("industry_concentration_tolerance") or "").lower() or None
        qdii_fx_tolerance = str(profile.get("qdii_fx_risk_tolerance") or "").lower() or None
        fee_sensitivity = str(profile.get("fee_sensitivity") or "").lower() or None
        personalization = profile.get("advisor_personalization") if isinstance(profile.get("advisor_personalization"), dict) else {}
        ft = (advice.fund_type or "").lower()
        is_risky = ft in RISK_ASSET_TYPES

        if not any([
            goal,
            horizon,
            liquidity_need,
            max_dd is not None,
            monthly_invest_amount is not None,
            industry_tolerance,
            qdii_fx_tolerance,
            fee_sensitivity,
            personalization,
        ]):
            return

        if personalization and float(personalization.get("confidence") or 0.0) >= 0.2:
            style = str(personalization.get("preferred_execution_style") or "neutral")
            amount_scale = float(personalization.get("amount_scale") or 1.0)
            if advice.action == "buy" and amount_scale < 0.98:
                self._scale_buy_amount_for_profile(
                    advice,
                    amount_scale,
                    f"根据你的历史执行习惯，已按用户级学习将单笔金额收敛到 {amount_scale:.0%}",
                )
                self._add_profile_constraint(
                    advice,
                    "用户级学习",
                    "reduce_amount",
                    "系统仅基于你的执行记录学习交易节奏，不改变底层买卖信号和风控门禁",
                    warning=False,
                )
            elif advice.action == "buy" and style in {"batch", "small_steps"}:
                self._add_profile_constraint(
                    advice,
                    "用户级学习",
                    "warning",
                    "你的历史执行更偏分批/小步，交易计划会优先给出更保守节奏",
                    warning=False,
                )

        if goal == "cash_management" and advice.action == "buy" and ft != "money":
            if is_risky:
                self._scale_buy_amount_for_profile(advice, 0.25, "投资目标为现金管理，不宜大幅配置波动型基金")
                self._add_profile_constraint(advice, "投资目标", "reduce_amount", "现金管理目标下，权益/混合/QDII 等波动型基金仅适合小比例观察")
            elif ft == "bond":
                self._scale_buy_amount_for_profile(advice, 0.5, "投资目标为现金管理，债券基金也需控制金额")
                self._add_profile_constraint(advice, "投资目标", "reduce_amount", "现金管理目标更强调流动性和低波动，债券基金买入金额已下调")

        if horizon == "within_3_months" and advice.action == "buy" and is_risky:
            self._scale_buy_amount_for_profile(advice, 0.4, "投资期限小于3个月，不适合重仓波动型基金")
            self._add_profile_constraint(advice, "投资期限", "reduce_amount", "短期限资金应优先考虑流动性和净值波动风险，已降低买入金额")
        elif horizon == "3_to_12_months" and advice.action == "buy" and ft in {"stock", "qdii"}:
            self._scale_buy_amount_for_profile(advice, 0.7, "投资期限不足1年，股票/QDII 基金买入金额需适度降低")
            self._add_profile_constraint(advice, "投资期限", "reduce_amount", "中短期资金配置高波动基金需要控制仓位")

        if liquidity_need == "high" and advice.action == "buy":
            if ft == "qdii":
                self._scale_buy_amount_for_profile(advice, 0.4, "流动性需求较高，QDII 确认和赎回到账周期较长")
                self._add_profile_constraint(advice, "流动性需求", "reduce_amount", "QDII 到账周期较长，与高流动性需求不完全匹配")
            elif is_risky:
                self._scale_buy_amount_for_profile(advice, 0.7, "流动性需求较高，波动型基金买入金额需控制")
                self._add_profile_constraint(advice, "流动性需求", "reduce_amount", "高流动性需求下应保留更多现金或低波动资产")

        try:
            max_dd_value = float(max_dd) if max_dd is not None else None
        except (TypeError, ValueError):
            max_dd_value = None
        if max_dd_value is not None and advice.risk_position:
            fund_dd = abs(advice.risk_position.max_drawdown_1y)
            if advice.action == "buy" and fund_dd > max_dd_value:
                if fund_dd > max_dd_value * 1.5:
                    factor = 0.35
                else:
                    factor = 0.6
                self._scale_buy_amount_for_profile(
                    advice,
                    factor,
                    f"基金近1年最大回撤 {fund_dd:.1%} 高于你的承受范围 {max_dd_value:.1%}",
                )
                self._add_profile_constraint(
                    advice,
                    "最大回撤承受力",
                    "reduce_amount",
                    f"基金历史回撤高于你的设定承受范围，已降低买入金额",
                )
            elif fund_dd > max_dd_value:
                self._add_profile_constraint(
                    advice,
                    "最大回撤承受力",
                    "warning",
                    f"基金近1年最大回撤 {fund_dd:.1%} 高于你的承受范围 {max_dd_value:.1%}",
                )

        try:
            monthly_amount_value = float(monthly_invest_amount) if monthly_invest_amount is not None else None
        except (TypeError, ValueError):
            monthly_amount_value = None
        if advice.action == "buy" and monthly_amount_value is not None and monthly_amount_value > 0 and advice.suggested_amount > monthly_amount_value:
            factor = max(0.1, monthly_amount_value / max(advice.suggested_amount, 1e-6))
            self._scale_buy_amount_for_profile(advice, factor, f"单次建议金额已按每月可投资金额 ¥{monthly_amount_value:,.0f} 收敛")
            self._add_profile_constraint(advice, "每月可投资金额", "reduce_amount", "单笔买入金额已按你的月度预算上限收敛")

        if advice.action == "buy" and ft == "qdii" and qdii_fx_tolerance == "low":
            self._scale_buy_amount_for_profile(advice, 0.5, "你对 QDII 汇率波动容忍度较低，已下调买入金额")
            self._add_profile_constraint(advice, "QDII 汇率风险", "reduce_amount", "低汇率风险容忍度下，QDII 买入金额已下调")
        elif advice.action == "buy" and ft == "qdii" and qdii_fx_tolerance == "medium":
            self._add_profile_constraint(advice, "QDII 汇率风险", "warning", "QDII 存在汇率波动风险，请结合汇率判断分批执行")

        if advice.action == "buy" and fee_sensitivity == "high" and advice.fee_estimate and advice.fee_estimate.fee_impact_pct > 0.005:
            self._scale_buy_amount_for_profile(advice, 0.8, "你对费率较敏感，已下调高费用交易金额")
            self._add_profile_constraint(advice, "费率敏感度", "reduce_amount", "高费率敏感度下，已降低费用拖累较高的交易金额")

        if advice.action == "buy" and industry_tolerance == "low" and advice.position_after > 0.15:
            self._scale_buy_amount_for_profile(advice, 0.7, "你偏好更分散的配置，单基金集中度已下调")
            self._add_profile_constraint(advice, "集中度偏好", "reduce_amount", "低集中度偏好下，已控制单只基金的目标仓位")
        elif advice.action == "buy" and industry_tolerance == "medium" and advice.position_after > 0.2:
            self._add_profile_constraint(advice, "集中度偏好", "warning", "当前单只基金目标仓位偏高，建议关注集中度风险")

    def _apply_suitability_check(self, advice: TradingAdvice) -> None:
        """根据用户风险偏好和基金估算风险等级做适当性校验。"""
        user_risk = str(self.user_profile.get("risk_level") or "moderate")
        user_risk_num = USER_RISK_LEVEL_MAP.get(user_risk, 3)
        fund_risk_label, fund_risk_num = _estimate_fund_risk_level(
            advice.fund_type,
            advice.risk_position,
        )
        matched = fund_risk_num <= user_risk_num
        check = SuitabilityCheck(
            user_risk_level=user_risk,
            fund_risk_level=fund_risk_label,
            matched=matched,
        )

        if not matched:
            check.warning = f"该基金估算风险等级 {fund_risk_label} 高于当前用户风险偏好，建议谨慎操作"
            if check.warning not in advice.risk_warnings:
                advice.risk_warnings.append(check.warning)
            if advice.action == "buy" and advice.suggested_amount > 0:
                original_amount = advice.suggested_amount
                advice.suggested_amount = round(original_amount * 0.5, 2)
                advice.estimated_gross_amount = advice.suggested_amount
                advice.suggested_pct = (
                    advice.suggested_amount / self.total_capital
                    if self.total_capital > 0 else 0.0
                )
                advice.position_after = (
                    (self._current_position_value(advice.fund_code) + advice.suggested_amount) / self.total_capital
                    if self.total_capital > 0 else 0.0
                )
                if advice.fee_estimate:
                    advice.fee_estimate.estimated_fee = (
                        advice.suggested_amount * advice.fee_estimate.subscribe_fee_rate
                    )
                    advice.fee_estimate.net_trade_amount = (
                        advice.suggested_amount - advice.fee_estimate.estimated_fee
                    )
                    advice.fee_estimate.fee_impact_pct = (
                        advice.fee_estimate.estimated_fee / advice.suggested_amount
                        if advice.suggested_amount > 0 else 0.0
                    )
                    advice.estimated_net_amount = advice.fee_estimate.net_trade_amount
                else:
                    advice.estimated_net_amount = advice.suggested_amount
                check.action_adjusted = True
                advice.reasons.append(
                    f"因风险等级不匹配，买入金额由 ¥{original_amount:,.0f} 下调至 ¥{advice.suggested_amount:,.0f}"
                )

        advice.suitability = check

    def _classify_advice_strength(self, advice: TradingAdvice) -> str:
        if advice.action in {"buy", "sell"}:
            if advice.confidence >= 0.75:
                return "strong"
            if advice.confidence >= 0.45:
                return "medium"
            return "weak"
        if advice.action == "watch":
            if advice.confidence >= 0.6:
                return "medium"
            return "weak"
        if advice.confidence >= 0.7:
            return "medium"
        return "weak"

    def _build_trade_plan(self, advice: TradingAdvice) -> TradePlan:
        """构建交易计划，提供金额区间、分批建议和目标仓位。"""
        current_value = self._current_position_value(advice.fund_code)
        current_weight = current_value / self.total_capital if self.total_capital > 0 else 0.0
        target_weight = advice.position_after if self.total_capital > 0 else 0.0
        suggested = max(0.0, advice.suggested_amount)

        plan = TradePlan(
            suggested_amount=suggested,
            min_amount=max(self.config.min_trade_amount, suggested * 0.5) if suggested > 0 else 0.0,
            max_amount=min(self.total_capital * self.config.max_daily_trade_pct, suggested * 1.5) if suggested > 0 else 0.0,
            current_weight=current_weight,
            target_weight=target_weight,
        )

        if advice.action == "buy":
            high_vol = bool(
                advice.momentum
                and advice.momentum.vol_percentile is not None
                and advice.momentum.vol_percentile > 0.8
            )
            if high_vol or advice.confidence < 0.55:
                plan.execution_type = "batch"
                plan.batch_count = 3
                plan.batch_interval_days = 7
                plan.explanation = "当前波动或信号不确定性偏高，可考虑分批小额增配以降低择时风险"
            else:
                plan.execution_type = "one_time"
                plan.explanation = "当前信号相对明确，可将小额增配作为候选方案复核"
        elif advice.action == "sell":
            if advice.confidence >= 0.65:
                plan.execution_type = "one_time"
                plan.explanation = "减配候选信号较强，可将一次性执行作为复核方案"
            else:
                plan.execution_type = "batch"
                plan.batch_count = 2
                plan.batch_interval_days = 5
                plan.explanation = "减配候选信号为中等强度，可考虑分批降低仓位"
        elif advice.action == "watch":
            plan.execution_type = "hold"
            plan.explanation = "当前信号接近交易阈值，可先观察净值、信号和数据质量变化，再决定是否执行"
        else:
            plan.execution_type = "hold"
            plan.explanation = "当前没有足够强的可复核交易信号，可暂不操作并等待新数据"

        personalization = self.user_profile.get("advisor_personalization") if isinstance(self.user_profile.get("advisor_personalization"), dict) else {}
        if personalization and float(personalization.get("confidence") or 0.0) >= 0.2 and advice.action in {"buy", "sell"}:
            style = str(personalization.get("preferred_execution_style") or "neutral")
            preferred_count = personalization.get("preferred_batch_count")
            preferred_interval = personalization.get("preferred_batch_interval_days")
            if style in {"batch", "small_steps", "slower_cadence"}:
                plan.execution_type = "batch"
                if preferred_count is not None:
                    plan.batch_count = int(max(2, min(4, int(preferred_count))))
                elif plan.batch_count is None:
                    plan.batch_count = 3 if style != "slower_cadence" else 2
                if preferred_interval is not None:
                    plan.batch_interval_days = int(max(5, min(14, int(preferred_interval))))
                elif plan.batch_interval_days is None:
                    plan.batch_interval_days = 10 if style != "slower_cadence" else 12
                learned_note = "已结合你的历史执行记录，把本次计划调整为更匹配的分批节奏"
                plan.explanation = f"{plan.explanation}；{learned_note}" if plan.explanation else learned_note

        plan.triggers = self._build_trade_plan_triggers(advice, plan)
        return plan

    def _build_trade_plan_triggers(self, advice: TradingAdvice, plan: TradePlan) -> list[TradePlanTrigger]:
        """为交易计划补充结构化条件触发规则。"""
        triggers: list[TradePlanTrigger] = []

        if advice.action == "buy":
            triggers.append(TradePlanTrigger(
                trigger_type="stop_buy",
                condition=f"本基金仓位达到目标仓位 {plan.target_weight:.1%} 左右",
                action="暂停继续增配",
                reason="达到目标仓位后继续增配会抬高集中度与回撤风险",
                severity="info",
            ))
            if plan.execution_type == "batch":
                triggers.append(TradePlanTrigger(
                    trigger_type="pause_buy",
                    condition="下一批执行前，若数据质量降为 poor 或过拟合风险升至 high",
                    action="暂停后续批次，先刷新建议",
                    reason="分批计划依赖后续数据仍然可靠，质量/泛化恶化时不应机械加仓",
                    severity="high",
                ))
            if advice.momentum and advice.momentum.vol_percentile is not None and advice.momentum.vol_percentile > 0.8:
                triggers.append(TradePlanTrigger(
                    trigger_type="review",
                    condition="若近期波动继续处于历史高位（波动率分位维持在高位）",
                    action="缩小单笔金额或延长批次间隔",
                    reason="高波动环境下应优先控制节奏，避免一次性承担过大择时风险",
                    severity="warning",
                ))
        elif advice.action == "sell":
            triggers.append(TradePlanTrigger(
                trigger_type="reduce_position",
                condition=f"卖出后本基金仓位回落到目标仓位 {plan.target_weight:.1%} 附近",
                action="停止继续减仓",
                reason="达到目标仓位后继续卖出可能导致仓位过低或偏离原配置目标",
                severity="info",
            ))
            if plan.execution_type == "batch":
                triggers.append(TradePlanTrigger(
                    trigger_type="review",
                    condition="两次卖出间若信号重新回到持有区间",
                    action="暂停第二笔卖出并重新评估",
                    reason="中等强度卖出信号更适合边执行边确认，避免在噪声波动中连续减仓",
                    severity="warning",
                ))
        else:
            triggers.append(TradePlanTrigger(
                trigger_type="refresh",
                condition="数据质量、过拟合风险、净值波动或用户资金/风险偏好发生明显变化",
                action="刷新建议后再决定是否交易",
                reason="当前建议以观察/持有为主，后续是否执行取决于新数据是否带来更清晰信号",
                severity="info",
            ))

        if advice.data_quality and advice.data_quality.status in {"warning", "poor"}:
            triggers.append(TradePlanTrigger(
                trigger_type="refresh",
                condition=f"数据质量维持 {advice.data_quality.status} 或继续变差",
                action="优先刷新建议，避免直接执行",
                reason="当前交易判断已受到数据质量门禁影响，应等待更完整或更稳定的数据",
                severity="high" if advice.data_quality.status == "poor" else "warning",
            ))
        if advice.overfit_risk and advice.overfit_risk.level in {"medium", "high"}:
            triggers.append(TradePlanTrigger(
                trigger_type="refresh",
                condition=f"过拟合风险保持在 {advice.overfit_risk.level} 或进一步升高",
                action="降低执行力度，必要时转为观察",
                reason="样本外稳健性不足时，历史最优信号未必能在未来稳定复现",
                severity="high" if advice.overfit_risk.level == "high" else "warning",
            ))
        return triggers[:4]

    def _build_portfolio_impact(self, advice: TradingAdvice) -> PortfolioImpact:
        """估算本条建议执行后的仓位和风险变化。"""
        current_value = self._current_position_value(advice.fund_code)
        before_weight = current_value / self.total_capital if self.total_capital > 0 else 0.0
        after_weight = advice.position_after if self.total_capital > 0 else before_weight
        position_change = after_weight - before_weight
        ft = (advice.fund_type or "").lower()

        risk_change = "unchanged"
        if advice.action == "buy" and ft in RISK_ASSET_TYPES:
            risk_change = "increase"
        elif advice.action == "sell" and ft in RISK_ASSET_TYPES:
            risk_change = "decrease"
        elif advice.action == "buy" and ft == "bond":
            risk_change = "unchanged"
        elif advice.action == "sell" and ft == "bond":
            risk_change = "unchanged"

        concentration_warning = None
        if after_weight > self.config.max_single_position * 0.9:
            concentration_warning = "操作后单只基金仓位接近上限，需注意集中度风险"
            if concentration_warning not in advice.risk_warnings:
                advice.risk_warnings.append(concentration_warning)

        explanation = f"该操作会使本基金仓位从 {before_weight:.1%} 变为 {after_weight:.1%}"
        return PortfolioImpact(
            before_weight=before_weight,
            after_weight=after_weight,
            position_change=position_change,
            risk_change=risk_change,
            concentration_warning=concentration_warning,
            explanation=explanation,
        )

    def _build_reasoning(self, advice: TradingAdvice) -> AdviceReasoning:
        """把分散的分数字段汇总成结构化、可解释的决策链。"""
        factors: list[ReasonFactor] = [
            ReasonFactor(
                name="综合评分",
                impact="positive" if advice.composite_score > 0 else "negative" if advice.composite_score < 0 else "neutral",
                score=advice.composite_score,
                explanation=f"多维信号融合后的综合得分为 {advice.composite_score:.2f}",
            )
        ]

        if advice.momentum:
            factors.append(ReasonFactor(
                name="动量趋势",
                impact="positive" if advice.momentum_score > 0.15 else "negative" if advice.momentum_score < -0.15 else "neutral",
                score=advice.momentum_score,
                explanation="基于近 20/60 日收益、波动率分位和均值回复状态判断",
            ))
        if advice.strategy_score != 0:
            factors.append(ReasonFactor(
                name="策略信号",
                impact="positive" if advice.strategy_score > 0 else "negative",
                score=advice.strategy_score,
                explanation="来自已配置策略的最新信号，并按新鲜度做衰减",
            ))
        if advice.cross_sectional_score != 0:
            factors.append(ReasonFactor(
                name="同类排名",
                impact="positive" if advice.cross_sectional_score > 0 else "negative",
                score=advice.cross_sectional_score,
                explanation="截面因子用于判断该基金相对同类基金的优劣",
            ))
        if advice.risk_position:
            factors.append(ReasonFactor(
                name="风险预算",
                impact="neutral",
                score=advice.risk_position.suggested_position_pct,
                explanation=f"风险预算模型建议目标仓位约 {advice.risk_position.suggested_position_pct:.1%}",
            ))
        if advice.fee_estimate:
            factors.append(ReasonFactor(
                name="交易成本",
                impact="negative" if advice.fee_estimate.fee_impact_pct > 0.005 else "neutral",
                score=advice.fee_estimate.fee_impact_pct,
                explanation=f"预估费用占交易金额 {advice.fee_estimate.fee_impact_pct:.2%}",
            ))
        if advice.reliability_adjustment:
            factors.append(ReasonFactor(
                name="样本外可靠性",
                impact="negative" if advice.reliability_adjustment.multiplier < 1.0 else "neutral",
                score=advice.reliability_adjustment.multiplier,
                explanation=advice.reliability_adjustment.reason or "根据历史跟踪健康度评估信号可靠性",
            ))
        for constraint in advice.profile_constraints:
            if constraint.triggered:
                factors.append(ReasonFactor(
                    name=constraint.name,
                    impact="negative" if constraint.effect in {"reduce_amount", "hold", "warning"} else "neutral",
                    score=None,
                    explanation=constraint.explanation,
                ))

        if advice.action == "buy":
            summary = f"可考虑增配候选 ¥{advice.suggested_amount:,.0f}，置信度 {advice.confidence:.0%}。"
        elif advice.action == "sell":
            summary = f"可考虑减配候选约 ¥{advice.suggested_amount:,.0f}，置信度 {advice.confidence:.0%}。"
        elif advice.action == "watch":
            summary = "当前可重点观察，信号已接近交易阈值，但仍需复核后再决定是否执行。"
        else:
            summary = "当前维持观察，等待更明确的可复核信号。"
        if advice.data_quality and advice.data_quality.status != "good":
            factors.append(ReasonFactor(
                name="数据质量",
                impact="negative" if advice.data_quality.status == "poor" else "neutral",
                score=advice.data_quality.score,
                explanation=f"数据质量等级 {advice.data_quality.status}，已影响建议强度",
            ))
        if advice.overfit_risk:
            factors.append(ReasonFactor(
                name="过拟合风险",
                impact="negative" if advice.overfit_risk.level in {"medium", "high"} else "neutral",
                score=advice.overfit_risk.score,
                explanation=f"过拟合风险等级 {advice.overfit_risk.level}，{';'.join(advice.overfit_risk.reasons[:2])}",
            ))
        if advice.risk_warnings:
            summary += f"需关注 {len(advice.risk_warnings)} 条风险提示。"

        high_trust_allowed = (
            advice.confidence >= 0.7
            and (not advice.data_quality or advice.data_quality.status in {"good", "warning"})
            and (not advice.overfit_risk or advice.overfit_risk.level not in {"high", "unknown"})
            and advice.oos_validation_status == "available"
        )
        if high_trust_allowed:
            confidence_level = "high"
        elif advice.confidence >= 0.4:
            confidence_level = "medium"
        else:
            confidence_level = "low"
        if advice.confidence >= 0.7 and not high_trust_allowed:
            factors.append(ReasonFactor(
                name="可信度门禁",
                impact="negative",
                score=advice.confidence,
                explanation="数据质量或样本外验证未同时满足，不能标记为较高可信",
            ))
        return AdviceReasoning(
            summary=summary,
            confidence_level=confidence_level,
            factors=factors,
        )

    def _build_decision_audit(
        self,
        advice: TradingAdvice,
        nav_records: list[tuple[str, float]],
    ) -> DecisionAudit:
        """生成决策审计信息，记录阈值、权重、数据质量和市场状态。"""
        buy_threshold = float(getattr(advice, "_effective_buy_threshold", self.config.buy_threshold))
        sell_threshold = float(getattr(advice, "_effective_sell_threshold", self.config.sell_threshold))
        score = advice.composite_score

        if advice.action == "buy":
            threshold_state = "above_buy_threshold"
            threshold_margin = score - buy_threshold
        elif advice.action == "sell":
            threshold_state = "below_sell_threshold"
            threshold_margin = sell_threshold - score
        else:
            threshold_state = "within_hold_band"
            if score >= 0:
                threshold_margin = score - buy_threshold
            else:
                threshold_margin = sell_threshold - score

        nav_count = len(nav_records)
        data_quality = advice.data_quality.to_dict() if advice.data_quality else build_advisor_data_quality_report(
            advice.fund_code,
            nav_records,
            as_of_date=self.as_of_date,
            lookback_days=self.config.lookback_days,
            prediction_sample_size=advice.prediction.sample_size if advice.prediction else 0,
            current_volatility=advice.momentum.current_vol if advice.momentum else None,
            volatility_percentile=advice.momentum.vol_percentile if advice.momentum else None,
            nav_diagnostics=self.nav_quality_diagnostics.get(advice.fund_code),
        ).to_dict()
        overfit_risk = advice.overfit_risk.to_dict() if advice.overfit_risk else {}
        signal_contributions, dominant_signal = _build_signal_contributions(advice)

        notes: list[str] = []
        missing_sources = int(getattr(advice, "_missing_sources", 0))
        if missing_sources:
            notes.append(f"{missing_sources} 个信号源不可用，已触发权重重分配/动态阈值")
        if getattr(advice, "_learned_adjustments_applied", False):
            notes.append("已应用历史反馈学习参数（含收缩限制，避免贴合历史噪声）")
        learned_multipliers = getattr(advice, "_learned_weight_multipliers", None)
        if learned_multipliers:
            notes.append(
                "学习权重乘数：" + ", ".join(
                    f"{k}={v:.2f}" for k, v in learned_multipliers.items()
                )
            )
        learned_threshold_adj = getattr(advice, "_learned_threshold_adjustment", None)
        if learned_threshold_adj is not None:
            notes.append(f"学习阈值调整：{learned_threshold_adj:+.3f}")
        if advice.suitability and not advice.suitability.matched:
            notes.append("适当性不匹配，买入金额可能已下调")
        if advice.profile_constraints:
            notes.append("已应用投资画像约束")
        if advice.data_quality:
            if advice.data_quality.status != "good":
                notes.append(f"数据质量为 {advice.data_quality.status}，已纳入交易门禁")
            notes.extend(advice.data_quality.warnings[:3])
        if advice.overfit_risk:
            if advice.overfit_risk.level != "low":
                notes.append(f"过拟合风险为 {advice.overfit_risk.level}，已纳入交易门禁")
            notes.extend(advice.overfit_risk.reasons[:3])
        if advice.reliability_adjustment and advice.reliability_adjustment.multiplier < 1.0:
            notes.append(
                f"已应用防过拟合可靠性折扣（{advice.reliability_adjustment.multiplier:.0%}）"
            )
        if nav_count < 120:
            notes.append("净值样本少于 120 条，统计估计稳定性较弱")

        return DecisionAudit(
            effective_buy_threshold=buy_threshold,
            effective_sell_threshold=sell_threshold,
            threshold_state=threshold_state,
            threshold_margin=threshold_margin,
            missing_sources=missing_sources,
            signal_weights=dict(getattr(advice, "_signal_weights", {})),
            signal_availability=dict(getattr(advice, "_signal_availability", {})),
            signal_contributions=signal_contributions,
            dominant_signal=dominant_signal,
            data_quality=data_quality,
            overfit_risk=overfit_risk,
            market_regime=getattr(advice, "_market_regime_audit", None),
            notes=notes,
        )

    def _build_validity(
        self,
        advice: TradingAdvice,
        nav_records: list[tuple[str, float]],
    ) -> AdviceValidity:
        """生成建议有效期和失效条件。"""
        generated = datetime.now(ZoneInfo("Asia/Shanghai"))
        data_as_of = nav_records[-1][0] if nav_records else self.as_of_date.isoformat()
        valid_until = (self.as_of_date + timedelta(days=5)).isoformat()
        invalidation_rules = [
            "基金暂停申购或赎回",
            "用户持仓、可用资金或风险偏好发生明显变化",
            "基金净值出现大幅波动",
            "市场波动状态发生明显变化",
            "超过建议有效期",
        ]
        if (advice.fund_type or "").lower() == "qdii":
            invalidation_rules.append("境外市场假期、汇率或 QDII 额度发生明显变化")
        return AdviceValidity(
            generated_at=generated.isoformat(),
            data_as_of=str(data_as_of),
            valid_until=valid_until,
            invalidation_rules=invalidation_rules,
        )

    def _detect_portfolio_regime(
        self, nav_data: dict[str, list[tuple[str, float]]]
    ) -> MarketRegime:
        """检测组合层面的市场 regime（多基金投票法）。

        对所有数据充足的基金分别检测 regime，取多数投票结果。
        避免单只基金（可能是债券/QDII）误导整体市场状态判断。
        """
        if not nav_data:
            return MarketRegime()

        # 对所有数据充足的基金检测 regime
        regimes: list[MarketRegime] = []
        for code, records in nav_data.items():
            navs = [r[1] for r in records]
            if len(navs) >= 120:
                r = detect_market_regime(navs, self.config)
                regimes.append(r)

        if not regimes:
            return MarketRegime()

        # 单只基金时直接返回
        if len(regimes) == 1:
            return regimes[0]

        # 多数投票：统计各 regime 出现次数
        from collections import Counter
        regime_counts = Counter(r.regime for r in regimes)
        majority_regime = regime_counts.most_common(1)[0][0]

        # 取多数 regime 中置信度最高的作为代表
        candidates = [r for r in regimes if r.regime == majority_regime]
        best = max(candidates, key=lambda r: r.regime_confidence)

        # 如果投票不一致（最多 regime 占比 < 50%），降低置信度
        majority_ratio = regime_counts[majority_regime] / len(regimes)
        if majority_ratio < 0.5:
            best.regime_confidence *= 0.6
            # v5: 分歧较大时，更强烈地向 normal 靠拢
            best.signal_weight_multiplier = (
                best.signal_weight_multiplier * 0.3 + 1.0 * 0.7
            )
            best.hold_bias *= 0.3

        return best

    def _analyze_fund(
        self,
        fund_code: str,
        nav_records: list[tuple[str, float]],
        signal: dict[str, Any] | None,
        fund_name: str | None,
        fund_type: str | None,
        sub_type: str | None,
        fee_info: dict[str, float] | None,
        today_str: str,
        n_funds: int,
        market_regime: MarketRegime | None = None,
    ) -> TradingAdvice:
        """分析单只基金并生成建议（v5 增强：自适应权重 + 动态阈值 + 信号共识加成）。"""
        advice = TradingAdvice(
            fund_code=fund_code,
            fund_name=fund_name,
            fund_type=fund_type,
            advice_date=today_str,
        )
        advice._as_of_date = self.as_of_date

        nav_diagnostics = self.nav_quality_diagnostics.get(fund_code)
        preliminary_quality = build_advisor_data_quality_report(
            fund_code,
            nav_records,
            as_of_date=self.as_of_date,
            lookback_days=self.config.lookback_days,
            nav_diagnostics=nav_diagnostics,
        )
        advice.data_quality = preliminary_quality

        # 货币基金不需要交易建议
        if fund_type and fund_type.lower() == "money":
            advice.action = "hold"
            advice.reasons.append("货币基金无需择时，建议作为现金管理工具持有")
            advice.limitations.append("货币基金收益稳定，技术分析和动量分析不适用")
            _attach_trade_timing(advice)
            return advice

        if not nav_records or len(nav_records) < 60:
            advice.action = "hold"
            advice.reasons.append("历史数据不足（少于60个交易日），无法生成可靠建议")
            advice.limitations.append("样本量不足，统计估计不稳定")
            if preliminary_quality.status == "poor":
                advice.risk_warnings.extend(preliminary_quality.warnings)
            _attach_trade_timing(advice)
            return advice

        # 提取净值序列
        nav_values = [r[1] for r in nav_records]

        # 获取基金类型配置
        profile = _get_fund_profile(fund_type, sub_type)

        # 1. 技术指标分析
        indicators = compute_technical_indicators(
            nav_values, self.config, fund_type, sub_type
        )
        advice.technical = indicators
        advice.technical_score = score_technical(indicators)

        # 2. 动量/均值回复分析
        momentum = compute_momentum_score(nav_values, self.config.lookback_days)
        advice.momentum = momentum
        advice.momentum_score = momentum.momentum_score

        # v5: 动量因子 A 股有效性自适应折扣（温和化）
        if fund_type and fund_type.lower() != "qdii":
            discount = _compute_adaptive_momentum_discount(
                nav_values, self.config.momentum_ashare_discount
            )
            # v5: 应用反馈学习的动量折扣校准
            if self._learned_weights and self._learned_weights.confidence >= 0.3:
                from app.services.advisor_feedback import apply_learned_momentum_discount
                discount = apply_learned_momentum_discount(discount, self._learned_weights)
            advice.momentum_score *= discount

        # 3. 策略信号（v5 增强：延长半衰期到 14 天）
        strategy_score = 0.0
        strategy_available = False
        if signal:
            direction = normalize_trade_direction(signal.get("direction", "hold"))
            strength = signal.get("strength", 0.5)
            target_weight = signal.get("target_weight")
            signal_date_str = signal.get("signal_date")

            # 基础方向评分
            if direction == "subscribe":
                strategy_score = float(strength) if strength else 0.5
                strategy_available = True
            elif direction == "redeem":
                strategy_score = -float(strength) if strength else -0.5
                strategy_available = True

            # v5: 信号新鲜度衰减（半衰期延长到 14 天）
            if signal_date_str and strategy_available:
                try:
                    signal_d = date.fromisoformat(signal_date_str)
                    days_old = (self.as_of_date - signal_d).days
                    if days_old > 0:
                        half_life = self.config.signal_freshness_half_life
                        freshness = 0.5 ** (days_old / half_life)
                        # v5: 最低保留 20% 信号强度（旧信号仍有参考价值）
                        freshness = max(0.2, freshness)
                        strategy_score *= freshness
                except (ValueError, TypeError):
                    pass

            # target_weight 融合
            if target_weight is not None and target_weight > 0:
                advice._strategy_target_weight = float(target_weight)

        advice.strategy_score = strategy_score

        # 4. 截面因子信号
        cross_sectional_score = self.cross_sectional_scores.get(fund_code, 0.0)
        cross_sectional_available = cross_sectional_score != 0.0 or bool(self.cross_sectional_scores)
        advice.cross_sectional_score = cross_sectional_score

        # 5. Bootstrap 预测（v5: 恢复参与决策）
        _pred_seed = _stable_seed(fund_code, today_str)
        prediction = compute_prediction_score(nav_values, self.config, random_seed=_pred_seed)
        advice.prediction = prediction
        advice.prediction_score = prediction.prediction_score
        advice.data_quality = build_advisor_data_quality_report(
            fund_code,
            nav_records,
            as_of_date=self.as_of_date,
            lookback_days=self.config.lookback_days,
            prediction_sample_size=prediction.sample_size,
            current_volatility=momentum.current_vol,
            volatility_percentile=momentum.vol_percentile,
            nav_diagnostics=nav_diagnostics,
        )

        # ===================================================================
        # v5 核心改进：自适应权重重分配 + 动态阈值
        # ===================================================================

        # 获取基础权重
        w_tech = profile["weight_technical"]
        w_mom = profile["weight_momentum"]
        w_strat = profile["weight_strategy"]
        w_pred = profile.get("weight_prediction", 0.10)
        w_cs = profile.get("weight_cross_sectional", 0.0)

        # v5: 应用反馈学习的权重乘数（如果可用）
        learned_applied = bool(
            self._learned_weights and self._learned_weights.confidence >= 0.3
        )
        learned_weight_multipliers: dict[str, float] | None = None
        if learned_applied:
            lw = self._learned_weights
            learned_weight_multipliers = {
                "technical": lw.multiplier_technical,
                "momentum": lw.multiplier_momentum,
                "strategy": lw.multiplier_strategy,
                "prediction": lw.multiplier_prediction,
                "cross_sectional": lw.multiplier_cross_sectional,
            }
            w_tech *= lw.multiplier_technical
            w_mom *= lw.multiplier_momentum
            w_strat *= lw.multiplier_strategy
            w_pred *= lw.multiplier_prediction
            w_cs *= lw.multiplier_cross_sectional

        signal_availability = {
            "technical": bool(indicators.applicable or w_tech > 0),
            "momentum": bool(momentum.momentum_score != 0.0 or len(nav_values) >= 60),
            "strategy": strategy_available,
            "prediction": bool(prediction.sample_size > 0),
            "cross_sectional": cross_sectional_available,
            "macro": bool(self.macro_score != 0.0),
        }

        # v5: 检测哪些信号源实际可用
        # 信号源"不可用"的定义：
        # - 策略信号：没有信号数据（signal 为 None 或 direction 为 hold）
        # - 截面因子：cross_sectional_scores 为空字典
        # - 技术分析：不适用（applicable=False）且权重为0
        missing_sources = 0
        redistributable_weight = 0.0

        if not strategy_available and w_strat > 0:
            missing_sources += 1
            redistributable_weight += w_strat
            w_strat = 0.0  # 策略信号不可用，权重归零

        if not cross_sectional_available and w_cs > 0:
            missing_sources += 1
            redistributable_weight += w_cs
            w_cs = 0.0  # 截面因子不可用，权重归零

        if not indicators.applicable and w_tech > 0:
            missing_sources += 1
            redistributable_weight += w_tech
            w_tech = 0.0

        # v5: 将不可用信号源的权重按比例重分配给可用信号源
        if redistributable_weight > 0:
            available_weight = w_tech + w_mom + w_strat + w_pred + w_cs
            if available_weight > 0:
                # 按现有比例分配
                scale = (available_weight + redistributable_weight) / available_weight
                w_tech *= scale
                w_mom *= scale
                w_strat *= scale
                w_pred *= scale
                w_cs *= scale

        # 归一化权重（确保和为1）
        w_total = w_tech + w_mom + w_strat + w_pred + w_cs
        if w_total > 0:
            w_tech /= w_total
            w_mom /= w_total
            w_strat /= w_total
            w_pred /= w_total
            w_cs /= w_total

        # 计算加权综合分
        # v5: 使用非线性融合（在线性基础上叠加交互项和极端放大）
        try:
            from app.services.nonlinear_fusion import FusionInput, nonlinear_fuse
            fusion_input = FusionInput(
                technical=advice.technical_score,
                momentum=advice.momentum_score,
                strategy=advice.strategy_score,
                prediction=advice.prediction_score,
                cross_sectional=cross_sectional_score,
                macro=self.macro_score,
            )
            fusion_weights = {
                "technical": w_tech,
                "momentum": w_mom,
                "strategy": w_strat,
                "prediction": w_pred,
                "cross_sectional": w_cs,
            }
            fusion_result = nonlinear_fuse(fusion_input, fusion_weights, self.macro_score)
            composite = fusion_result.score
        except Exception:
            # 非线性融合失败时回退到线性加权
            composite = (
                w_tech * advice.technical_score
                + w_mom * advice.momentum_score
                + w_strat * advice.strategy_score
                + w_pred * advice.prediction_score
                + w_cs * cross_sectional_score
            )

        # v5: 宏观因子加成（不占权重，作为额外的方向确认/抑制）
        # 宏观因子与个股信号方向一致时加成，方向相反时抑制
        if self.macro_score != 0.0:
            macro_alignment = self.macro_score * np.sign(composite) if composite != 0 else 0
            if macro_alignment > 0:
                # 宏观与个股方向一致：加成 10~20%
                macro_boost = 1.0 + abs(self.macro_score) * 0.15
                composite *= macro_boost
            elif macro_alignment < 0 and abs(self.macro_score) > 0.3:
                # 宏观与个股方向相反且宏观信号较强：温和抑制
                macro_dampen = 1.0 - abs(self.macro_score) * 0.10
                composite *= macro_dampen

        # v5: 非线性信号共识加成
        # 当多个信号源方向一致时，给予额外加成（信号确认效应）
        signal_directions = []
        if abs(advice.technical_score) > 0.2:
            signal_directions.append(1 if advice.technical_score > 0 else -1)
        if abs(advice.momentum_score) > 0.15:
            signal_directions.append(1 if advice.momentum_score > 0 else -1)
        if abs(advice.strategy_score) > 0.2:
            signal_directions.append(1 if advice.strategy_score > 0 else -1)
        if abs(advice.prediction_score) > 0.15:
            signal_directions.append(1 if advice.prediction_score > 0 else -1)
        if abs(cross_sectional_score) > 0.2:
            signal_directions.append(1 if cross_sectional_score > 0 else -1)

        if len(signal_directions) >= self.config.signal_consensus_threshold:
            # 检查是否多数方向一致
            positive_count = sum(1 for d in signal_directions if d > 0)
            negative_count = sum(1 for d in signal_directions if d < 0)
            total_signals = len(signal_directions)

            if positive_count >= self.config.signal_consensus_threshold:
                # 多信号看多一致，加成
                consensus_ratio = positive_count / total_signals
                boost = 1.0 + (self.config.signal_consensus_boost - 1.0) * consensus_ratio
                composite *= boost
            elif negative_count >= self.config.signal_consensus_threshold:
                # 多信号看空一致，加成（绝对值增大）
                consensus_ratio = negative_count / total_signals
                boost = 1.0 + (self.config.signal_consensus_boost - 1.0) * consensus_ratio
                composite *= boost

        # v5: 市场 regime 温和调整（不再过度压制）
        if market_regime:
            composite *= market_regime.signal_weight_multiplier
            if market_regime.hold_bias > 0:
                composite *= (1.0 - market_regime.hold_bias)

            if market_regime.regime != "normal":
                regime_labels = {
                    "bull": "牛市",
                    "bear": "熊市",
                    "crisis": "危机",
                    "volatile": "高波动震荡",
                }
                regime_label = regime_labels.get(market_regime.regime, market_regime.regime)
                advice.risk_warnings.append(
                    f"当前市场状态: {regime_label}，"
                    f"信号权重调整为 {market_regime.signal_weight_multiplier*100:.0f}%"
                )

        advice.composite_score = float(np.clip(composite, -1.0, 1.0))
        self._apply_reliability_adjustment(advice)

        # v5: 动态阈值计算
        # 当信号源缺失时，降低买卖阈值，确保可用信号能触发决策
        effective_buy_threshold = self.config.buy_threshold
        effective_sell_threshold = self.config.sell_threshold

        # v5: 应用反馈学习的阈值调整
        if self._learned_weights and self._learned_weights.confidence >= 0.3:
            adj = self._learned_weights.threshold_adjustment
            advice._learned_threshold_adjustment = adj
            effective_buy_threshold -= adj
            effective_sell_threshold += adj

        if self.config.adaptive_threshold and missing_sources > 0:
            threshold_reduction = missing_sources * self.config.threshold_decay_per_missing_source
            effective_buy_threshold = max(
                self.config.min_threshold,
                effective_buy_threshold - threshold_reduction,
            )
            effective_sell_threshold = min(
                -self.config.min_threshold,
                effective_sell_threshold + threshold_reduction,
            )

        # 保存有效阈值供 _determine_action 使用
        advice._effective_buy_threshold = effective_buy_threshold
        advice._effective_sell_threshold = effective_sell_threshold
        advice._missing_sources = missing_sources
        advice._signal_weights = {
            "technical": w_tech,
            "momentum": w_mom,
            "strategy": w_strat,
            "prediction": w_pred,
            "cross_sectional": w_cs,
        }
        advice._signal_availability = signal_availability
        advice._learned_adjustments_applied = learned_applied
        if learned_weight_multipliers is not None:
            advice._learned_weight_multipliers = learned_weight_multipliers
        if market_regime:
            advice._market_regime_audit = {
                "regime": market_regime.regime,
                "volatility_state": market_regime.volatility_state,
                "trend_state": market_regime.trend_state,
                "confidence": market_regime.regime_confidence,
                "signal_weight_multiplier": market_regime.signal_weight_multiplier,
                "hold_bias": market_regime.hold_bias,
            }

        # 6. 风险预算仓位
        risk_pos = compute_risk_budget_position(
            nav_values=nav_values,
            total_capital=self.total_capital,
            current_position_value=self.current_positions.get(fund_code, 0.0),
            config=self.config,
            n_funds_in_portfolio=n_funds,
        )
        advice.risk_position = risk_pos

        # 7. 生成操作建议（含费用估算）
        self._determine_action(advice, nav_values, profile, fee_info)

        # 8. 数据质量/过拟合门禁后，再生成交易时点、理由和局限性说明
        self._apply_quality_and_overfit_gates(advice)
        _attach_trade_timing(advice)
        self._generate_reasons(advice, profile)

        return advice

    def _determine_action(
        self,
        advice: TradingAdvice,
        nav_values: list[float],
        profile: dict[str, Any],
        fee_info: dict[str, float] | None,
    ) -> None:
        """根据综合评分确定操作方向和金额，含费用估算（v5: 使用动态阈值）。"""
        score = advice.composite_score
        detail = self.positions_detail.get(advice.fund_code, {})
        current_value = self.current_positions.get(advice.fund_code, 0.0)
        if detail.get("market_value") is not None:
            try:
                current_value = float(detail["market_value"])
            except (TypeError, ValueError):
                pass
        risk_pos = advice.risk_position
        reliability = advice.reliability_adjustment
        if advice.overfit_risk is None:
            advice.overfit_risk = build_advisor_overfit_risk(
                advice,
                engine_health_status=str(self._extract_engine_health_value("status", "not_evaluated") or "not_evaluated"),
                rolling_ic_samples=int(self._extract_engine_health_value("rolling_ic_samples", 0) or 0),
            )

        # v5: 使用动态阈值（由 _analyze_fund 计算）
        buy_threshold = getattr(advice, '_effective_buy_threshold', self.config.buy_threshold)
        sell_threshold = getattr(advice, '_effective_sell_threshold', self.config.sell_threshold)

        # 确定方向
        if score > buy_threshold:
            advice.action = "buy"
        elif score < sell_threshold:
            advice.action = "sell"
        else:
            advice.action = "hold"

        # v5: 置信度计算改进 — 相对于阈值的超额部分
        if advice.action == "buy":
            # 超过阈值越多，置信度越高
            excess = (score - buy_threshold) / (1.0 - buy_threshold) if buy_threshold < 1.0 else 0
            advice.confidence = min(1.0, 0.4 + 0.6 * excess)
        elif advice.action == "sell":
            excess = (sell_threshold - score) / (1.0 + sell_threshold) if sell_threshold > -1.0 else 0
            advice.confidence = min(1.0, 0.4 + 0.6 * excess)
        else:
            advice.confidence = min(1.0, abs(score))

        if reliability:
            advice.confidence = float(np.clip(advice.confidence * reliability.confidence_multiplier, 0.0, 1.0))

        # 紧迫程度
        if abs(score) > self.config.high_confidence_threshold:
            advice.urgency = "high"
        elif abs(score) > self.config.buy_threshold:
            advice.urgency = "normal"
        else:
            advice.urgency = "low"

        # 观望动作：分数接近阈值、但尚未形成明确买卖时，区分于普通 hold
        watch_margin = min(0.08, max(0.03, abs(buy_threshold) * 0.35))
        near_buy_band = buy_threshold - watch_margin <= score <= buy_threshold
        near_sell_band = sell_threshold <= score <= sell_threshold + watch_margin
        if advice.action == "hold" and (near_buy_band or near_sell_band):
            advice.action = "watch"
            advice.reasons.append("当前信号已接近交易阈值，可重点观察后续变化")

        # 计算建议金额
        if advice.action == "buy":
            # 基于风险预算计算买入金额
            if risk_pos and risk_pos.suggested_amount > 0:
                base_amount = risk_pos.suggested_amount
            else:
                # 回退：使用固定比例
                base_amount = self.total_capital * 0.05

            # v3: 融合策略 target_weight（如果策略给出了目标仓位）
            # 策略目标仓位 40% + 风险预算仓位 60% = 最终仓位
            strategy_tw = getattr(advice, '_strategy_target_weight', None)
            if strategy_tw is not None and strategy_tw > 0:
                strategy_target_amount = self.total_capital * strategy_tw - current_value
                strategy_target_amount = max(0.0, strategy_target_amount)
                # 加权融合：风险预算 60%，策略目标 40%
                base_amount = base_amount * 0.6 + strategy_target_amount * 0.4

            # 按置信度调整
            confidence_factor = 0.5 + 0.5 * advice.confidence
            raw_amount = base_amount * confidence_factor
            if reliability:
                raw_amount *= reliability.amount_multiplier

            # 应用风控限制
            max_trade = self.total_capital * self.config.max_daily_trade_pct
            max_position = self.total_capital * self.config.max_single_position
            remaining_room = max(0, max_position - current_value)

            advice.suggested_amount = max(
                0.0,
                min(raw_amount, max_trade, remaining_room),
            )

            # 费用估算
            if self.config.include_fee_estimate and advice.suggested_amount > 0:
                fee_est = estimate_trading_fee(
                    advice.suggested_amount,
                    "buy",
                    advice.fund_type,
                    None,
                    fee_info,
                )
                advice.fee_estimate = fee_est
                advice.estimated_net_amount = fee_est.net_trade_amount

                # 如果费用占比过高（>2%），降低建议金额或改为 hold
                if fee_est.fee_impact_pct > 0.02:
                    advice.risk_warnings.append(
                        f"交易费用占比 {fee_est.fee_impact_pct*100:.2f}%，"
                        f"可复核是否增大单笔金额以摊薄费用"
                    )
            else:
                advice.estimated_net_amount = advice.suggested_amount

            if advice.suggested_amount < self.config.min_trade_amount:
                advice.suggested_amount = 0.0
                advice.action = "watch"
                advice.reasons.append("候选金额低于最小交易额，可继续观察")

            advice.estimated_gross_amount = advice.suggested_amount
            advice.suggested_pct = (
                advice.suggested_amount / self.total_capital
                if self.total_capital > 0 else 0.0
            )
            advice.position_after = (
                (current_value + advice.suggested_amount) / self.total_capital
                if self.total_capital > 0 else 0.0
            )

        elif advice.action == "sell":
            if current_value <= 0:
                advice.action = "watch"
                advice.reasons.append("当前无持仓，暂无减配基础，可继续观察")
                return

            # --- 持有天数和盈亏判断 ---
            buy_date_str = detail.get("buy_date")
            cost = detail.get("cost_basis", detail.get("cost", 0)) or 0
            holding_days = 0

            if buy_date_str:
                try:
                    from datetime import datetime as dt
                    buy_d = dt.strptime(str(buy_date_str), "%Y-%m-%d").date()
                    holding_days = (self.as_of_date - buy_d).days
                except (ValueError, TypeError):
                    pass

            # 持有 < 7 天：惩罚性赎回费 1.5%，强烈阻止卖出
            if holding_days > 0 and holding_days < 7:
                advice.action = "hold"
                advice.reasons.append(
                    f"持有仅 {holding_days} 天，赎回费高达 1.5%，可优先维持观察"
                )
                advice.risk_warnings.append("短期赎回惩罚性费率，优先维持观察")
                return

            # 持有 7~30 天：费率较高，降低卖出倾向
            sell_penalty = 1.0
            if 7 <= holding_days < 30:
                sell_penalty = 0.5  # 信号减半
                advice.risk_warnings.append(
                    f"持有 {holding_days} 天，赎回费率较高，已降低卖出倾向"
                )

            # 浮亏判断：如果亏损且信号不够强，不建议割肉
            # v5: 阈值从 0.5 降低到 0.25，与新的动态阈值体系匹配
            if cost > 0 and current_value < cost:
                loss_pct = (current_value - cost) / cost
                if abs(score) < 0.22:
                    advice.action = "hold"
                    advice.reasons.append(
                        f"当前浮亏 {loss_pct*100:.1f}%，信号强度不足，维持观察"
                    )
                    return
                else:
                    advice.risk_warnings.append(
                        f"当前浮亏 {loss_pct*100:.1f}%，卖出将实现亏损"
                    )

            # 浮盈判断：盈利较大时可适当止盈
            if cost > 0 and current_value > cost * 1.2:
                profit_pct = (current_value - cost) / cost
                advice.reasons.append(
                    f"当前浮盈 {profit_pct*100:.1f}%，可考虑部分止盈"
                )

            # 卖出比例：综合信号强度 + 浮盈程度 + VaR 风险
            effective_score = abs(score) * sell_penalty

            # 基础比例（基于信号强度）
            if effective_score > 0.7:
                base_sell_ratio = 1.0  # 强烈看空
            elif effective_score > 0.5:
                base_sell_ratio = 0.5  # 中等看空
            else:
                base_sell_ratio = 0.3  # 温和看空

            # 浮盈调整：盈利越多，越倾向多卖（锁定利润）
            profit_boost = 1.0
            if cost > 0 and current_value > cost:
                profit_ratio = (current_value - cost) / cost
                if profit_ratio > 0.5:
                    profit_boost = 1.3  # 盈利>50%，多卖30%
                elif profit_ratio > 0.2:
                    profit_boost = 1.15  # 盈利>20%，多卖15%

            # VaR 调整：预测风险越大，越倾向多卖
            var_boost = 1.0
            if advice.prediction and advice.prediction.var_95_30d is not None:
                var_30d = abs(advice.prediction.var_95_30d)
                if var_30d > 0.10:
                    var_boost = 1.2  # 30日VaR>10%，多卖20%
                elif var_30d > 0.05:
                    var_boost = 1.1

            sell_ratio = min(1.0, base_sell_ratio * profit_boost * var_boost)
            if reliability and reliability.status in {"unhealthy", "unknown", "insufficient_data"}:
                # 卖出通常是风险控制动作，不像买入一样大幅压制；但低可靠时避免过度清仓。
                sell_ratio = max(0.1, sell_ratio * max(0.7, reliability.amount_multiplier))

            advice.suggested_amount = current_value * sell_ratio

            # 若持仓详情中提供了份额数量，则同步给出建议赎回份额
            detail_shares = detail.get("shares", detail.get("amount"))
            if detail_shares is not None:
                try:
                    current_shares = float(detail_shares)
                    advice._current_shares = current_shares
                    advice.suggested_shares = max(0.0, current_shares * sell_ratio)
                except (TypeError, ValueError):
                    advice.suggested_shares = None

            # 卖出理由补充
            if sell_ratio >= 1.0:
                advice.reasons.append("减配信号较强，可考虑清仓候选")
            elif sell_ratio >= 0.5:
                advice.reasons.append(f"可考虑减配 {sell_ratio*100:.0f}%")
            else:
                advice.reasons.append(f"可考虑小幅减配 {sell_ratio*100:.0f}%")

            # 费用估算（使用实际持有天数计算费率）
            if self.config.include_fee_estimate:
                fee_est = estimate_trading_fee(
                    advice.suggested_amount,
                    "sell",
                    advice.fund_type,
                    None,
                    fee_info,
                    holding_days=holding_days,
                )
                advice.fee_estimate = fee_est
                advice.estimated_net_amount = fee_est.net_trade_amount
            else:
                advice.estimated_net_amount = advice.suggested_amount

            advice.estimated_gross_amount = advice.suggested_amount

            advice.suggested_pct = (
                advice.suggested_amount / self.total_capital
                if self.total_capital > 0 else 0.0
            )
            advice.position_after = (
                (current_value - advice.suggested_amount) / self.total_capital
                if self.total_capital > 0 else 0.0
            )

    def _generate_reasons(
        self, advice: TradingAdvice, profile: dict[str, Any]
    ) -> None:
        """生成人类可读的建议理由、风险提示和局限性说明。"""
        reasons = advice.reasons
        warnings = advice.risk_warnings
        limitations = advice.limitations

        # 技术面理由（仅适用时展示）
        if advice.technical and advice.technical.applicable:
            ti = advice.technical
            if ti.macd_signal == "bullish":
                reasons.append("MACD 金叉，短期动能转强")
            elif ti.macd_signal == "bearish":
                reasons.append("MACD 死叉，短期动能减弱")

            if ti.rsi_signal == "oversold" and ti.rsi_14 is not None:
                reasons.append(f"RSI({ti.rsi_14:.1f}) 进入超卖区域，存在反弹机会")
            elif ti.rsi_signal == "overbought" and ti.rsi_14 is not None:
                reasons.append(f"RSI({ti.rsi_14:.1f}) 进入超买区域，注意回调风险")

            if ti.trend_score > 0.5:
                reasons.append("均线多头排列，中长期趋势向好")
            elif ti.trend_score < -0.5:
                reasons.append("均线空头排列，中长期趋势偏弱")

        elif advice.technical and not advice.technical.applicable:
            limitations.append(
                f"技术分析对{profile.get('label', '该类型')}基金适用性有限，"
                f"已降低其权重（{profile['weight_technical']*100:.0f}%）"
            )

        # 动量面理由
        if advice.momentum:
            mom = advice.momentum
            if mom.regime == "trending_up":
                reasons.append(
                    f"中期动量向上（60日收益 {mom.return_60d*100:.1f}%），"
                    f"趋势延续概率较高"
                )
            elif mom.regime == "trending_down":
                reasons.append(
                    f"中期动量向下（60日收益 {mom.return_60d*100:.1f}%），"
                    f"趋势可能延续"
                )
            elif mom.regime == "mean_reverting":
                reasons.append("收益率偏离历史均值较远，存在均值回复可能")

            if mom.vol_percentile is not None and mom.vol_percentile > 0.8:
                warnings.append(
                    f"当前波动率处于历史 {mom.vol_percentile*100:.0f}% 分位，"
                    f"市场不确定性较高"
                )

        # 预测面理由
        if advice.prediction:
            pred = advice.prediction
            if pred.prob_positive_30d is not None:
                if pred.prob_positive_30d > 0.6:
                    reasons.append(
                        f"Bootstrap 模拟显示30日正收益概率 "
                        f"{pred.prob_positive_30d*100:.0f}%"
                        f"（基于{pred.sample_size}个历史样本）"
                    )
                elif pred.prob_positive_30d < 0.4:
                    reasons.append(
                        f"Bootstrap 模拟显示30日正收益概率仅 "
                        f"{pred.prob_positive_30d*100:.0f}%"
                    )

            if pred.confidence_band_width and pred.confidence_band_width > 0.15:
                warnings.append(
                    f"预测不确定性较高（90%置信区间宽度 "
                    f"{pred.confidence_band_width*100:.1f}%），信号可靠性降低"
                )

        # 策略信号理由
        if advice.strategy_score > 0.3:
            reasons.append("策略模型显示增配候选信号")
        elif advice.strategy_score < -0.3:
            reasons.append("策略模型显示减配候选信号")

        # v4: 截面因子理由
        if advice.cross_sectional_score > 0.3:
            reasons.append(
                f"截面因子排名靠前（同类基金中相对优秀），"
                f"截面信号 {advice.cross_sectional_score:.2f}"
            )
        elif advice.cross_sectional_score < -0.3:
            reasons.append(
                f"截面因子排名靠后（同类基金中相对较弱），"
                f"截面信号 {advice.cross_sectional_score:.2f}"
            )
        elif advice.cross_sectional_score == 0.0 and not self.cross_sectional_scores:
            limitations.append(
                "截面因子未计算（需要同类基金池≥10只），"
                "权重已自动分配给其他可用信号源"
            )

        # v5: 动态阈值和权重重分配说明
        missing_sources = getattr(advice, '_missing_sources', 0)
        if missing_sources > 0:
            effective_buy = getattr(advice, '_effective_buy_threshold', self.config.buy_threshold)
            reasons.append(
                f"有 {missing_sources} 个信号源不可用，"
                f"买卖阈值已自适应调整为 ±{effective_buy:.2f}（基础 ±{self.config.buy_threshold:.2f}）"
            )

        # 风险预算理由
        if advice.risk_position and advice.action == "buy":
            rp = advice.risk_position
            reasons.append(
                f"风险预算模型建议仓位 {rp.suggested_position_pct*100:.1f}%"
                f"（基于年化波动率 {rp.annualized_vol*100:.1f}%）"
            )

        # 费用提示
        if advice.fee_estimate and advice.fee_estimate.estimated_fee > 0:
            fee = advice.fee_estimate
            warnings.append(
                f"预估交易费用 ¥{fee.estimated_fee:.2f}"
                f"（费率 {fee.subscribe_fee_rate*100:.2f}%）"
                if advice.action == "buy" else
                f"预估赎回费用 ¥{fee.estimated_fee:.2f}"
                f"（费率 {fee.redeem_fee_rate*100:.2f}%）"
            )

        # VaR 风险提示
        if advice.prediction and advice.prediction.var_95_30d is not None:
            warnings.append(
                f"30日 95% VaR: {advice.prediction.var_95_30d*100:.1f}%"
                f"（即有5%概率亏损超过此幅度）"
            )

        if advice.data_quality and advice.data_quality.status != "good":
            warnings.append(f"数据质量等级为 {advice.data_quality.status}，建议降低交易强度")
        if advice.overfit_risk and advice.overfit_risk.level != "low":
            warnings.append(f"过拟合风险等级为 {advice.overfit_risk.level}，样本外证据不足时应优先观望")
        if advice.overfit_risk and advice.overfit_risk.pbo is not None and advice.overfit_risk.pbo >= 0.5:
            warnings.append(f"CPCV/PBO={advice.overfit_risk.pbo:.0%}，提示参数或信号组合可能过度贴合历史")
        if advice.confidence < 0.4:
            warnings.append("信号置信度较低，建议小仓位试探或继续观望")

        # 申赎延迟提示（v3 增强：含交易时间规则）
        settlement = profile.get("settlement_days", 2)
        if advice.action != "hold":
            # 基金交易时间规则提示
            time_warnings = _generate_trading_time_warnings(
                action=advice.action,
                settlement_days=settlement,
                fund_type=advice.fund_type,
            )
            warnings.extend(time_warnings)

        # 模型局限性说明（始终附带）
        limitations.append(
            "本建议基于历史数据的统计分析，假设未来市场环境与样本期相似"
        )
        limitations.append(
            "系统未经过充分的样本外验证，历史有效不代表未来有效"
        )
        if advice.action != "hold":
            limitations.append(
                "建议仅供参考，不构成投资建议，请结合自身情况独立决策"
            )

        # 动量因子在 A 股市场的特殊说明
        if advice.momentum and abs(advice.momentum_score) > 0.3:
            limitations.append(
                "动量因子在 A 股市场近年（2017年后）有效性显著下降，"
                "动量信号的可靠性可能低于历史回测表现"
            )

        # 特定场景的额外局限性
        if advice.prediction and advice.prediction.sample_size < 120:
            limitations.append(
                f"预测模型样本量较少（{advice.prediction.sample_size}天），"
                f"估计可能不稳定"
            )


# ---------------------------------------------------------------------------
# 数据库数据加载辅助函数
# ---------------------------------------------------------------------------


async def load_nav_data_for_advisor(
    fund_codes: list[str],
    session: Any,
    lookback_days: int = 750,
    as_of_date: date | None = None,
) -> dict[str, list[tuple[str, float]]]:
    """从数据库加载基金净值数据供 Advisor 使用。

    Args:
        fund_codes: 基金代码列表
        session: AsyncSession 实例
        lookback_days: 回看天数

    Returns:
        {fund_code: [(date_str, nav), ...]} 按日期升序
    """
    from sqlalchemy import or_, select

    from app.data.models.fund_nav import FundNav

    if not fund_codes:
        return {}

    end_date = as_of_date or date.today()
    start_date = end_date - timedelta(days=lookback_days)

    nav_data: dict[str, list[tuple[str, float]]] = {}
    stmt = (
        select(
            FundNav.fund_code,
            FundNav.trade_date,
            FundNav.adj_nav,
            FundNav.unit_nav,
        )
        .where(
            FundNav.fund_code.in_(fund_codes),
            FundNav.trade_date >= start_date,
            FundNav.trade_date <= end_date,
            or_(FundNav.adj_nav.is_not(None), FundNav.unit_nav.is_not(None)),
        )
        .order_by(FundNav.fund_code, FundNav.trade_date)
    )

    try:
        result = await session.execute(stmt)
    except Exception as exc:
        await _rollback_session_if_possible(session)
        logger.warning("advisor.load_nav_data_error: %s", str(exc))
        return {}

    for fund_code, trade_date, adj_nav, unit_nav in result:
        nav_value = adj_nav if adj_nav is not None else unit_nav
        if nav_value is None:
            continue
        nav_data.setdefault(str(fund_code), []).append((str(trade_date), float(nav_value)))

    return nav_data


async def load_nav_quality_diagnostics_for_advisor(
    fund_codes: list[str],
    session: Any,
    lookback_days: int = 750,
    as_of_date: date | None = None,
) -> dict[str, dict[str, Any]]:
    """加载 Advisor 使用窗口内的 NAV 来源和复权一致性诊断。"""
    from sqlalchemy import or_, select

    from app.data.models.fund_nav import FundNav

    if not fund_codes:
        return {}

    end_date = as_of_date or date.today()
    start_date = end_date - timedelta(days=lookback_days)

    rows_by_fund: dict[str, list[dict[str, Any]]] = {code: [] for code in fund_codes}
    stmt = (
        select(
            FundNav.fund_code,
            FundNav.trade_date,
            FundNav.unit_nav,
            FundNav.adj_nav,
            FundNav.source,
            FundNav.status,
        )
        .where(
            FundNav.fund_code.in_(fund_codes),
            FundNav.trade_date >= start_date,
            FundNav.trade_date <= end_date,
            or_(FundNav.adj_nav.is_not(None), FundNav.unit_nav.is_not(None)),
        )
        .order_by(FundNav.fund_code, FundNav.trade_date)
    )
    try:
        result = await session.execute(stmt)
    except Exception as exc:
        await _rollback_session_if_possible(session)
        logger.warning("advisor.load_nav_quality_error: %s", str(exc))
        result = []

    for row in result:
        rows_by_fund.setdefault(str(row[0]), []).append({
            "trade_date": row[1],
            "unit_nav": row[2],
            "adj_nav": row[3],
            "source": row[4],
            "status": row[5],
        })

    diagnostics: dict[str, dict[str, Any]] = {}
    for code in fund_codes:
        records = rows_by_fund.get(code) or []
        point_count = len(records)
        if not records:
            diagnostics[code] = {
                "source_consistency": {
                    "point_count": 0,
                    "source_count": 0,
                    "primary_source": None,
                    "source_switch_count": 0,
                    "source_switch_ratio": 0.0,
                    "missing_source_count": 0,
                    "sources": {},
                },
                "adjustment_consistency": {
                    "point_count": 0,
                    "adjusted_count": 0,
                    "unit_nav_count": 0,
                    "fallback_to_unit_count": 0,
                    "adjusted_coverage_ratio": None,
                    "factor_jump_count": 0,
                    "factor_jump_dates": [],
                    "missing_unit_count": 0,
                    "missing_adj_count": 0,
                },
                "cross_source_consistency": {
                    "status": "insufficient_sources",
                    "hard_gate": False,
                    "provider_count": 0,
                    "providers": [],
                    "reason": "无 Advisor NAV 数据，无法执行跨源原始对照",
                },
            }
            continue

        source_counts: Counter[str] = Counter()
        missing_source_count = 0
        previous_source: str | None = None
        source_switch_count = 0
        adjusted_count = 0
        unit_nav_count = 0
        fallback_to_unit_count = 0
        missing_unit_count = 0
        missing_adj_count = 0
        factor_jump_dates: list[str] = []
        previous_factor: float | None = None

        for item in records:
            raw_source = item.get("source")
            source = str(raw_source).strip() if raw_source is not None and str(raw_source).strip() else None
            if source:
                source_counts[source] += 1
            else:
                missing_source_count += 1
                source = "unknown"
            if previous_source is not None and source != previous_source:
                source_switch_count += 1
            previous_source = source

            unit_nav = item.get("unit_nav")
            adj_nav = item.get("adj_nav")
            if unit_nav is not None:
                unit_nav_count += 1
            else:
                missing_unit_count += 1
            if adj_nav is not None:
                adjusted_count += 1
            else:
                missing_adj_count += 1
                if unit_nav is not None:
                    fallback_to_unit_count += 1
            if unit_nav is not None and adj_nav is not None:
                try:
                    unit_float = float(unit_nav)
                    adj_float = float(adj_nav)
                    if unit_float > 0:
                        factor = adj_float / unit_float
                        if previous_factor is not None and previous_factor > 0:
                            rel_change = abs(factor - previous_factor) / previous_factor
                            if rel_change > 0.20:
                                factor_jump_dates.append(str(item.get("trade_date")))
                        previous_factor = factor
                except (TypeError, ValueError, ZeroDivisionError):
                    pass

        primary_source = source_counts.most_common(1)[0][0] if source_counts else None
        diagnostics[code] = {
            "source_consistency": {
                "point_count": point_count,
                "source_count": len(source_counts),
                "primary_source": primary_source,
                "source_switch_count": source_switch_count,
                "source_switch_ratio": round(source_switch_count / max(1, point_count), 4),
                "missing_source_count": missing_source_count,
                "sources": dict(source_counts),
            },
            "adjustment_consistency": {
                "point_count": point_count,
                "adjusted_count": adjusted_count,
                "unit_nav_count": unit_nav_count,
                "fallback_to_unit_count": fallback_to_unit_count,
                "adjusted_coverage_ratio": round(adjusted_count / max(1, point_count), 4),
                "factor_jump_count": len(factor_jump_dates),
                "factor_jump_dates": factor_jump_dates[:10],
                "missing_unit_count": missing_unit_count,
                "missing_adj_count": missing_adj_count,
            },
            "cross_source_consistency": {
                "status": "pass" if len(source_counts) >= 2 else "insufficient_sources",
                "hard_gate": False,
                "provider_count": len(source_counts),
                "providers": sorted(source_counts.keys()),
                "reason": (
                    "fund_nav 窗口存在多个已入库来源，但缺少原始同日对照告警"
                    if len(source_counts) >= 2
                    else "fund_nav 窗口少于 2 个来源，无法执行同日跨源原始对照"
                ),
            },
        }

    return diagnostics


async def load_strategy_signals_for_advisor(
    fund_codes: list[str],
    session: Any,
    strategy_id: int | None = None,
    as_of_date: date | None = None,
) -> dict[str, dict[str, Any]]:
    """加载最新的策略信号。

    修正：根据策略调仓频率动态设置时间窗口，
    而非固定7天（避免遗漏低频策略信号）。

    Args:
        fund_codes: 基金代码列表
        session: AsyncSession 实例
        strategy_id: 可选，指定策略 ID

    Returns:
        {fund_code: {direction, strength, target_weight, reason}}
    """
    from sqlalchemy import text

    if not fund_codes:
        return {}

    signals: dict[str, dict[str, Any]] = {}

    # 扩大时间窗口到90天，覆盖季频策略
    placeholders = ", ".join([f":code_{i}" for i in range(len(fund_codes))])
    strategy_filter = ""
    if strategy_id is not None:
        strategy_filter = "AND strategy_id = :strategy_id "

    query = text(
        f"SELECT fund_code, direction, strength, target_weight, reason, signal_date "
        f"FROM ("
        f"  SELECT s.fund_code, s.direction, s.strength, s.target_weight, s.reason, s.signal_date, "
        f"         ROW_NUMBER() OVER ("
        f"             PARTITION BY s.fund_code "
        f"             ORDER BY s.signal_date DESC, s.created_at DESC, s.id DESC"
        f"         ) as rn "
        f"  FROM signals s "
        f"  WHERE s.fund_code IN ({placeholders}) "
        f"    AND s.signal_date >= :min_date "
        f"    AND s.signal_date <= :max_date "
        f"    {strategy_filter}"
        f") ranked "
        f"WHERE rn = 1"
    )

    params: dict[str, Any] = {
        f"code_{i}": code for i, code in enumerate(fund_codes)
    }
    max_date = as_of_date or date.today()
    params["min_date"] = max_date - timedelta(days=90)  # 扩大到90天
    params["max_date"] = max_date
    if strategy_id is not None:
        params["strategy_id"] = strategy_id

    try:
        result = await session.execute(query, params)
        for row in result:
            signals[row[0]] = {
                "direction": row[1],
                "strength": float(row[2]) if row[2] else None,
                "target_weight": float(row[3]) if row[3] else None,
                "reason": row[4],
                "signal_date": str(row[5]),
            }
    except Exception as e:
        await _rollback_session_if_possible(session)
        logger.warning("advisor.load_signals_error: %s", str(e))

    return signals


async def load_fund_names(
    fund_codes: list[str],
    session: Any,
) -> dict[str, str]:
    """加载基金名称。"""
    from sqlalchemy import text

    if not fund_codes:
        return {}

    placeholders = ", ".join([f":code_{i}" for i in range(len(fund_codes))])
    query = text(
        f"SELECT code, name FROM funds WHERE code IN ({placeholders})"
    )
    params = {f"code_{i}": code for i, code in enumerate(fund_codes)}

    names: dict[str, str] = {}
    try:
        result = await session.execute(query, params)
        for row in result:
            names[row[0]] = row[1]
    except Exception:
        await _rollback_session_if_possible(session)

    return names


async def load_fund_types(
    fund_codes: list[str],
    session: Any,
) -> dict[str, tuple[str | None, str | None]]:
    """加载基金类型信息。

    Returns:
        {fund_code: (fund_type, sub_type)}
    """
    from sqlalchemy import text

    if not fund_codes:
        return {}

    placeholders = ", ".join([f":code_{i}" for i in range(len(fund_codes))])
    query = text(
        f"SELECT code, fund_type, sub_type FROM funds "
        f"WHERE code IN ({placeholders})"
    )
    params = {f"code_{i}": code for i, code in enumerate(fund_codes)}

    types: dict[str, tuple[str | None, str | None]] = {}
    try:
        result = await session.execute(query, params)
        for row in result:
            types[row[0]] = (row[1], row[2])
    except Exception:
        await _rollback_session_if_possible(session)

    return types


async def load_fund_fees(
    fund_codes: list[str],
    session: Any,
) -> dict[str, dict[str, Any]]:
    """加载基金费率数据。

    返回每只基金的完整申购/赎回费率梯度，并保留兜底费率字段。

    Returns:
        {
            fund_code: {
                subscribe_rate: float,
                redeem_rate: float,
                subscribe_tiers: [{min_amount, max_amount, rate}],
                redeem_tiers: [{min_days, max_days, rate}],
            }
        }
    """
    from sqlalchemy import text

    if not fund_codes:
        return {}

    placeholders = ", ".join([f":code_{i}" for i in range(len(fund_codes))])
    query = text(
        f"SELECT fund_code, fee_type, min_amount, max_amount, "
        f"min_holding_days, max_holding_days, rate "
        f"FROM fund_fees "
        f"WHERE fund_code IN ({placeholders}) "
        f"ORDER BY fund_code, fee_type, min_amount, min_holding_days"
    )
    params = {f"code_{i}": code for i, code in enumerate(fund_codes)}

    fees: dict[str, dict[str, Any]] = {}
    try:
        result = await session.execute(query, params)
        for row in result:
            fund_code = row[0]
            fee_type = row[1]
            min_amount = row[2]
            max_amount = row[3]
            min_holding_days = row[4]
            max_holding_days = row[5]
            rate = float(row[6])

            if fund_code not in fees:
                fees[fund_code] = {
                    "subscribe_rate": 0.0,
                    "redeem_rate": 0.0,
                    "subscribe_tiers": [],
                    "redeem_tiers": [],
                }

            if fee_type == "subscribe":
                fees[fund_code]["subscribe_tiers"].append({
                    "min_amount": float(min_amount) if min_amount is not None else 0.0,
                    "max_amount": float(max_amount) if max_amount is not None else None,
                    "rate": rate,
                })
                if fees[fund_code]["subscribe_rate"] == 0.0:
                    fees[fund_code]["subscribe_rate"] = rate
            elif fee_type == "redeem":
                fees[fund_code]["redeem_tiers"].append({
                    "min_days": int(min_holding_days) if min_holding_days is not None else 0,
                    "max_days": int(max_holding_days) if max_holding_days is not None else None,
                    "rate": rate,
                })
                if fees[fund_code]["redeem_rate"] == 0.0:
                    fees[fund_code]["redeem_rate"] = rate
    except Exception:
        await _rollback_session_if_possible(session)

    return fees


# ---------------------------------------------------------------------------
# 基金交易规则加载（v3 新增）
# ---------------------------------------------------------------------------


@dataclass
class FundTradingRules:
    """基金交易规则约束。

    从数据库加载的基金状态和交易限制，用于在生成建议前过滤不可交易的基金。
    """

    status: str = "active"  # active/suspended/delisted
    is_purchasable: bool = True  # 是否可申购
    is_redeemable: bool = True  # 是否可赎回
    purchase_limit: float | None = None  # 单笔申购限额（元），None=无限制
    daily_purchase_limit: float | None = None  # 单日申购限额（元），None=无限制
    min_purchase_amount: float | None = None  # 最低首次申购金额（元）
    min_additional_amount: float | None = None  # 最低追加申购金额（元）
    min_redeem_shares: float | None = None  # 最低赎回份额
    min_holding_shares: float | None = None  # 最低保留份额
    max_redeem_shares: float | None = None  # 单笔最大赎回份额
    fund_phase: str | None = None  # subscribe/open/closed 等
    delisting_date: str | None = None  # 退市日期
    # 赎回费率梯度（按持有天数）
    redeem_fee_tiers: list[dict[str, Any]] = field(default_factory=list)
    # 即将分红信息
    upcoming_dividend: dict[str, Any] | None = None  # {ex_date, record_date, dividend_per_share}


async def load_fund_trading_rules(
    fund_codes: list[str],
    session: Any,
    as_of_date: date | None = None,
) -> dict[str, FundTradingRules]:
    """加载基金交易规则约束。

    包括：
    - 基金状态（active/suspended/delisted）
    - 是否可申购
    - 单笔申购限额
    - 退市日期
    - 赎回费率梯度（按持有天数分档）

    Args:
        fund_codes: 基金代码列表
        session: AsyncSession

    Returns:
        {fund_code: FundTradingRules}
    """
    from sqlalchemy import text

    if not fund_codes:
        return {}

    rules: dict[str, FundTradingRules] = {}
    cutoff_date = as_of_date or date.today()

    # 1. 加载基金状态和限制（优先使用 as_of_date 当时的 PIT 快照）
    placeholders = ", ".join([f":code_{i}" for i in range(len(fund_codes))])
    query = text(
        f"SELECT f.code, "
        f"       COALESCE(pit.status, f.status) AS status, "
        f"       COALESCE(pit.is_purchasable, f.is_purchasable) AS is_purchasable, "
        f"       COALESCE(pit.purchase_limit, f.purchase_limit) AS purchase_limit, "
        f"       f.delisting_date "
        f"FROM funds f "
        f"LEFT JOIN ("
        f"  SELECT fund_code, status, is_purchasable, purchase_limit, "
        f"         ROW_NUMBER() OVER (PARTITION BY fund_code ORDER BY effective_date DESC) AS rn "
        f"  FROM fund_meta_history "
        f"  WHERE effective_date <= :cutoff_date"
        f") pit ON pit.fund_code = f.code AND pit.rn = 1 "
        f"WHERE f.code IN ({placeholders})"
    )
    params = {f"code_{i}": code for i, code in enumerate(fund_codes)}
    params["cutoff_date"] = cutoff_date

    try:
        result = await session.execute(query, params)
        for row in result:
            code = row[0]
            rules[code] = FundTradingRules(
                status=row[1] or "active",
                is_purchasable=bool(row[2]) if row[2] is not None else True,
                purchase_limit=float(row[3]) if row[3] is not None else None,
                delisting_date=str(row[4]) if row[4] is not None else None,
            )
    except Exception:
        await _rollback_session_if_possible(session)

    # 2. 加载可选的交易规则扩展表（如存在）
    try:
        rule_query = text(
            f"SELECT fund_code, is_redeemable, daily_purchase_limit, "
            f"min_purchase_amount, min_additional_amount, min_redeem_shares, "
            f"min_holding_shares, max_redeem_shares, fund_phase "
            f"FROM fund_trade_rules "
            f"WHERE fund_code IN ({placeholders})"
        )
        rule_result = await session.execute(rule_query, params)
        for row in rule_result:
            code = row[0]
            if code not in rules:
                rules[code] = FundTradingRules()
            rules[code].is_redeemable = bool(row[1]) if row[1] is not None else True
            rules[code].daily_purchase_limit = float(row[2]) if row[2] is not None else None
            rules[code].min_purchase_amount = float(row[3]) if row[3] is not None else None
            rules[code].min_additional_amount = float(row[4]) if row[4] is not None else None
            rules[code].min_redeem_shares = float(row[5]) if row[5] is not None else None
            rules[code].min_holding_shares = float(row[6]) if row[6] is not None else None
            rules[code].max_redeem_shares = float(row[7]) if row[7] is not None else None
            rules[code].fund_phase = row[8]
    except Exception:
        # 扩展表不存在或字段缺失时忽略，使用 funds 主表的基础规则。
        await _rollback_session_if_possible(session)

    # 3. 加载赎回费率梯度
    try:
        fee_query = text(
            f"SELECT fund_code, min_holding_days, max_holding_days, rate "
            f"FROM fund_fees "
            f"WHERE fund_code IN ({placeholders}) "
            f"AND fee_type = 'redeem' "
            f"ORDER BY fund_code, min_holding_days"
        )
        fee_result = await session.execute(fee_query, params)
        for row in fee_result:
            code = row[0]
            if code in rules:
                rules[code].redeem_fee_tiers.append({
                    "min_days": int(row[1]),
                    "max_days": int(row[2]) if row[2] is not None else None,
                    "rate": float(row[3]),
                })
    except Exception:
        await _rollback_session_if_possible(session)

    # 4. 加载 as_of_date 之后 30 天窗口内的即将分红信息
    try:
        future_30d = cutoff_date + timedelta(days=30)
        div_query = text(
            f"SELECT fund_code, ex_date, record_date, dividend_per_share "
            f"FROM fund_dividends "
            f"WHERE fund_code IN ({placeholders}) "
            f"AND ex_date BETWEEN :today AND :future "
            f"ORDER BY fund_code, ex_date "
            f"LIMIT 100"
        )
        div_params = {**params, "today": cutoff_date, "future": future_30d}
        div_result = await session.execute(div_query, div_params)
        for row in div_result:
            code = row[0]
            if code in rules and rules[code].upcoming_dividend is None:
                # 只取最近的一次分红
                rules[code].upcoming_dividend = {
                    "ex_date": str(row[1]),
                    "record_date": str(row[2]) if row[2] else None,
                    "dividend_per_share": float(row[3]),
                }
    except Exception:
        await _rollback_session_if_possible(session)

    return rules


def apply_fund_trading_rules(
    advice: "TradingAdvice",
    rules: FundTradingRules | None,
    holding_days: int | None = None,
) -> "TradingAdvice":
    """应用基金交易规则约束到建议结果。

    规则检查：
    1. 基金已退市/暂停 → 强制 hold，不建议任何操作
    2. 基金暂停申购 → 买入建议改为 hold
    3. 单笔限额 → 建议金额不超过限额
    4. 赎回费率梯度 → 使用精确的持有天数费率
    5. 即将分红 → 提示分红信息和影响

    Args:
        advice: 交易建议
        rules: 基金交易规则，None 表示无规则数据
        holding_days: 持有天数（用于精确费率计算）

    Returns:
        修改后的 advice（原地修改）
    """
    if rules is None:
        return advice

    # 1. 基金已退市
    if rules.status == "delisted":
        if advice.action == "buy":
            advice.action = "hold"
            advice.suggested_amount = 0.0
            advice.reasons.append("⚠️ 该基金已退市，无法申购")
            advice.risk_warnings.append("基金已退市，请尽快赎回剩余持仓")
        return advice

    # 2. 基金暂停交易
    if rules.status == "suspended":
        advice.action = "hold"
        advice.suggested_amount = 0.0
        advice.reasons.append("⚠️ 该基金当前处于暂停状态，无法交易")
        return advice

    # 3. 基金暂停申购（只影响买入）
    if not rules.is_purchasable and advice.action == "buy":
        advice.action = "hold"
        advice.suggested_amount = 0.0
        advice.reasons.append("⚠️ 该基金当前暂停申购，无法买入")
        advice.risk_warnings.append("基金暂停申购可能是因为规模过大或投资限制")
        return advice

    # 4. 认购/开放期约束
    if rules.fund_phase == "closed" and advice.action in ("buy", "sell"):
        advice.action = "hold"
        advice.suggested_amount = 0.0
        advice.suggested_shares = None
        advice.reasons.append("⚠️ 该基金处于封闭期，当前不可申购/赎回")
        advice.risk_warnings.append("封闭期基金需等待开放期后才能交易")
        return advice

    # 5. 买入可执行性校验
    if advice.action == "buy":
        _apply_purchase_execution_rules(advice, rules)

    # 6. 卖出可执行性校验
    if advice.action == "sell":
        _apply_redeem_execution_rules(advice, rules)

    # 7. 赎回费率精确计算（基于持有天数梯度）
    if rules.redeem_fee_tiers and advice.action == "sell":
        _apply_precise_redeem_fee(advice, rules.redeem_fee_tiers, holding_days)

    # 6. 即将分红提示
    if rules.upcoming_dividend:
        _apply_dividend_warning(advice, rules.upcoming_dividend)

    return advice


def _reset_to_hold(advice: "TradingAdvice", reason: str, warning: str | None = None) -> None:
    """将建议重置为持有，并清空交易相关数量。"""
    advice.action = "hold"
    advice.suggested_amount = 0.0
    advice.suggested_shares = None
    advice.estimated_gross_amount = None
    advice.estimated_net_amount = None
    advice.suggested_pct = 0.0
    if advice.fee_estimate:
        advice.fee_estimate.estimated_fee = 0.0
        advice.fee_estimate.net_trade_amount = None
        advice.fee_estimate.fee_impact_pct = 0.0
    advice.reasons.append(reason)
    if warning:
        advice.risk_warnings.append(warning)


def _apply_purchase_execution_rules(
    advice: "TradingAdvice",
    rules: FundTradingRules,
) -> None:
    """应用申购侧可执行性规则。"""
    if advice.action != "buy":
        return

    if rules.min_purchase_amount is not None and advice.suggested_amount < rules.min_purchase_amount:
        _reset_to_hold(
            advice,
            f"建议申购金额低于最低申购金额 ¥{rules.min_purchase_amount:,.0f}，暂不建议交易",
            "低于基金最低申购金额，订单可能被销售机构拒绝",
        )
        return

    if rules.min_additional_amount is not None and advice.suggested_amount < rules.min_additional_amount:
        _reset_to_hold(
            advice,
            f"建议申购金额低于最低追加金额 ¥{rules.min_additional_amount:,.0f}，暂不建议交易",
            "低于基金最低追加申购金额，订单可能被销售机构拒绝",
        )
        return

    amount_cap = rules.purchase_limit
    if amount_cap is not None and advice.suggested_amount > amount_cap:
        original_amount = advice.suggested_amount
        advice.suggested_amount = amount_cap
        advice.estimated_gross_amount = amount_cap
        advice.risk_warnings.append(
            f"单笔申购限额 ¥{amount_cap:,.0f}，原建议 ¥{original_amount:,.0f} 已调整为限额"
        )
        if advice.fee_estimate:
            advice.fee_estimate.estimated_fee = (
                advice.suggested_amount * advice.fee_estimate.subscribe_fee_rate
            )
            advice.fee_estimate.net_trade_amount = advice.suggested_amount - advice.fee_estimate.estimated_fee
            advice.estimated_net_amount = advice.fee_estimate.net_trade_amount
        else:
            advice.estimated_net_amount = amount_cap

    if rules.daily_purchase_limit is not None and advice.suggested_amount > rules.daily_purchase_limit:
        original_amount = advice.suggested_amount
        advice.suggested_amount = rules.daily_purchase_limit
        advice.estimated_gross_amount = advice.suggested_amount
        if advice.fee_estimate:
            advice.fee_estimate.estimated_fee = (
                advice.suggested_amount * advice.fee_estimate.subscribe_fee_rate
            )
            advice.fee_estimate.net_trade_amount = advice.suggested_amount - advice.fee_estimate.estimated_fee
            advice.estimated_net_amount = advice.fee_estimate.net_trade_amount
        else:
            advice.estimated_net_amount = advice.suggested_amount
        advice.risk_warnings.append(
            f"单日申购限额 ¥{rules.daily_purchase_limit:,.0f}，原建议 ¥{original_amount:,.0f} 已调整为单日限额"
        )


def _apply_redeem_execution_rules(
    advice: "TradingAdvice",
    rules: FundTradingRules,
) -> None:
    """应用赎回侧可执行性规则。"""
    if advice.action != "sell":
        return

    if not rules.is_redeemable:
        _reset_to_hold(
            advice,
            "⚠️ 该基金当前暂停赎回，无法卖出",
            "基金暂停赎回时提交赎回申请可能被拒绝",
        )
        return

    if rules.max_redeem_shares is not None and advice.suggested_shares is not None:
        if advice.suggested_shares > rules.max_redeem_shares:
            original_shares = advice.suggested_shares
            ratio = rules.max_redeem_shares / original_shares if original_shares > 0 else 0.0
            advice.suggested_shares = rules.max_redeem_shares
            advice.suggested_amount *= ratio
            advice.estimated_gross_amount = advice.suggested_amount
            if advice.fee_estimate:
                advice.fee_estimate.estimated_fee *= ratio
                advice.fee_estimate.net_trade_amount = advice.suggested_amount - advice.fee_estimate.estimated_fee
                advice.estimated_net_amount = advice.fee_estimate.net_trade_amount
            else:
                advice.estimated_net_amount = advice.suggested_amount
            advice.risk_warnings.append(
                f"单笔最大赎回份额 {rules.max_redeem_shares:,.2f} 份，原建议 {original_shares:,.2f} 份已调整"
            )

    if rules.min_redeem_shares is not None and advice.suggested_shares is not None:
        if advice.suggested_shares < rules.min_redeem_shares:
            _reset_to_hold(
                advice,
                f"建议赎回份额 {advice.suggested_shares:,.2f} 低于最低赎回份额 {rules.min_redeem_shares:,.2f}，暂不建议交易",
                "低于最低赎回份额，订单可能被销售机构拒绝",
            )
            return

    if (
        rules.min_holding_shares is not None
        and advice.suggested_shares is not None
        and advice.suggested_shares > 0
    ):
        current_shares = getattr(advice, "_current_shares", None)
        if current_shares is not None:
            remaining_shares = current_shares - advice.suggested_shares
            if 0 < remaining_shares < rules.min_holding_shares:
                advice.risk_warnings.append(
                    f"赎回后预计剩余 {remaining_shares:,.2f} 份，低于最低保留份额 {rules.min_holding_shares:,.2f}，可能需全部赎回或调整份额"
                )


def _apply_precise_redeem_fee(
    advice: "TradingAdvice",
    fee_tiers: list[dict[str, Any]],
    holding_days: int | None = None,
) -> None:
    """基于持有天数梯度精确计算赎回费率。

    中国公募基金赎回费率规则：
    - 持有 < 7 天：惩罚性费率 1.5%（强制，监管要求）
    - 持有 7~30 天：较高费率（通常 0.5%~0.75%）
    - 持有 30~365 天：标准费率（通常 0.5%）
    - 持有 365~730 天：优惠费率（通常 0.25%）
    - 持有 > 730 天：通常免赎回费

    Args:
        advice: 交易建议
        fee_tiers: 赎回费率梯度列表 [{min_days, max_days, rate}, ...]
        holding_days: 持有天数，None 表示未知
    """
    if not fee_tiers:
        return

    # 按 min_days 排序
    sorted_tiers = sorted(fee_tiers, key=lambda t: t["min_days"])

    if holding_days is not None and holding_days >= 0:
        # 精确匹配持有天数对应的费率
        matched_rate = None
        for tier in sorted_tiers:
            min_d = tier["min_days"]
            max_d = tier.get("max_days")
            if holding_days >= min_d and (max_d is None or holding_days < max_d):
                matched_rate = tier["rate"]
                break

        if matched_rate is not None:
            # 更新费用估算
            if advice.fee_estimate:
                advice.fee_estimate.redeem_fee_rate = matched_rate
                advice.fee_estimate.estimated_fee = (
                    advice.suggested_amount * matched_rate
                )
                advice.fee_estimate.fee_impact_pct = matched_rate

            # 费率提示
            if matched_rate >= 0.015:
                advice.risk_warnings.append(
                    f"⚠️ 持有仅 {holding_days} 天，赎回费率 {matched_rate*100:.2f}%"
                    f"（惩罚性费率），优先维持观察并复核是否延后减配"
                )
                # 惩罚性费率时强制改为 hold
                advice.action = "hold"
                advice.suggested_amount = 0.0
                advice.reasons.append(
                    f"持有 {holding_days} 天，赎回费率高达 {matched_rate*100:.1f}%，"
                    f"优先维持观察"
                )
            elif matched_rate >= 0.005:
                advice.risk_warnings.append(
                    f"持有 {holding_days} 天，赎回费率 {matched_rate*100:.2f}%"
                )
            elif matched_rate == 0:
                advice.reasons.append(
                    f"持有 {holding_days} 天，已免赎回费"
                )
    else:
        # 持有天数未知，展示费率梯度供参考
        # 找到最高费率（最短持有期）
        if sorted_tiers:
            highest = sorted_tiers[0]
            if highest["rate"] >= 0.015:
                max_days = highest.get("max_days", 7)
                advice.risk_warnings.append(
                    f"注意：持有<{max_days}天赎回费率高达"
                    f" {highest['rate']*100:.1f}%（惩罚性费率）"
                )

            # 展示完整费率梯度
            tier_desc = []
            for tier in sorted_tiers[:4]:  # 最多展示4档
                min_d = tier["min_days"]
                max_d = tier.get("max_days")
                rate = tier["rate"]
                if max_d:
                    tier_desc.append(f"{min_d}~{max_d}天:{rate*100:.2f}%")
                else:
                    tier_desc.append(f"≥{min_d}天:{rate*100:.2f}%")
            if tier_desc:
                advice.risk_warnings.append(
                    f"赎回费率梯度: {' / '.join(tier_desc)}"
                )


def _apply_dividend_warning(
    advice: "TradingAdvice",
    dividend_info: dict[str, Any],
) -> None:
    """应用即将分红的提示信息。

    分红对交易决策的影响：
    - 买入：在权益登记日前买入可享受分红，但除权后净值下跌
    - 卖出：在除权日前卖出可避免除权后净值下跌（但错过分红）
    - 分红本身不创造价值（除权），但有税务和再投资影响

    Args:
        advice: 交易建议
        dividend_info: {ex_date, record_date, dividend_per_share}
    """
    ex_date = dividend_info.get("ex_date", "")
    record_date = dividend_info.get("record_date")
    dps = dividend_info.get("dividend_per_share", 0)

    if not ex_date or dps <= 0:
        return

    # 计算距除权日的天数
    try:
        ex_d = date.fromisoformat(ex_date)
        reference_date = getattr(advice, "_as_of_date", date.today())
        days_to_ex = (ex_d - reference_date).days
    except (ValueError, TypeError):
        return

    if days_to_ex < 0:
        return  # 已过除权日

    dps_text = f"每份 ¥{dps:.4f}"

    if advice.action == "buy":
        if record_date and days_to_ex > 0:
            advice.reasons.append(
                f"📢 该基金即将分红（除权日 {ex_date}，{dps_text}）"
            )
            if days_to_ex <= 3:
                advice.risk_warnings.append(
                    f"距除权日仅 {days_to_ex} 天，"
                    f"买入后可能因 T+1 确认而错过本次分红权益登记"
                )
            else:
                advice.reasons.append(
                    f"在权益登记日前确认份额可享受本次分红"
                )
        # 提示分红不创造额外价值
        advice.limitations.append(
            "分红后基金净值会等额下调（除权），分红本身不创造额外收益"
        )

    elif advice.action == "sell":
        if days_to_ex <= 7:
            advice.risk_warnings.append(
                f"📢 该基金 {days_to_ex} 天后分红（{ex_date}，{dps_text}），"
                f"卖出将错过本次分红"
            )
            # 如果分红金额较大（>0.1元/份），提示可能值得等待
            if dps >= 0.1:
                advice.risk_warnings.append(
                    f"分红金额较大（{dps_text}），可考虑等分红到账后再赎回"
                )

    elif advice.action == "hold":
        if days_to_ex <= 14:
            advice.reasons.append(
                f"📢 该基金即将分红（{ex_date}，{dps_text}）"
            )


# ---------------------------------------------------------------------------
# 向后兼容：保留旧接口名称（供 advisor.py API 端点使用）
# ---------------------------------------------------------------------------

# 旧版 ValuationAnalysis 和 KellyPosition 的兼容别名
ValuationAnalysis = MomentumAnalysis
KellyPosition = RiskBudgetPosition


__all__ = [
    "TradingAdvisor",
    "AdvisorConfig",
    "TradingAdvice",
    "TechnicalIndicators",
    "MomentumAnalysis",
    "RiskBudgetPosition",
    "PredictionRef",
    "FeeEstimate",
    "AdviceReasoning",
    "AdviceValidity",
    "AdvisorDataQualityReport",
    "AdvisorOverfitRisk",
    "PortfolioImpact",
    "ProfileConstraint",
    "SuitabilityCheck",
    "TradePlan",
    "MarketRegime",
    "FundTradingRules",
    "compute_technical_indicators",
    "compute_momentum_score",
    "compute_risk_budget_position",
    "compute_prediction_score",
    "detect_market_regime",
    "compute_correlation_matrix",
    "filter_correlated_advices",
    "apply_signal_cooldown",
    "apply_fund_trading_rules",
    "estimate_trading_fee",
    "load_nav_data_for_advisor",
    "load_strategy_signals_for_advisor",
    "load_fund_names",
    "load_fund_types",
    "load_fund_fees",
    "load_fund_trading_rules",
    # 向后兼容
    "ValuationAnalysis",
    "KellyPosition",
]
