"""建议引擎自适应反馈学习模块（v5 新增）。

核心功能：
基于历史建议的实际效果（tracked_returns），自动学习并调整引擎参数，
让系统越用越准。

学习维度：
1. 因子权重优化：根据各维度评分与实际收益的 IC，自适应调整权重
2. 阈值优化：根据命中率和收益分布，动态调整买卖阈值
3. 动量折扣校准：根据动量信号的实际有效性，校准 A 股折扣系数
4. Regime 乘数校准：根据不同 regime 下的信号有效性，校准调整幅度

设计原则：
- 渐进式学习：每次调整幅度有限（学习率控制），避免过拟合
- 最小样本量：数据不足时回退到默认参数
- 安全边界：所有参数有硬性上下限，防止极端值
- 可解释性：每次调整都记录原因和幅度
- 可回滚：保存调整历史，支持回退到任意版本
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 学习结果数据结构
# ---------------------------------------------------------------------------


@dataclass
class LearnedWeights:
    """学习到的因子权重调整。"""

    # 各因子的 IC（信息系数）
    ic_technical: float | None = None
    ic_momentum: float | None = None
    ic_strategy: float | None = None
    ic_prediction: float | None = None
    ic_cross_sectional: float | None = None

    # 学习到的权重乘数（相对于基础权重的调整系数）
    # 1.0 = 不调整，>1 = 增加权重，<1 = 降低权重
    multiplier_technical: float = 1.0
    multiplier_momentum: float = 1.0
    multiplier_strategy: float = 1.0
    multiplier_prediction: float = 1.0
    multiplier_cross_sectional: float = 1.0

    # 学习到的阈值调整
    threshold_adjustment: float = 0.0  # 正值=放宽阈值，负值=收紧

    # 动量折扣校准
    momentum_discount_calibrated: float | None = None

    # 元数据
    version_id: int | None = None
    engine_version: str = "5.0"
    sample_count: int = 0
    learn_date: str = ""
    confidence: float = 0.0  # 学习结果的置信度 (0~1)
    adjustments_log: list[str] = field(default_factory=list)
    # 参数治理：OOS/PBO 发布门禁。未通过时只能 shadow，不进入默认建议链路。
    config_hash: str | None = None
    gate_status: str = "not_evaluated"
    gate_action: str = "shadow_only"
    gate_reason: str | None = None
    gate_checked_at: str | None = None
    gate_metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeedbackConfig:
    """反馈学习配置。"""

    # 最小样本量（低于此值不进行学习）
    min_samples: int = 30
    # 学习率（每次调整的最大幅度）
    learning_rate: float = 0.15
    # 权重乘数的安全边界
    min_weight_multiplier: float = 0.3
    max_weight_multiplier: float = 2.5
    # 阈值调整的安全边界
    max_threshold_adjustment: float = 0.10
    # IC 有效性阈值（低于此值的因子降权）
    ic_effective_threshold: float = 0.02
    # IC 强有效阈值（高于此值的因子增权）
    ic_strong_threshold: float = 0.08
    # 防过拟合：向默认参数收缩，避免把历史最优直接搬到线上
    multiplier_shrinkage: float = 0.35
    threshold_shrinkage: float = 0.35
    momentum_discount_shrinkage: float = 0.40
    # 防过拟合：限制单次相对默认值的最大偏移幅度
    max_relative_upside: float = 0.30
    max_relative_downside: float = 0.30
    # 回看天数
    lookback_days: int = 180
    # 存储路径
    learned_params_path: str = ""


# ---------------------------------------------------------------------------
# 核心学习引擎
# ---------------------------------------------------------------------------


class AdvisorFeedbackLearner:
    """建议引擎自适应反馈学习器。

    从历史建议的实际效果中学习，自动调整引擎参数。

    学习流程：
    1. 从 advisor_results 提取已跟踪的建议数据
    2. 计算各维度评分与实际收益的 IC
    3. 基于 IC 计算权重调整乘数
    4. 基于命中率分布计算阈值调整
    5. 保存学习结果供引擎使用

    使用方式：
    - 定时任务每周运行一次 learn()
    - TradingAdvisor 初始化时加载学习结果
    """

    def __init__(self, config: FeedbackConfig | None = None) -> None:
        self.config = config or FeedbackConfig()
        self._learned: LearnedWeights | None = None

    def _shrink_multiplier(self, learned_multiplier: float) -> float:
        """将学习到的权重乘数向默认值 1.0 收缩，并限制最大偏移。"""
        shrunk = 1.0 + (learned_multiplier - 1.0) * self.config.multiplier_shrinkage
        lower = 1.0 - self.config.max_relative_downside
        upper = 1.0 + self.config.max_relative_upside
        return float(np.clip(shrunk, lower, upper))

    def _shrink_threshold_adjustment(self, learned_adjustment: float) -> float:
        """将阈值调整向 0 收缩，并限制最大调整幅度。"""
        shrunk = learned_adjustment * self.config.threshold_shrinkage
        max_abs = min(self.config.max_threshold_adjustment, self.config.max_relative_upside)
        return float(np.clip(shrunk, -max_abs, max_abs))

    def _shrink_momentum_discount(self, learned_discount: float) -> float:
        """将学习到的动量折扣向默认 0.7 收缩。"""
        baseline = 0.7
        shrunk = baseline + (learned_discount - baseline) * self.config.momentum_discount_shrinkage
        lower = max(0.3, baseline - self.config.max_relative_downside)
        upper = min(1.0, baseline + self.config.max_relative_upside)
        return float(np.clip(shrunk, lower, upper))

    def learn_from_history_sync(self) -> LearnedWeights:
        """从历史跟踪数据中学习参数调整（同步版本）。

        Returns:
            LearnedWeights 包含学习到的参数调整
        """
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from app.core.config import get_settings
        from app.data.models.advisor_results import AdvisorResult

        settings = get_settings()
        engine = create_engine(settings.database_sync_url)

        learned = LearnedWeights(learn_date=date.today().isoformat())

        try:
            with Session(engine) as session:
                min_date = date.today() - timedelta(days=self.config.lookback_days)
                results = (
                    session.query(AdvisorResult)
                    .filter(AdvisorResult.advice_date >= min_date)
                    .filter(AdvisorResult.tracked_returns.isnot(None))
                    .order_by(AdvisorResult.advice_date.desc())
                    .limit(300)
                    .all()
                )

                if not results:
                    learned.adjustments_log.append("无跟踪数据，使用默认参数")
                    return learned

                # 提取各维度评分和实际收益
                data = self._extract_learning_data(results)
                learned.sample_count = len(data["returns_20d"])

                if learned.sample_count < self.config.min_samples:
                    learned.adjustments_log.append(
                        f"样本量不足({learned.sample_count}/{self.config.min_samples})，"
                        f"使用默认参数"
                    )
                    return learned

                # 1. 计算各因子 IC 并调整权重
                self._learn_factor_weights(learned, data)

                # 2. 学习阈值调整
                self._learn_threshold(learned, data)

                # 3. 校准动量折扣
                self._learn_momentum_discount(learned, data)

                # 计算学习置信度
                learned.confidence = min(1.0, learned.sample_count / 100)

        except Exception as e:
            logger.error("feedback_learner.error: %s", str(e))
            learned.adjustments_log.append(f"学习异常: {str(e)}")
        finally:
            engine.dispose()

        self._learned = learned
        self._save_learned(learned)
        return learned

    def _extract_learning_data(
        self, results: list[Any]
    ) -> dict[str, list[float]]:
        """从建议记录中提取学习所需的数据。"""
        data: dict[str, list[float]] = {
            "scores_technical": [],
            "scores_momentum": [],
            "scores_strategy": [],
            "scores_prediction": [],
            "scores_cross_sectional": [],
            "scores_composite": [],
            "returns_20d": [],
            "actions": [],  # 1=buy, -1=sell
            "hit_20d": [],  # 1=hit, 0=miss
        }

        for result in results:
            if not result.tracked_returns or not result.advices:
                continue

            for adv in result.advices:
                code = adv.get("fund_code")
                action = adv.get("action")
                scores = adv.get("scores", {})

                if action == "hold" or code not in result.tracked_returns:
                    continue

                tracking = result.tracked_returns[code]
                ret_20d = tracking.get("return_20d")
                hit_20d = tracking.get("hit_20d")

                if ret_20d is None:
                    continue

                data["scores_technical"].append(scores.get("technical", 0))
                data["scores_momentum"].append(scores.get("momentum", 0))
                data["scores_strategy"].append(scores.get("strategy", 0))
                data["scores_prediction"].append(scores.get("prediction", 0))
                data["scores_cross_sectional"].append(
                    scores.get("cross_sectional", 0)
                )
                data["scores_composite"].append(scores.get("composite", 0))
                data["returns_20d"].append(ret_20d)
                data["actions"].append(1.0 if action == "buy" else -1.0)
                data["hit_20d"].append(1.0 if hit_20d else 0.0)

        return data

    def _learn_factor_weights(
        self, learned: LearnedWeights, data: dict[str, list[float]]
    ) -> None:
        """基于各因子 IC 学习权重调整乘数。

        方法：
        - 计算每个因子评分与 20 日实际收益的 Spearman IC
        - IC > ic_strong_threshold → 增加权重（乘数 > 1）
        - IC < ic_effective_threshold → 降低权重（乘数 < 1）
        - IC 在中间 → 保持不变（乘数 = 1）
        - 调整幅度受 learning_rate 限制
        """
        from scipy.stats import spearmanr

        returns = np.array(data["returns_20d"])
        if np.std(returns) == 0:
            return

        factors = {
            "technical": data["scores_technical"],
            "momentum": data["scores_momentum"],
            "strategy": data["scores_strategy"],
            "prediction": data["scores_prediction"],
            "cross_sectional": data["scores_cross_sectional"],
        }

        for name, scores_list in factors.items():
            scores = np.array(scores_list)
            # 跳过全零因子（该信号源不可用）
            if np.std(scores) == 0:
                continue

            ic_val, p_value = spearmanr(scores, returns)
            if np.isnan(ic_val):
                continue

            ic_val = float(ic_val)
            setattr(learned, f"ic_{name}", round(ic_val, 4))

            # 计算权重乘数
            lr = self.config.learning_rate
            if ic_val >= self.config.ic_strong_threshold:
                # 强有效因子：增加权重
                boost = 1.0 + lr * (ic_val / self.config.ic_strong_threshold)
                multiplier = min(self.config.max_weight_multiplier, boost)
                learned.adjustments_log.append(
                    f"{name}: IC={ic_val:.4f}(强有效)，权重×{multiplier:.2f}"
                )
            elif ic_val <= self.config.ic_effective_threshold:
                # 无效/反向因子：降低权重
                if ic_val < 0:
                    # 反向因子：大幅降权
                    multiplier = max(
                        self.config.min_weight_multiplier, 1.0 + lr * ic_val * 5
                    )
                else:
                    # 微弱因子：温和降权
                    multiplier = max(
                        self.config.min_weight_multiplier, 1.0 - lr * 0.5
                    )
                learned.adjustments_log.append(
                    f"{name}: IC={ic_val:.4f}(弱/无效)，权重×{multiplier:.2f}"
                )
            else:
                # 中等有效：微调
                multiplier = 1.0 + lr * (ic_val - 0.05) * 2
                multiplier = np.clip(
                    multiplier,
                    self.config.min_weight_multiplier,
                    self.config.max_weight_multiplier,
                )

            raw_multiplier = float(multiplier)
            shrunk_multiplier = self._shrink_multiplier(raw_multiplier)
            setattr(learned, f"multiplier_{name}", round(shrunk_multiplier, 3))
            if abs(shrunk_multiplier - raw_multiplier) > 1e-6:
                learned.adjustments_log.append(
                    f"{name}: 为防止过拟合，乘数由 {raw_multiplier:.2f} 收缩为 {shrunk_multiplier:.2f}"
                )

    def _learn_threshold(
        self, learned: LearnedWeights, data: dict[str, list[float]]
    ) -> None:
        """基于命中率和收益分布学习阈值调整。

        方法：
        - 如果整体命中率 > 60%：阈值可以适当放宽（更多操作）
        - 如果整体命中率 < 45%：阈值应该收紧（减少错误操作）
        - 调整幅度受 max_threshold_adjustment 限制
        """
        hits = data["hit_20d"]
        if len(hits) < 20:
            return

        hit_rate = np.mean(hits)
        composites = np.array(data["scores_composite"])

        # 基于命中率调整
        if hit_rate >= 0.60:
            # 命中率高：可以放宽阈值，让更多信号通过
            adjustment = self.config.learning_rate * (hit_rate - 0.55) * 0.5
            learned.adjustments_log.append(
                f"命中率={hit_rate:.1%}(优秀)，放宽阈值 {adjustment:+.3f}"
            )
        elif hit_rate < 0.45:
            # 命中率低：收紧阈值，只让强信号通过
            adjustment = -self.config.learning_rate * (0.50 - hit_rate) * 0.5
            learned.adjustments_log.append(
                f"命中率={hit_rate:.1%}(偏低)，收紧阈值 {adjustment:+.3f}"
            )
        else:
            adjustment = 0.0
            learned.adjustments_log.append(
                f"命中率={hit_rate:.1%}(正常)，阈值不调整"
            )

        raw_adjustment = float(np.clip(
            adjustment,
            -self.config.max_threshold_adjustment,
            self.config.max_threshold_adjustment,
        ))
        learned.threshold_adjustment = self._shrink_threshold_adjustment(raw_adjustment)
        if abs(learned.threshold_adjustment - raw_adjustment) > 1e-6:
            learned.adjustments_log.append(
                f"阈值调整为防止过拟合已由 {raw_adjustment:+.3f} 收缩为 {learned.threshold_adjustment:+.3f}"
            )

    def _learn_momentum_discount(
        self, learned: LearnedWeights, data: dict[str, list[float]]
    ) -> None:
        """基于动量信号的实际有效性校准折扣系数。

        方法：
        - 计算动量评分与实际收益的方向一致率
        - 一致率高 → 折扣接近 1.0（动量有效）
        - 一致率低 → 折扣降低（动量失效）
        """
        mom_scores = np.array(data["scores_momentum"])
        returns = np.array(data["returns_20d"])

        # 只看动量信号非零的样本
        mask = np.abs(mom_scores) > 0.05
        if np.sum(mask) < 15:
            return

        mom_filtered = mom_scores[mask]
        ret_filtered = returns[mask]

        # 方向一致率
        direction_match = np.sign(mom_filtered) == np.sign(ret_filtered)
        hit_rate = float(np.mean(direction_match))

        # 映射到折扣系数
        if hit_rate >= 0.60:
            calibrated = min(1.0, 0.7 + (hit_rate - 0.50) * 2)
        elif hit_rate >= 0.45:
            calibrated = 0.7
        else:
            calibrated = max(0.4, 0.7 - (0.45 - hit_rate) * 2)

        raw_calibrated = float(calibrated)
        shrunk_calibrated = self._shrink_momentum_discount(raw_calibrated)
        learned.momentum_discount_calibrated = round(shrunk_calibrated, 3)
        learned.adjustments_log.append(
            f"动量方向一致率={hit_rate:.1%}，校准折扣={raw_calibrated:.3f}"
        )
        if abs(shrunk_calibrated - raw_calibrated) > 1e-6:
            learned.adjustments_log.append(
                f"动量折扣为防止过拟合已由 {raw_calibrated:.3f} 收缩为 {shrunk_calibrated:.3f}"
            )

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_learned_payload(learned: LearnedWeights) -> dict[str, Any]:
        payload = {
            "version_id": learned.version_id,
            "version": learned.engine_version,
            "learn_date": learned.learn_date,
            "sample_count": learned.sample_count,
            "confidence": learned.confidence,
            "factor_ics": {
                "technical": learned.ic_technical,
                "momentum": learned.ic_momentum,
                "strategy": learned.ic_strategy,
                "prediction": learned.ic_prediction,
                "cross_sectional": learned.ic_cross_sectional,
            },
            "weight_multipliers": {
                "technical": learned.multiplier_technical,
                "momentum": learned.multiplier_momentum,
                "strategy": learned.multiplier_strategy,
                "prediction": learned.multiplier_prediction,
                "cross_sectional": learned.multiplier_cross_sectional,
            },
            "threshold_adjustment": learned.threshold_adjustment,
            "momentum_discount_calibrated": learned.momentum_discount_calibrated,
            "adjustments_log": learned.adjustments_log,
            "config_hash": learned.config_hash,
            "gate_status": learned.gate_status,
            "gate_action": learned.gate_action,
            "gate_reason": learned.gate_reason,
            "gate_checked_at": learned.gate_checked_at,
            "gate_metrics": learned.gate_metrics,
        }
        if not payload["config_hash"]:
            from app.services.advisor_parameter_governance import compute_learned_params_config_hash

            payload["config_hash"] = compute_learned_params_config_hash(payload)
        return payload

    @classmethod
    def _deserialize_learned_payload(cls, data: dict[str, Any]) -> LearnedWeights:
        learned = LearnedWeights(
            version_id=data.get("version_id"),
            engine_version=str(data.get("version") or data.get("engine_version") or "5.0"),
            learn_date=data.get("learn_date", ""),
            sample_count=data.get("sample_count", 0),
            confidence=data.get("confidence", 0),
            threshold_adjustment=data.get("threshold_adjustment", 0),
            momentum_discount_calibrated=data.get(
                "momentum_discount_calibrated"
            ),
            adjustments_log=data.get("adjustments_log", []),
            config_hash=data.get("config_hash"),
            gate_status=str(data.get("gate_status") or "not_evaluated"),
            gate_action=str(data.get("gate_action") or "shadow_only"),
            gate_reason=data.get("gate_reason"),
            gate_checked_at=data.get("gate_checked_at"),
            gate_metrics=dict(data.get("gate_metrics") or {}),
        )
        ics = data.get("factor_ics", {})
        learned.ic_technical = ics.get("technical")
        learned.ic_momentum = ics.get("momentum")
        learned.ic_strategy = ics.get("strategy")
        learned.ic_prediction = ics.get("prediction")
        learned.ic_cross_sectional = ics.get("cross_sectional")
        mults = data.get("weight_multipliers", {})
        learned.multiplier_technical = mults.get("technical", 1.0)
        learned.multiplier_momentum = mults.get("momentum", 1.0)
        learned.multiplier_strategy = mults.get("strategy", 1.0)
        learned.multiplier_prediction = mults.get("prediction", 1.0)
        learned.multiplier_cross_sectional = mults.get("cross_sectional", 1.0)
        if not learned.config_hash:
            from app.services.advisor_parameter_governance import compute_learned_params_config_hash

            learned.config_hash = compute_learned_params_config_hash(data)
        return learned

    @staticmethod
    def _default_storage_path() -> Path:
        return Path(__file__).parent.parent / "data" / "learned_params.json"

    def _get_storage_path(self) -> Path:
        """获取学习结果存储路径。"""
        if self.config.learned_params_path:
            return Path(self.config.learned_params_path)
        return self._default_storage_path()

    @classmethod
    def _db_engine(cls):
        from sqlalchemy import create_engine

        from app.core.config import get_settings

        settings = get_settings()
        return create_engine(settings.database_sync_url)

    @classmethod
    def _db_available(cls) -> bool:
        from sqlalchemy import inspect

        try:
            engine = cls._db_engine()
            try:
                inspector = inspect(engine)
                return inspector.has_table("advisor_learned_params_versions")
            finally:
                engine.dispose()
        except Exception:
            return False

    @classmethod
    def _load_gate_fund_codes(cls, *, limit: int = 50) -> list[str]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app.data.models.strategies import Strategy

        if not cls._db_available():
            return []

        engine = cls._db_engine()
        try:
            with Session(engine) as session:
                rows = session.execute(select(Strategy.universe)).scalars().all()
                codes: set[str] = set()
                for universe in rows:
                    if isinstance(universe, dict):
                        candidates = universe.get("fund_codes") or []
                    elif isinstance(universe, list):
                        candidates = universe
                    else:
                        candidates = []
                    codes.update(str(code) for code in candidates if code)
                return sorted(codes)[:limit]
        except Exception as e:
            logger.warning("feedback_learner.gate_funds_load_error: %s", str(e))
            return []
        finally:
            engine.dispose()

    @classmethod
    def _evaluate_parameter_gate(cls, learned: LearnedWeights) -> None:
        from app.services.advisor_parameter_governance import evaluate_parameter_gate

        payload = cls._serialize_learned_payload(learned)
        fund_codes = cls._load_gate_fund_codes()
        gate = evaluate_parameter_gate(
            learned_payload=payload,
            risk_level="moderate",
            fund_codes=fund_codes,
        )
        learned.config_hash = gate.config_hash
        learned.gate_status = gate.status
        learned.gate_action = gate.action
        learned.gate_reason = gate.reason
        learned.gate_checked_at = gate.checked_at
        learned.gate_metrics = gate.metrics
        if gate.allow_default:
            learned.adjustments_log.append("参数发布门禁通过：允许作为默认学习参数")
        else:
            learned.adjustments_log.append(f"参数发布门禁未通过：{gate.reason}")

    @classmethod
    def _save_learned_to_db(cls, learned: LearnedWeights) -> int | None:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app.data.models.advisor_learned_params_versions import (
            AdvisorLearnedParamsVersion,
        )

        if not cls._db_available():
            return None

        engine = cls._db_engine()
        try:
            with Session(engine) as session:
                learn_date = date.fromisoformat(
                    learned.learn_date or date.today().isoformat()
                )
                row = session.execute(
                    select(AdvisorLearnedParamsVersion).where(
                        AdvisorLearnedParamsVersion.learn_date == learn_date
                    )
                ).scalar_one_or_none()
                if row is None:
                    row = AdvisorLearnedParamsVersion(learn_date=learn_date)
                    session.add(row)

                if not learned.gate_checked_at:
                    cls._evaluate_parameter_gate(learned)
                payload = cls._serialize_learned_payload(learned)
                row.learn_date = learn_date
                row.engine_version = str(payload.get("version") or "5.0")
                row.sample_count = int(payload.get("sample_count") or 0)
                row.confidence = float(payload.get("confidence") or 0.0)
                row.factor_ics = payload.get("factor_ics")
                row.weight_multipliers = payload.get("weight_multipliers")
                row.threshold_adjustment = float(payload.get("threshold_adjustment") or 0.0)
                row.momentum_discount_calibrated = payload.get(
                    "momentum_discount_calibrated"
                )
                row.adjustments_log = list(payload.get("adjustments_log") or [])
                row.config_hash = payload.get("config_hash")
                row.gate_status = str(payload.get("gate_status") or "not_evaluated")
                row.gate_action = str(payload.get("gate_action") or "shadow_only")
                row.gate_reason = payload.get("gate_reason")
                gate_checked_at = payload.get("gate_checked_at")
                if isinstance(gate_checked_at, str) and gate_checked_at:
                    try:
                        row.gate_checked_at = datetime.fromisoformat(gate_checked_at)
                    except ValueError:
                        row.gate_checked_at = None
                else:
                    row.gate_checked_at = None
                row.gate_metrics = payload.get("gate_metrics") or {}
                session.commit()
                session.refresh(row)
                learned.version_id = row.id
                return row.id
        except Exception as e:
            logger.warning("feedback_learner.db_save_error: %s", str(e))
            return None
        finally:
            engine.dispose()

    @classmethod
    def _load_learned_from_db(
        cls,
        *,
        as_of_date: date | None = None,
        allow_shadow: bool = False,
    ) -> LearnedWeights | None:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app.data.models.advisor_learned_params_versions import (
            AdvisorLearnedParamsVersion,
        )
        from app.services.advisor_parameter_governance import GATE_STATUS_APPROVED

        if not cls._db_available():
            return None

        engine = cls._db_engine()
        try:
            with Session(engine) as session:
                stmt = select(AdvisorLearnedParamsVersion)
                if as_of_date is not None:
                    stmt = stmt.where(
                        AdvisorLearnedParamsVersion.learn_date <= as_of_date
                    )
                if not allow_shadow:
                    stmt = stmt.where(
                        AdvisorLearnedParamsVersion.gate_status == GATE_STATUS_APPROVED,
                        AdvisorLearnedParamsVersion.gate_action == "allow_default",
                    )
                stmt = stmt.order_by(
                    AdvisorLearnedParamsVersion.learn_date.desc(),
                    AdvisorLearnedParamsVersion.id.desc(),
                ).limit(1)
                row = session.execute(stmt).scalar_one_or_none()
                if row is None:
                    return None
                return cls._deserialize_learned_payload(
                    {
                        "version_id": row.id,
                        "version": row.engine_version,
                        "learn_date": str(row.learn_date),
                        "sample_count": row.sample_count,
                        "confidence": row.confidence,
                        "factor_ics": row.factor_ics or {},
                        "weight_multipliers": row.weight_multipliers or {},
                        "threshold_adjustment": row.threshold_adjustment,
                        "momentum_discount_calibrated": row.momentum_discount_calibrated,
                        "adjustments_log": row.adjustments_log or [],
                        "config_hash": getattr(row, "config_hash", None),
                        "gate_status": getattr(row, "gate_status", None),
                        "gate_action": getattr(row, "gate_action", None),
                        "gate_reason": getattr(row, "gate_reason", None),
                        "gate_checked_at": (
                            row.gate_checked_at.isoformat()
                            if getattr(row, "gate_checked_at", None) else None
                        ),
                        "gate_metrics": getattr(row, "gate_metrics", None) or {},
                    }
                )
        except Exception as e:
            logger.warning("feedback_learner.db_load_error: %s", str(e))
            return None
        finally:
            engine.dispose()

    @classmethod
    def _resolve_history_file_path(
        cls,
        file_path: Path,
        *,
        as_of_date: date | None = None,
    ) -> Path | None:
        if as_of_date is None:
            return file_path if file_path.exists() else None

        candidates: list[tuple[date, Path]] = []
        pattern = f"{file_path.stem}.*{file_path.suffix}"
        for candidate in file_path.parent.glob(pattern):
            suffix = candidate.name[len(file_path.stem) + 1 : -len(file_path.suffix)]
            try:
                candidate_date = date.fromisoformat(suffix)
            except Exception:
                continue
            if candidate_date <= as_of_date:
                candidates.append((candidate_date, candidate))
        if candidates:
            return max(candidates, key=lambda item: item[0])[1]
        return file_path if file_path.exists() else None

    @classmethod
    def _load_learned_from_file(
        cls,
        file_path: Path,
        *,
        as_of_date: date | None = None,
        allow_shadow: bool = False,
    ) -> LearnedWeights | None:
        resolved_path = cls._resolve_history_file_path(file_path, as_of_date=as_of_date)
        if resolved_path is None:
            return None
        try:
            data = json.loads(resolved_path.read_text(encoding="utf-8"))
            learned = cls._deserialize_learned_payload(data)
            if not allow_shadow and not (
                learned.gate_status == "approved" and learned.gate_action == "allow_default"
            ):
                logger.info(
                    "feedback_learner.file_shadow_blocked: %s status=%s",
                    resolved_path,
                    learned.gate_status,
                )
                return None
            return learned
        except Exception as e:
            logger.warning("feedback_learner.load_error: %s", str(e))
            return None

    def _save_learned(self, learned: LearnedWeights) -> None:
        """保存学习结果到数据库与文件。"""
        if not learned.gate_checked_at:
            self._evaluate_parameter_gate(learned)
        learned.version_id = self._save_learned_to_db(learned)
        path = self._get_storage_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self._serialize_learned_payload(learned)

        try:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            history_path = path.with_suffix(f".{learned.learn_date}.json")
            history_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("feedback_learner.saved: %s", path)
        except Exception as e:
            logger.error("feedback_learner.save_error: %s", str(e))

    @classmethod
    def load_learned(
        cls,
        path: str | None = None,
        *,
        as_of_date: date | None = None,
        allow_shadow: bool = False,
    ) -> LearnedWeights | None:
        """加载已保存的学习结果。

        Args:
            path: 自定义路径；指定时仅从文件加载
            as_of_date: 指定历史时点时，优先加载不晚于该日期的版本
            allow_shadow: True 时允许读取未通过发布门禁的 shadow/blocked 参数，仅供审计或实验

        Returns:
            LearnedWeights 或 None（无可用版本时）
        """
        if path:
            return cls._load_learned_from_file(
                Path(path),
                as_of_date=as_of_date,
                allow_shadow=allow_shadow,
            )

        learned = cls._load_learned_from_db(
            as_of_date=as_of_date,
            allow_shadow=allow_shadow,
        )
        if learned is not None:
            return learned
        return cls._load_learned_from_file(
            cls._default_storage_path(),
            as_of_date=as_of_date,
            allow_shadow=allow_shadow,
        )


# ---------------------------------------------------------------------------
# 应用学习结果到引擎
# ---------------------------------------------------------------------------


def apply_learned_weights(
    base_weights: dict[str, float],
    learned: LearnedWeights | None,
) -> dict[str, float]:
    """将学习到的权重乘数应用到基础权重上。

    Args:
        base_weights: 基础权重 {technical: 0.10, momentum: 0.25, ...}
        learned: 学习结果，None 时返回原始权重

    Returns:
        调整后的权重（已归一化）
    """
    if learned is None or learned.confidence < 0.3:
        return base_weights

    adjusted = {}
    for key, base_w in base_weights.items():
        multiplier = getattr(learned, f"multiplier_{key}", 1.0)
        adjusted[key] = base_w * multiplier

    # 归一化
    total = sum(adjusted.values())
    if total > 0:
        adjusted = {k: v / total for k, v in adjusted.items()}

    return adjusted


def apply_learned_threshold(
    base_threshold: float,
    learned: LearnedWeights | None,
) -> float:
    """将学习到的阈值调整应用到基础阈值。

    Args:
        base_threshold: 基础阈值（正值）
        learned: 学习结果

    Returns:
        调整后的阈值
    """
    if learned is None or learned.confidence < 0.3:
        return base_threshold

    adjusted = base_threshold - learned.threshold_adjustment
    # 安全边界：阈值不能低于 0.05 或高于 0.5
    return float(np.clip(adjusted, 0.05, 0.50))


def apply_learned_momentum_discount(
    base_discount: float,
    learned: LearnedWeights | None,
) -> float:
    """将学习到的动量折扣应用到基础折扣。"""
    if learned is None or learned.confidence < 0.3:
        return base_discount

    if learned.momentum_discount_calibrated is not None:
        # 渐进式调整：基础折扣 60% + 学习折扣 40%
        blended = base_discount * 0.6 + learned.momentum_discount_calibrated * 0.4
        return float(np.clip(blended, 0.3, 1.0))

    return base_discount


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

__all__ = [
    "AdvisorFeedbackLearner",
    "FeedbackConfig",
    "LearnedWeights",
    "apply_learned_weights",
    "apply_learned_threshold",
    "apply_learned_momentum_discount",
]
