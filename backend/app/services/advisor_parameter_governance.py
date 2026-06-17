"""Advisor 参数发布门禁、参数注册表与回滚治理。

第一阶段以反馈学习参数（``AdvisorLearnedParamsVersion``）作为治理对象，
复用 Walk-Forward/OOS + CPCV/PBO 快照作为发布证据。当前模块进一步扩展为
完整参数注册表：默认配置参数集也会生成稳定 ``param_set_id``，并经过发布门禁、
人工审核、激活和回滚流程后才能进入默认建议链路。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import date, datetime, timezone
from typing import Any

from app.services.advisor_oos import OOSValidationSnapshot, OOSValidationStore

logger = logging.getLogger(__name__)

PARAM_SET_KIND_DEFAULT_CONFIG = "default_config"
PARAM_SET_KIND_FEEDBACK_LEARNING = "feedback_learning"
# 向后兼容：原常量用于 learned params 发布门禁。
DEFAULT_PARAM_SET_KIND = PARAM_SET_KIND_FEEDBACK_LEARNING

GATE_STATUS_APPROVED = "approved"
GATE_STATUS_SHADOW_ONLY = "shadow_only"
GATE_STATUS_BLOCKED = "blocked"
GATE_STATUS_NOT_EVALUATED = "not_evaluated"

GATE_ACTION_ALLOW_DEFAULT = "allow_default"
GATE_ACTION_SHADOW_ONLY = "shadow_only"
GATE_ACTION_BLOCK_DEFAULT = "block_default"

REVIEW_STATUS_PENDING = "pending"
REVIEW_STATUS_APPROVED = "approved"
REVIEW_STATUS_REJECTED = "rejected"

RELEASE_STATUS_SHADOW = "shadow"
RELEASE_STATUS_ACTIVE = "active"
RELEASE_STATUS_ARCHIVED = "archived"
RELEASE_STATUS_ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class AdvisorParameterGateThresholds:
    """不同风险档的参数发布门禁阈值。"""

    min_coverage_ratio: float
    min_oos_signals: int
    min_oos_ic: float
    min_ic_degradation: float
    max_pbo: float
    min_cpcv_paths: int = 10
    min_multi_objective_score: float = -0.05
    min_baseline_adjusted_score: float = -0.05


_GATE_THRESHOLDS: dict[str, AdvisorParameterGateThresholds] = {
    "conservative": AdvisorParameterGateThresholds(
        min_coverage_ratio=0.70,
        min_oos_signals=30,
        min_oos_ic=0.035,
        min_ic_degradation=0.55,
        max_pbo=0.40,
    ),
    "moderate": AdvisorParameterGateThresholds(
        min_coverage_ratio=0.60,
        min_oos_signals=20,
        min_oos_ic=0.025,
        min_ic_degradation=0.45,
        max_pbo=0.50,
    ),
    "aggressive": AdvisorParameterGateThresholds(
        min_coverage_ratio=0.50,
        min_oos_signals=15,
        min_oos_ic=0.015,
        min_ic_degradation=0.35,
        max_pbo=0.60,
    ),
}


@dataclass
class AdvisorParameterGateResult:
    """一次参数集发布门禁评估结果。"""

    status: str = GATE_STATUS_NOT_EVALUATED
    action: str = GATE_ACTION_SHADOW_ONLY
    reason: str = "尚未执行参数发布门禁"
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    config_hash: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def allow_default(self) -> bool:
        return self.status == GATE_STATUS_APPROVED and self.action == GATE_ACTION_ALLOW_DEFAULT

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "action": self.action,
            "reason": self.reason,
            "checked_at": self.checked_at,
            "config_hash": self.config_hash,
            "metrics": self.metrics,
            "allow_default": self.allow_default,
        }


@dataclass
class AdvisorParameterSetRecord:
    """参数注册表行的轻量序列化对象。"""

    id: int | None = None
    param_set_id: str = ""
    kind: str = PARAM_SET_KIND_DEFAULT_CONFIG
    risk_level: str = "moderate"
    engine_version: str = "5.0"
    name: str | None = None
    description: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    config_hash: str = ""
    source_learned_params_version_id: int | None = None
    train_window: dict[str, Any] | None = None
    validation_window: dict[str, Any] | None = None
    oos_window: dict[str, Any] | None = None
    created_reason: str | None = None
    gate_status: str = GATE_STATUS_NOT_EVALUATED
    gate_action: str = GATE_ACTION_SHADOW_ONLY
    gate_reason: str | None = None
    gate_checked_at: str | None = None
    gate_metrics: dict[str, Any] = field(default_factory=dict)
    review_status: str = REVIEW_STATUS_PENDING
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_notes: str | None = None
    release_status: str = RELEASE_STATUS_SHADOW
    activated_at: str | None = None
    archived_at: str | None = None
    rolled_back_at: str | None = None
    rollback_from_param_set_id: str | None = None
    rollback_reason: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "AdvisorParameterSetRecord":
        return cls(
            id=getattr(row, "id", None),
            param_set_id=str(getattr(row, "param_set_id", "") or ""),
            kind=str(getattr(row, "kind", PARAM_SET_KIND_DEFAULT_CONFIG) or PARAM_SET_KIND_DEFAULT_CONFIG),
            risk_level=str(getattr(row, "risk_level", "moderate") or "moderate"),
            engine_version=str(getattr(row, "engine_version", "5.0") or "5.0"),
            name=getattr(row, "name", None),
            description=getattr(row, "description", None),
            payload=dict(getattr(row, "payload", None) or {}),
            config_hash=str(getattr(row, "config_hash", "") or ""),
            source_learned_params_version_id=getattr(row, "source_learned_params_version_id", None),
            train_window=getattr(row, "train_window", None),
            validation_window=getattr(row, "validation_window", None),
            oos_window=getattr(row, "oos_window", None),
            created_reason=getattr(row, "created_reason", None),
            gate_status=str(getattr(row, "gate_status", GATE_STATUS_NOT_EVALUATED) or GATE_STATUS_NOT_EVALUATED),
            gate_action=str(getattr(row, "gate_action", GATE_ACTION_SHADOW_ONLY) or GATE_ACTION_SHADOW_ONLY),
            gate_reason=getattr(row, "gate_reason", None),
            gate_checked_at=_serialize_temporal(getattr(row, "gate_checked_at", None)),
            gate_metrics=dict(getattr(row, "gate_metrics", None) or {}),
            review_status=str(getattr(row, "review_status", REVIEW_STATUS_PENDING) or REVIEW_STATUS_PENDING),
            reviewed_by=getattr(row, "reviewed_by", None),
            reviewed_at=_serialize_temporal(getattr(row, "reviewed_at", None)),
            review_notes=getattr(row, "review_notes", None),
            release_status=str(getattr(row, "release_status", RELEASE_STATUS_SHADOW) or RELEASE_STATUS_SHADOW),
            activated_at=_serialize_temporal(getattr(row, "activated_at", None)),
            archived_at=_serialize_temporal(getattr(row, "archived_at", None)),
            rolled_back_at=_serialize_temporal(getattr(row, "rolled_back_at", None)),
            rollback_from_param_set_id=getattr(row, "rollback_from_param_set_id", None),
            rollback_reason=getattr(row, "rollback_reason", None),
            effective_from=_serialize_temporal(getattr(row, "effective_from", None)),
            effective_to=_serialize_temporal(getattr(row, "effective_to", None)),
            created_at=_serialize_temporal(getattr(row, "created_at", None)),
            updated_at=_serialize_temporal(getattr(row, "updated_at", None)),
        )

    def to_dict(self, *, include_payload: bool = True) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "param_set_id": self.param_set_id,
            "kind": self.kind,
            "risk_level": self.risk_level,
            "engine_version": self.engine_version,
            "name": self.name,
            "description": self.description,
            "config_hash": self.config_hash,
            "source_learned_params_version_id": self.source_learned_params_version_id,
            "train_window": self.train_window,
            "validation_window": self.validation_window,
            "oos_window": self.oos_window,
            "created_reason": self.created_reason,
            "gate_status": self.gate_status,
            "gate_action": self.gate_action,
            "gate_reason": self.gate_reason,
            "gate_checked_at": self.gate_checked_at,
            "gate_metrics": self.gate_metrics,
            "review_status": self.review_status,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "review_notes": self.review_notes,
            "release_status": self.release_status,
            "activated_at": self.activated_at,
            "archived_at": self.archived_at,
            "rolled_back_at": self.rolled_back_at,
            "rollback_from_param_set_id": self.rollback_from_param_set_id,
            "rollback_reason": self.rollback_reason,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_payload:
            payload["payload"] = self.payload
        return payload



def _serialize_temporal(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)



def _parse_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None



def _normalize_json_value(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {
            str(k): _normalize_json_value(v)
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value



def normalize_parameter_set_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """归一化参数集 payload，确保哈希稳定。"""

    return _normalize_json_value(dict(payload or {}))



def _stable_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        normalize_parameter_set_payload(payload),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )



def compute_parameter_config_hash(payload: dict[str, Any]) -> str:
    """为完整参数 payload 生成稳定 config hash。"""

    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:16]



def compute_parameter_set_id(
    *,
    kind: str = PARAM_SET_KIND_DEFAULT_CONFIG,
    risk_level: str = "moderate",
    payload: dict[str, Any] | None = None,
    engine_version: str = "5.0",
) -> str:
    """生成稳定参数集外部 ID。"""

    normalized_kind = str(kind or PARAM_SET_KIND_DEFAULT_CONFIG).strip().lower()
    normalized_risk = normalize_gate_risk_level(risk_level)
    seed = {
        "kind": normalized_kind,
        "risk_level": normalized_risk,
        "engine_version": str(engine_version or "5.0"),
        "payload": normalize_parameter_set_payload(payload or {}),
    }
    digest = hashlib.sha256(_stable_json(seed).encode("utf-8")).hexdigest()[:16]
    return f"{normalized_kind}_{normalized_risk}_{digest}"



def normalize_gate_risk_level(value: str | None) -> str:
    risk_level = str(value or "moderate").strip().lower()
    return risk_level if risk_level in _GATE_THRESHOLDS else "moderate"



def parameter_gate_thresholds(risk_level: str | None = None) -> AdvisorParameterGateThresholds:
    return _GATE_THRESHOLDS[normalize_gate_risk_level(risk_level)]



def gate_allows_default(status: str | None, action: str | None = None) -> bool:
    """判断某参数版本是否允许成为默认参数。"""

    if status == GATE_STATUS_APPROVED:
        return action in {None, "", GATE_ACTION_ALLOW_DEFAULT}
    return False



def compute_learned_params_config_hash(payload: dict[str, Any]) -> str:
    """为学习参数 payload 生成稳定哈希，作为轻量 param_set_id。"""

    relevant = {
        "version": payload.get("version") or payload.get("engine_version") or "5.0",
        "factor_ics": payload.get("factor_ics") or {},
        "weight_multipliers": payload.get("weight_multipliers") or {},
        "threshold_adjustment": payload.get("threshold_adjustment") or 0.0,
        "momentum_discount_calibrated": payload.get("momentum_discount_calibrated"),
        "sample_count": payload.get("sample_count") or 0,
        "confidence": payload.get("confidence") or 0.0,
    }
    text = json.dumps(relevant, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]



def build_default_parameter_payload(
    risk_level: str = "moderate",
    config: Any | None = None,
    *,
    engine_version: str = "5.0",
) -> dict[str, Any]:
    """把当前 Advisor 默认配置序列化成可回放参数 payload。"""

    from app.services.advisor_profiles import RISK_PROFILES, build_advisor_config
    from app.services.trading_advisor import FUND_TYPE_PROFILES

    normalized_risk = normalize_gate_risk_level(risk_level)
    config_obj = config or build_advisor_config(normalized_risk)
    config_payload = asdict(config_obj) if is_dataclass(config_obj) else dict(config_obj)
    signal_weights = {
        fund_type: {
            key: value
            for key, value in profile.items()
            if key.startswith("weight_") or key in {"technical_applicable", "label"}
        }
        for fund_type, profile in FUND_TYPE_PROFILES.items()
    }
    return {
        "schema_version": 1,
        "kind": PARAM_SET_KIND_DEFAULT_CONFIG,
        "engine_version": str(engine_version or "5.0"),
        "risk_level": normalized_risk,
        "advisor_config": normalize_parameter_set_payload(config_payload),
        "risk_profile": normalize_parameter_set_payload(RISK_PROFILES.get(normalized_risk, {})),
        "fund_type_signal_profiles": normalize_parameter_set_payload(signal_weights),
        "governance": {
            "requires_oos_pbo_gate": True,
            "requires_multi_objective_gate": True,
            "requires_baseline_gate": True,
            "requires_manual_review": True,
            "default_objective": "baseline_adjusted_score",
            "multi_objective_terms": [
                "oos_return",
                "risk_adjusted_return",
                "hit_rate",
                "drawdown_penalty",
                "turnover_fee_penalty",
                "overfit_penalty",
                "data_quality_penalty",
                "baseline_uplift",
                "complexity_penalty",
            ],
            "hash_algorithm": "sha256:16",
        },
    }



def advisor_config_from_parameter_payload(
    payload: dict[str, Any] | None,
    *,
    fallback_risk_level: str = "moderate",
) -> Any:
    """从参数集 payload 还原 AdvisorConfig；失败时回退内置风险档配置。"""

    from app.services.advisor_profiles import build_advisor_config
    from app.services.trading_advisor import AdvisorConfig

    data = dict((payload or {}).get("advisor_config") or {})
    if not data:
        return build_advisor_config(fallback_risk_level)

    allowed = {item.name for item in fields(AdvisorConfig)}
    kwargs = {key: value for key, value in data.items() if key in allowed}
    try:
        return AdvisorConfig(**kwargs)
    except Exception:
        logger.warning("advisor_parameter_set.config_restore_failed", exc_info=True)
        return build_advisor_config(fallback_risk_level)



def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)



def _snapshot_passes_gate(
    snapshot: OOSValidationSnapshot,
    thresholds: AdvisorParameterGateThresholds,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    total_oos_signals = int(getattr(snapshot, "total_oos_signals", 0) or 0)
    if total_oos_signals < thresholds.min_oos_signals:
        reasons.append(
            f"样本外信号数 {total_oos_signals} < {thresholds.min_oos_signals}"
        )

    avg_oos_ic = getattr(snapshot, "avg_oos_ic", None)
    if _is_number(avg_oos_ic) and avg_oos_ic < thresholds.min_oos_ic:
        reasons.append(f"OOS IC {avg_oos_ic:.4f} < {thresholds.min_oos_ic:.4f}")
    elif avg_oos_ic is None:
        reasons.append("缺少 OOS IC")

    ic_degradation = getattr(snapshot, "ic_degradation", None)
    if _is_number(ic_degradation) and ic_degradation < thresholds.min_ic_degradation:
        reasons.append(
            f"IC 衰减比 {ic_degradation:.2f} < {thresholds.min_ic_degradation:.2f}"
        )

    pbo = getattr(snapshot, "pbo", None)
    if _is_number(pbo) and pbo > thresholds.max_pbo:
        reasons.append(f"PBO {pbo:.0%} > {thresholds.max_pbo:.0%}")

    cpcv_n_paths = int(getattr(snapshot, "cpcv_n_paths", 0) or 0)
    if pbo is not None and cpcv_n_paths and cpcv_n_paths < thresholds.min_cpcv_paths:
        reasons.append(f"CPCV 路径数 {cpcv_n_paths} < {thresholds.min_cpcv_paths}")

    multi_score = getattr(snapshot, "multi_objective_score", None)
    if _is_number(multi_score) and multi_score < thresholds.min_multi_objective_score:
        reasons.append(
            f"多目标分数 {multi_score:.4f} < {thresholds.min_multi_objective_score:.4f}"
        )
    if bool(getattr(snapshot, "multi_objective_eliminated", False)):
        reasons.append("多目标门禁已淘汰该快照")

    baseline_passed = getattr(snapshot, "baseline_passed", None)
    if baseline_passed is False:
        reasons.append("未通过定投/风险平价/简单动量 baseline 对照门禁")
    baseline_score = getattr(snapshot, "baseline_adjusted_score", None)
    if _is_number(baseline_score) and baseline_score < thresholds.min_baseline_adjusted_score:
        reasons.append(
            f"baseline 调整分 {baseline_score:.4f} < {thresholds.min_baseline_adjusted_score:.4f}"
        )

    return not reasons, reasons



def evaluate_parameter_gate(
    *,
    learned_payload: dict[str, Any] | None = None,
    parameter_payload: dict[str, Any] | None = None,
    risk_level: str = "moderate",
    fund_codes: list[str] | None = None,
    oos_snapshots: dict[str, OOSValidationSnapshot] | None = None,
    as_of_date: date | None = None,
) -> AdvisorParameterGateResult:
    """评估参数是否允许成为默认参数。

    ``learned_payload`` 为向后兼容参数名；新参数注册表可传 ``parameter_payload``。
    当无法取得基金池或 OOS 快照时，结果为 ``shadow_only``，确保参数不会绕过
    Walk-Forward 发布门禁直接上线。
    """

    normalized_risk = normalize_gate_risk_level(risk_level)
    thresholds = parameter_gate_thresholds(normalized_risk)
    payload = parameter_payload if parameter_payload is not None else (learned_payload or {})
    config_hash = (
        compute_parameter_config_hash(payload)
        if parameter_payload is not None
        else compute_learned_params_config_hash(payload)
    )

    codes = sorted({str(code) for code in (fund_codes or []) if code})
    if oos_snapshots is None and codes:
        try:
            oos_snapshots = OOSValidationStore.load_many(
                codes,
                risk_level=normalized_risk,
                as_of_date=as_of_date,
            )
        except Exception:
            oos_snapshots = {}
    oos_snapshots = oos_snapshots or {}

    metrics: dict[str, Any] = {
        "risk_level": normalized_risk,
        "fund_count": len(codes),
        "snapshot_count": len(oos_snapshots),
        "thresholds": {
            "min_coverage_ratio": thresholds.min_coverage_ratio,
            "min_oos_signals": thresholds.min_oos_signals,
            "min_oos_ic": thresholds.min_oos_ic,
            "min_ic_degradation": thresholds.min_ic_degradation,
            "max_pbo": thresholds.max_pbo,
            "min_cpcv_paths": thresholds.min_cpcv_paths,
            "min_multi_objective_score": thresholds.min_multi_objective_score,
            "min_baseline_adjusted_score": thresholds.min_baseline_adjusted_score,
        },
        "failed_funds": {},
        "shadow_mode": True,
    }

    if not codes:
        return AdvisorParameterGateResult(
            status=GATE_STATUS_SHADOW_ONLY,
            action=GATE_ACTION_SHADOW_ONLY,
            reason="缺少参数发布基金池，参数仅允许 shadow 使用",
            config_hash=config_hash,
            metrics=metrics,
        )

    coverage_ratio = len(oos_snapshots) / len(codes) if codes else 0.0
    metrics["coverage_ratio"] = round(coverage_ratio, 4)
    if coverage_ratio < thresholds.min_coverage_ratio:
        return AdvisorParameterGateResult(
            status=GATE_STATUS_SHADOW_ONLY,
            action=GATE_ACTION_SHADOW_ONLY,
            reason=(
                f"OOS 覆盖率 {coverage_ratio:.0%} 低于发布门禁 "
                f"{thresholds.min_coverage_ratio:.0%}，参数仅允许 shadow"
            ),
            config_hash=config_hash,
            metrics=metrics,
        )

    failed: dict[str, list[str]] = {}
    evaluated = 0
    pbo_values: list[float] = []
    oos_ic_values: list[float] = []
    signal_counts: list[int] = []
    multi_scores: list[float] = []
    baseline_scores: list[float] = []
    baseline_failed_count = 0
    for code in codes:
        snapshot = oos_snapshots.get(code)
        if snapshot is None:
            continue
        evaluated += 1
        passes, reasons = _snapshot_passes_gate(snapshot, thresholds)
        if not passes:
            failed[code] = reasons
        pbo = getattr(snapshot, "pbo", None)
        if _is_number(pbo):
            pbo_values.append(float(pbo))
        avg_oos_ic = getattr(snapshot, "avg_oos_ic", None)
        if _is_number(avg_oos_ic):
            oos_ic_values.append(float(avg_oos_ic))
        signal_counts.append(int(getattr(snapshot, "total_oos_signals", 0) or 0))
        multi_score = getattr(snapshot, "multi_objective_score", None)
        if _is_number(multi_score):
            multi_scores.append(float(multi_score))
        baseline_score = getattr(snapshot, "baseline_adjusted_score", None)
        if _is_number(baseline_score):
            baseline_scores.append(float(baseline_score))
        if getattr(snapshot, "baseline_passed", None) is False:
            baseline_failed_count += 1

    metrics.update({
        "evaluated_snapshot_count": evaluated,
        "failed_count": len(failed),
        "failed_funds": failed,
        "avg_pbo": round(sum(pbo_values) / len(pbo_values), 4) if pbo_values else None,
        "avg_oos_ic": round(sum(oos_ic_values) / len(oos_ic_values), 4) if oos_ic_values else None,
        "min_oos_signals": min(signal_counts) if signal_counts else 0,
        "avg_multi_objective_score": round(sum(multi_scores) / len(multi_scores), 4) if multi_scores else None,
        "min_multi_objective_score": round(min(multi_scores), 4) if multi_scores else None,
        "avg_baseline_adjusted_score": round(sum(baseline_scores) / len(baseline_scores), 4) if baseline_scores else None,
        "min_baseline_adjusted_score": round(min(baseline_scores), 4) if baseline_scores else None,
        "baseline_failed_count": baseline_failed_count,
    })

    if failed:
        sample = "; ".join(
            f"{code}: {', '.join(reasons[:2])}"
            for code, reasons in list(failed.items())[:3]
        )
        return AdvisorParameterGateResult(
            status=GATE_STATUS_BLOCKED,
            action=GATE_ACTION_BLOCK_DEFAULT,
            reason=f"{len(failed)} 只基金未通过 OOS/PBO 发布门禁：{sample}",
            config_hash=config_hash,
            metrics=metrics,
        )

    metrics["shadow_mode"] = False
    return AdvisorParameterGateResult(
        status=GATE_STATUS_APPROVED,
        action=GATE_ACTION_ALLOW_DEFAULT,
        reason="参数通过 OOS/PBO 发布门禁，可作为默认参数使用",
        config_hash=config_hash,
        metrics=metrics,
    )


class AdvisorParameterRegistry:
    """默认 Advisor 参数集注册、审核、激活与回滚服务。"""

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
                return inspector.has_table("advisor_parameter_sets")
            finally:
                engine.dispose()
        except Exception:
            return False

    @classmethod
    def _ensure_available(cls) -> None:
        if not cls._db_available():
            raise RuntimeError("advisor_parameter_sets 表不可用，请先运行数据库迁移")

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @classmethod
    def register_default_parameter_set(
        cls,
        *,
        risk_level: str = "moderate",
        config: Any | None = None,
        engine_version: str = "5.0",
        name: str | None = None,
        description: str | None = None,
        created_reason: str | None = None,
        fund_codes: list[str] | None = None,
        oos_snapshots: dict[str, OOSValidationSnapshot] | None = None,
        gate_result: AdvisorParameterGateResult | None = None,
        evaluate_gate: bool = True,
        review_status: str | None = None,
        release_status: str | None = None,
        effective_from: date | None = None,
        train_window: dict[str, Any] | None = None,
        validation_window: dict[str, Any] | None = None,
        oos_window: dict[str, Any] | None = None,
        source_learned_params_version_id: int | None = None,
    ) -> AdvisorParameterSetRecord | None:
        """注册或更新当前风险档的默认参数集。"""

        if not cls._db_available():
            return None

        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app.data.models.advisor_parameter_sets import AdvisorParameterSet

        normalized_risk = normalize_gate_risk_level(risk_level)
        payload = build_default_parameter_payload(
            normalized_risk,
            config,
            engine_version=engine_version,
        )
        config_hash = compute_parameter_config_hash(payload)
        param_set_id = compute_parameter_set_id(
            kind=PARAM_SET_KIND_DEFAULT_CONFIG,
            risk_level=normalized_risk,
            payload=payload,
            engine_version=engine_version,
        )
        if gate_result is None:
            if evaluate_gate:
                gate_result = evaluate_parameter_gate(
                    parameter_payload=payload,
                    risk_level=normalized_risk,
                    fund_codes=fund_codes,
                    oos_snapshots=oos_snapshots,
                )
            else:
                gate_result = AdvisorParameterGateResult(config_hash=config_hash)
        gate_checked_at = _parse_datetime(gate_result.checked_at)

        engine = cls._db_engine()
        try:
            with Session(engine) as session:
                row = session.execute(
                    select(AdvisorParameterSet).where(
                        AdvisorParameterSet.param_set_id == param_set_id
                    )
                ).scalar_one_or_none()
                is_new = row is None
                if row is None:
                    row = AdvisorParameterSet(
                        param_set_id=param_set_id,
                        kind=PARAM_SET_KIND_DEFAULT_CONFIG,
                        risk_level=normalized_risk,
                        payload=payload,
                        config_hash=config_hash,
                    )
                    session.add(row)

                row.kind = PARAM_SET_KIND_DEFAULT_CONFIG
                row.risk_level = normalized_risk
                row.engine_version = str(engine_version or "5.0")
                row.name = name or row.name or f"{normalized_risk} 默认参数集"
                row.description = description
                row.payload = payload
                row.config_hash = gate_result.config_hash or config_hash
                row.source_learned_params_version_id = source_learned_params_version_id
                row.train_window = train_window
                row.validation_window = validation_window
                row.oos_window = oos_window
                row.created_reason = created_reason or row.created_reason
                row.gate_status = gate_result.status
                row.gate_action = gate_result.action
                row.gate_reason = gate_result.reason
                row.gate_checked_at = gate_checked_at
                row.gate_metrics = gate_result.metrics
                if review_status is not None or is_new:
                    row.review_status = review_status or REVIEW_STATUS_PENDING
                if release_status is not None or is_new:
                    row.release_status = release_status or RELEASE_STATUS_SHADOW
                row.effective_from = effective_from
                session.commit()
                session.refresh(row)
                return AdvisorParameterSetRecord.from_row(row)
        finally:
            engine.dispose()

    @classmethod
    def load_parameter_set(cls, param_set_id: str) -> AdvisorParameterSetRecord | None:
        if not cls._db_available():
            return None

        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app.data.models.advisor_parameter_sets import AdvisorParameterSet

        engine = cls._db_engine()
        try:
            with Session(engine) as session:
                row = session.execute(
                    select(AdvisorParameterSet).where(
                        AdvisorParameterSet.param_set_id == param_set_id
                    )
                ).scalar_one_or_none()
                return AdvisorParameterSetRecord.from_row(row) if row is not None else None
        finally:
            engine.dispose()

    @classmethod
    def load_active_parameter_set(
        cls,
        *,
        risk_level: str = "moderate",
        kind: str = PARAM_SET_KIND_DEFAULT_CONFIG,
        as_of_date: date | None = None,
    ) -> AdvisorParameterSetRecord | None:
        """加载某风险档当前或历史时点可见的 active 默认参数集。"""

        if not cls._db_available():
            return None

        from sqlalchemy import or_, select
        from sqlalchemy.orm import Session

        from app.data.models.advisor_parameter_sets import AdvisorParameterSet

        normalized_risk = normalize_gate_risk_level(risk_level)
        engine = cls._db_engine()
        try:
            with Session(engine) as session:
                stmt = select(AdvisorParameterSet).where(
                    AdvisorParameterSet.kind == kind,
                    AdvisorParameterSet.risk_level == normalized_risk,
                    AdvisorParameterSet.release_status == RELEASE_STATUS_ACTIVE,
                )
                if as_of_date is not None:
                    stmt = stmt.where(
                        or_(
                            AdvisorParameterSet.effective_from.is_(None),
                            AdvisorParameterSet.effective_from <= as_of_date,
                        ),
                        or_(
                            AdvisorParameterSet.effective_to.is_(None),
                            AdvisorParameterSet.effective_to >= as_of_date,
                        ),
                    )
                stmt = stmt.order_by(
                    AdvisorParameterSet.effective_from.desc(),
                    AdvisorParameterSet.activated_at.desc(),
                    AdvisorParameterSet.id.desc(),
                ).limit(1)
                row = session.execute(stmt).scalar_one_or_none()
                return AdvisorParameterSetRecord.from_row(row) if row is not None else None
        finally:
            engine.dispose()

    @classmethod
    def list_parameter_sets(
        cls,
        *,
        risk_level: str | None = None,
        kind: str | None = None,
        release_status: str | None = None,
        review_status: str | None = None,
        gate_status: str | None = None,
        limit: int = 100,
    ) -> list[AdvisorParameterSetRecord]:
        if not cls._db_available():
            return []

        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app.data.models.advisor_parameter_sets import AdvisorParameterSet

        engine = cls._db_engine()
        try:
            with Session(engine) as session:
                stmt = select(AdvisorParameterSet)
                if risk_level:
                    stmt = stmt.where(AdvisorParameterSet.risk_level == normalize_gate_risk_level(risk_level))
                if kind:
                    stmt = stmt.where(AdvisorParameterSet.kind == kind)
                if release_status:
                    stmt = stmt.where(AdvisorParameterSet.release_status == release_status)
                if review_status:
                    stmt = stmt.where(AdvisorParameterSet.review_status == review_status)
                if gate_status:
                    stmt = stmt.where(AdvisorParameterSet.gate_status == gate_status)
                stmt = stmt.order_by(
                    AdvisorParameterSet.updated_at.desc(),
                    AdvisorParameterSet.id.desc(),
                ).limit(max(1, min(int(limit or 100), 500)))
                rows = session.execute(stmt).scalars().all()
                return [AdvisorParameterSetRecord.from_row(row) for row in rows]
        finally:
            engine.dispose()

    @classmethod
    def review_parameter_set(
        cls,
        *,
        param_set_id: str,
        review_status: str,
        reviewed_by: str | None = None,
        review_notes: str | None = None,
    ) -> AdvisorParameterSetRecord:
        """人工审核参数集。"""

        cls._ensure_available()
        if review_status not in {
            REVIEW_STATUS_PENDING,
            REVIEW_STATUS_APPROVED,
            REVIEW_STATUS_REJECTED,
        }:
            raise ValueError("review_status 必须是 pending/approved/rejected")

        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app.data.models.advisor_parameter_sets import AdvisorParameterSet

        engine = cls._db_engine()
        try:
            with Session(engine) as session:
                row = session.execute(
                    select(AdvisorParameterSet).where(
                        AdvisorParameterSet.param_set_id == param_set_id
                    )
                ).scalar_one_or_none()
                if row is None:
                    raise LookupError("参数集不存在")
                row.review_status = review_status
                row.reviewed_by = reviewed_by
                row.review_notes = review_notes
                row.reviewed_at = cls._now()
                session.commit()
                session.refresh(row)
                return AdvisorParameterSetRecord.from_row(row)
        finally:
            engine.dispose()

    @classmethod
    def activate_parameter_set(
        cls,
        *,
        param_set_id: str,
        reason: str | None = None,
        effective_from: date | None = None,
    ) -> AdvisorParameterSetRecord:
        """将已通过门禁和人工审核的参数集切为 active。"""

        cls._ensure_available()
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app.data.models.advisor_parameter_sets import AdvisorParameterSet

        engine = cls._db_engine()
        try:
            with Session(engine) as session:
                row = session.execute(
                    select(AdvisorParameterSet).where(
                        AdvisorParameterSet.param_set_id == param_set_id
                    )
                ).scalar_one_or_none()
                if row is None:
                    raise LookupError("参数集不存在")
                if not gate_allows_default(row.gate_status, row.gate_action):
                    raise ValueError(f"参数集未通过发布门禁，不能激活：{row.gate_reason}")
                if row.review_status != REVIEW_STATUS_APPROVED:
                    raise ValueError("参数集尚未人工审核通过，不能激活")

                now = cls._now()
                today = effective_from or date.today()
                current_rows = session.execute(
                    select(AdvisorParameterSet).where(
                        AdvisorParameterSet.kind == row.kind,
                        AdvisorParameterSet.risk_level == row.risk_level,
                        AdvisorParameterSet.release_status == RELEASE_STATUS_ACTIVE,
                    )
                ).scalars().all()
                for current in current_rows:
                    if current.param_set_id == row.param_set_id:
                        continue
                    current.release_status = RELEASE_STATUS_ARCHIVED
                    current.archived_at = now
                    current.effective_to = today

                row.release_status = RELEASE_STATUS_ACTIVE
                row.activated_at = now
                row.effective_from = today
                row.effective_to = None
                row.rollback_from_param_set_id = None
                if reason:
                    row.review_notes = (row.review_notes or "") + f"\n激活原因：{reason}"
                session.commit()
                session.refresh(row)
                return AdvisorParameterSetRecord.from_row(row)
        finally:
            engine.dispose()

    @classmethod
    def rollback_parameter_set(
        cls,
        *,
        risk_level: str = "moderate",
        target_param_set_id: str | None = None,
        reason: str | None = None,
    ) -> AdvisorParameterSetRecord:
        """回滚到指定或最近一个已通过门禁/审核的历史参数集。"""

        cls._ensure_available()
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app.data.models.advisor_parameter_sets import AdvisorParameterSet

        normalized_risk = normalize_gate_risk_level(risk_level)
        engine = cls._db_engine()
        try:
            with Session(engine) as session:
                current = session.execute(
                    select(AdvisorParameterSet)
                    .where(
                        AdvisorParameterSet.kind == PARAM_SET_KIND_DEFAULT_CONFIG,
                        AdvisorParameterSet.risk_level == normalized_risk,
                        AdvisorParameterSet.release_status == RELEASE_STATUS_ACTIVE,
                    )
                    .order_by(AdvisorParameterSet.activated_at.desc(), AdvisorParameterSet.id.desc())
                    .limit(1)
                ).scalar_one_or_none()

                if target_param_set_id:
                    target = session.execute(
                        select(AdvisorParameterSet).where(
                            AdvisorParameterSet.param_set_id == target_param_set_id
                        )
                    ).scalar_one_or_none()
                else:
                    stmt = select(AdvisorParameterSet).where(
                        AdvisorParameterSet.kind == PARAM_SET_KIND_DEFAULT_CONFIG,
                        AdvisorParameterSet.risk_level == normalized_risk,
                        AdvisorParameterSet.review_status == REVIEW_STATUS_APPROVED,
                        AdvisorParameterSet.gate_status == GATE_STATUS_APPROVED,
                        AdvisorParameterSet.gate_action == GATE_ACTION_ALLOW_DEFAULT,
                        AdvisorParameterSet.release_status != RELEASE_STATUS_ACTIVE,
                    )
                    if current is not None:
                        stmt = stmt.where(
                            AdvisorParameterSet.param_set_id != current.param_set_id
                        )
                    target = session.execute(
                        stmt.order_by(
                            AdvisorParameterSet.activated_at.desc(),
                            AdvisorParameterSet.id.desc(),
                        ).limit(1)
                    ).scalar_one_or_none()

                if target is None:
                    raise LookupError("未找到可回滚的参数集")
                if target.risk_level != normalized_risk:
                    raise ValueError("目标参数集风险档与回滚请求不一致")
                if not gate_allows_default(target.gate_status, target.gate_action):
                    raise ValueError(f"目标参数集未通过发布门禁：{target.gate_reason}")
                if target.review_status != REVIEW_STATUS_APPROVED:
                    raise ValueError("目标参数集尚未人工审核通过")

                now = cls._now()
                today = date.today()
                if current is not None and current.param_set_id != target.param_set_id:
                    current.release_status = RELEASE_STATUS_ROLLED_BACK
                    current.rolled_back_at = now
                    current.effective_to = today
                    current.rollback_reason = reason

                previous_id = current.param_set_id if current is not None else None
                target.release_status = RELEASE_STATUS_ACTIVE
                target.activated_at = now
                target.effective_from = today
                target.effective_to = None
                target.rollback_from_param_set_id = previous_id
                target.rollback_reason = reason
                session.commit()
                session.refresh(target)
                return AdvisorParameterSetRecord.from_row(target)
        finally:
            engine.dispose()


__all__ = [
    "AdvisorParameterGateResult",
    "AdvisorParameterGateThresholds",
    "AdvisorParameterRegistry",
    "AdvisorParameterSetRecord",
    "DEFAULT_PARAM_SET_KIND",
    "GATE_ACTION_ALLOW_DEFAULT",
    "GATE_ACTION_BLOCK_DEFAULT",
    "GATE_ACTION_SHADOW_ONLY",
    "GATE_STATUS_APPROVED",
    "GATE_STATUS_BLOCKED",
    "GATE_STATUS_NOT_EVALUATED",
    "GATE_STATUS_SHADOW_ONLY",
    "PARAM_SET_KIND_DEFAULT_CONFIG",
    "PARAM_SET_KIND_FEEDBACK_LEARNING",
    "RELEASE_STATUS_ACTIVE",
    "RELEASE_STATUS_ARCHIVED",
    "RELEASE_STATUS_ROLLED_BACK",
    "RELEASE_STATUS_SHADOW",
    "REVIEW_STATUS_APPROVED",
    "REVIEW_STATUS_PENDING",
    "REVIEW_STATUS_REJECTED",
    "advisor_config_from_parameter_payload",
    "build_default_parameter_payload",
    "compute_learned_params_config_hash",
    "compute_parameter_config_hash",
    "compute_parameter_set_id",
    "evaluate_parameter_gate",
    "gate_allows_default",
    "normalize_gate_risk_level",
    "normalize_parameter_set_payload",
    "parameter_gate_thresholds",
]
