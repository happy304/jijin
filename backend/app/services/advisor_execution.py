from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.advisor_results import AdvisorResult
from app.data.models.strategies import Strategy
from app.data.providers.snapshot import SnapshotArchive, SnapshotVersion
from app.services.advisor_profiles import build_advisor_config, normalize_risk_level
from app.services.trading_advisor import TradingAdvice, TradingAdvisor


async def _rollback_if_possible(session: AsyncSession) -> None:
    """Rollback a failed advisor data-loading transaction when possible.

    PostgreSQL marks the whole transaction as aborted after a statement error.
    Advisor deliberately degrades when optional data sources are unavailable,
    so every swallowed DB error must clear the transaction before the next
    query; otherwise later healthy queries raise InFailedSQLTransactionError.
    """
    try:
        await session.rollback()
    except Exception:
        pass


@dataclass
class AdvisorExecutionRequest:
    """统一的建议执行请求。"""

    fund_codes: list[str]
    total_capital: float
    current_positions: dict[str, float] = field(default_factory=dict)
    positions_detail: dict[str, dict[str, Any]] = field(default_factory=dict)
    risk_level: str = "moderate"
    user_profile: dict[str, Any] = field(default_factory=dict)
    strategy_id: int | None = None
    strategy_name: str | None = None
    as_of_date: date | None = None
    mode: str = "live"  # live/portfolio/history_refresh/nightly/walk_forward
    enable_reliability_layers: bool = True
    enable_learned_weights: bool = True
    enable_llm: bool = False
    source_result_id: int | None = None


@dataclass
class AdvisorExecutionBundle:
    """建议执行所需的数据包。"""

    config: Any
    nav_data: dict[str, list[tuple[str, float]]]
    strategy_signals: dict[str, dict[str, Any]]
    fund_names: dict[str, str]
    fund_types: dict[str, tuple[str | None, str | None]]
    fee_data: dict[str, dict[str, Any]]
    fund_rules: dict[str, Any]
    last_advices: dict[str, dict[str, str]]
    cross_sectional_scores: dict[str, float]
    macro_score: float
    engine_health: Any | None
    oos_snapshots: dict[str, Any]
    learned_weights: Any | None
    user_learning_profile: Any | None = None
    nav_quality_diagnostics: dict[str, dict[str, Any]] = field(default_factory=dict)
    parameter_set: Any | None = None
    execution_context: dict[str, Any] = field(default_factory=dict)


async def load_last_advices(
    fund_codes: list[str],
    session: AsyncSession,
    *,
    limit: int = 20,
    as_of_date: date | None = None,
) -> dict[str, dict[str, str]]:
    """加载各基金最近一次建议，用于信号冷却。"""
    last_advices: dict[str, dict[str, str]] = {}

    try:
        stmt = select(AdvisorResult)
        if as_of_date is not None:
            stmt = stmt.where(AdvisorResult.advice_date < as_of_date)

        result = await session.execute(
            stmt.order_by(
                AdvisorResult.updated_at.desc().nullslast(),
                AdvisorResult.created_at.desc().nullslast(),
            ).limit(limit)
        )
        rows = result.scalars().all()
        for row in rows:
            if not row.advices:
                continue
            for adv in row.advices:
                code = adv.get("fund_code")
                if code and code in fund_codes and code not in last_advices:
                    action = adv.get("action", "hold")
                    adv_date = adv.get("advice_date") or str(row.advice_date)
                    last_advices[code] = {"action": action, "date": adv_date}
    except Exception:
        pass

    return last_advices


async def _resolve_portfolio_fund_codes(
    request: AdvisorExecutionRequest,
    session: AsyncSession,
) -> list[str]:
    """在组合模式下从策略中解析基金池。"""
    if request.fund_codes:
        return list(request.fund_codes)
    if request.strategy_id is None:
        return []

    result = await session.execute(
        select(Strategy).where(Strategy.id == request.strategy_id)
    )
    strategy = result.scalar_one_or_none()
    if strategy is None:
        raise LookupError("策略不存在")

    if not request.strategy_name:
        request.strategy_name = strategy.name

    universe = strategy.universe
    if isinstance(universe, dict):
        return [str(code) for code in universe.get("fund_codes", []) if code]
    if isinstance(universe, list):
        return [str(code) for code in universe if code]
    return []


async def _compute_cross_sectional_scores_grouped(
    fund_codes: list[str],
    fund_types: dict[str, tuple[str | None, str | None]],
    session: AsyncSession,
    *,
    as_of_date: date | None = None,
) -> dict[str, float]:
    """按基金类型分组计算截面因子评分。"""
    scores: dict[str, float] = {}
    try:
        from app.services.cross_sectional_scorer import (
            CrossSectionalConfig,
            cross_sectional_to_signal,
            load_fund_data_for_scoring,
            run_cross_sectional_scoring,
        )

        type_groups: dict[str | None, list[str]] = {}
        for code in fund_codes:
            ft = fund_types.get(code, (None, None))[0]
            type_groups.setdefault(ft, []).append(code)

        cs_config = CrossSectionalConfig()
        for fund_type, codes_in_type in type_groups.items():
            cs_fund_data = await load_fund_data_for_scoring(
                session,
                fund_type=fund_type,
                min_history_days=252,
                as_of_date=as_of_date,
            )
            if len(cs_fund_data) < cs_config.min_funds_for_ranking:
                continue
            cs_result = run_cross_sectional_scoring(cs_fund_data, cs_config)
            for code in codes_in_type:
                scores[code] = cross_sectional_to_signal(code, cs_result, cs_config)
    except Exception:
        return {}

    return scores


def _serialize_date_like(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _normalize_snapshot_as_of(as_of_date: date | None) -> datetime | None:
    if as_of_date is None:
        return None
    return datetime.combine(as_of_date, datetime.max.time(), tzinfo=timezone.utc)


def _snapshot_version_payload(version: SnapshotVersion) -> dict[str, Any]:
    return {
        "version_id": version.version_id,
        "provider": version.provider,
        "fund_code": version.fund_code,
        "endpoint": version.endpoint,
        "ext": version.ext,
        "snapshot_date": version.snapshot_date.isoformat(),
        "captured_at": _serialize_date_like(version.captured_at),
        "sha256": version.sha256,
        "size_bytes": version.size_bytes,
        "path": str(version.path),
    }


async def _load_fund_source_map(
    fund_codes: list[str],
    session: AsyncSession,
) -> dict[str, dict[str, Any]]:
    if not fund_codes:
        return {}
    placeholders = ", ".join([f":code_{i}" for i in range(len(fund_codes))])
    query = text(
        f"SELECT code, source, updated_at FROM funds WHERE code IN ({placeholders})"
    )
    params = {f"code_{i}": code for i, code in enumerate(fund_codes)}
    result = await session.execute(query, params)
    payload: dict[str, dict[str, Any]] = {}
    for row in result:
        payload[str(row[0])] = {
            "source": str(row[1]).strip() if row[1] is not None and str(row[1]).strip() else None,
            "updated_at": _serialize_date_like(row[2]),
        }
    return payload


def _build_snapshot_lookup_index(
    *,
    archive: SnapshotArchive,
    endpoint: str,
    fund_codes: list[str],
    providers: set[str],
    as_of_date: date | None,
) -> dict[tuple[str, str], SnapshotVersion]:
    if not fund_codes or not providers:
        return {}
    versions = archive.list_versions(
        as_of=_normalize_snapshot_as_of(as_of_date),
    )
    fund_code_set = set(fund_codes)
    provider_set = {provider for provider in providers if provider}
    indexed: dict[tuple[str, str], SnapshotVersion] = {}
    for version in versions:
        if version.endpoint != endpoint:
            continue
        if version.fund_code not in fund_code_set:
            continue
        if version.provider not in provider_set:
            continue
        key = (version.provider, version.fund_code)
        current = indexed.get(key)
        if current is None or (version.captured_at, version.version_id) > (current.captured_at, current.version_id):
            indexed[key] = version
    return indexed


def _nav_audit_rows(
    fund_codes: list[str],
    nav_data: dict[str, list[tuple[str, float]]],
    nav_quality_diagnostics: dict[str, dict[str, Any]] | None = None,
    *,
    nav_snapshot_versions: dict[tuple[str, str], SnapshotVersion] | None = None,
    as_of_date: date | None = None,
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    diagnostics = nav_quality_diagnostics or {}
    snapshot_versions = nav_snapshot_versions or {}
    for code in fund_codes:
        records = nav_data.get(code) or []
        dates = [str(item[0]) for item in records if item]
        diagnostic = diagnostics.get(code) or {}
        source_consistency = diagnostic.get("source_consistency") or {}
        provider_versions: dict[str, dict[str, Any]] = {}
        for provider_name in (source_consistency.get("sources") or {}).keys():
            provider = str(provider_name).strip()
            if not provider or provider == "unknown":
                continue
            version = snapshot_versions.get((provider, code))
            if version is not None:
                provider_versions[provider] = _snapshot_version_payload(version)
        primary_provider = source_consistency.get("primary_source")
        primary_snapshot = provider_versions.get(str(primary_provider)) if primary_provider else None
        rows[code] = {
            "min_date": dates[0] if dates else None,
            "max_date": dates[-1] if dates else None,
            "point_count": len(records),
            "has_data": bool(records),
            "source_consistency": source_consistency,
            "adjustment_consistency": diagnostic.get("adjustment_consistency") or {},
            "cross_source_consistency": diagnostic.get("cross_source_consistency") or {},
            "snapshot_lookup_as_of": as_of_date.isoformat() if as_of_date else None,
            "snapshot_provider": primary_snapshot.get("provider") if primary_snapshot else None,
            "snapshot_version_id": primary_snapshot.get("version_id") if primary_snapshot else None,
            "snapshot_captured_at": primary_snapshot.get("captured_at") if primary_snapshot else None,
            "snapshot_sha256": primary_snapshot.get("sha256") if primary_snapshot else None,
            "snapshot_versions": provider_versions,
        }
    return rows


def _strategy_signal_audit_rows(
    fund_codes: list[str],
    strategy_signals: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for code in fund_codes:
        signal = strategy_signals.get(code) or {}
        rows[code] = {
            "signal_date": signal.get("signal_date"),
            "direction": signal.get("direction"),
            "strength": signal.get("strength"),
            "target_weight": signal.get("target_weight"),
            "has_signal": bool(signal),
        }
    return rows


def _rules_audit_rows(
    fund_codes: list[str],
    fund_rules: dict[str, Any],
    *,
    fund_source_map: dict[str, dict[str, Any]] | None = None,
    meta_snapshot_versions: dict[tuple[str, str], SnapshotVersion] | None = None,
    as_of_date: date | None = None,
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    source_map = fund_source_map or {}
    snapshot_versions = meta_snapshot_versions or {}
    for code in fund_codes:
        rules = fund_rules.get(code)
        source_info = source_map.get(code) or {}
        provider = source_info.get("source")
        version = snapshot_versions.get((str(provider), code)) if provider else None
        snapshot_payload = _snapshot_version_payload(version) if version is not None else None
        if rules is None:
            rows[code] = {
                "has_rules": False,
                "source": provider,
                "source_updated_at": source_info.get("updated_at"),
                "snapshot_lookup_as_of": as_of_date.isoformat() if as_of_date else None,
                "snapshot_provider": snapshot_payload.get("provider") if snapshot_payload else None,
                "snapshot_version_id": snapshot_payload.get("version_id") if snapshot_payload else None,
                "snapshot_captured_at": snapshot_payload.get("captured_at") if snapshot_payload else None,
                "snapshot_sha256": snapshot_payload.get("sha256") if snapshot_payload else None,
            }
            continue
        payload = asdict(rules) if is_dataclass(rules) else dict(rules)
        rows[code] = {
            "has_rules": True,
            "status": payload.get("status"),
            "is_purchasable": payload.get("is_purchasable"),
            "is_redeemable": payload.get("is_redeemable"),
            "purchase_limit": payload.get("purchase_limit"),
            "daily_purchase_limit": payload.get("daily_purchase_limit"),
            "min_purchase_amount": payload.get("min_purchase_amount"),
            "min_redeem_shares": payload.get("min_redeem_shares"),
            "fund_phase": payload.get("fund_phase"),
            "delisting_date": payload.get("delisting_date"),
            "upcoming_dividend": payload.get("upcoming_dividend"),
            "source": provider,
            "source_updated_at": source_info.get("updated_at"),
            "snapshot_lookup_as_of": as_of_date.isoformat() if as_of_date else None,
            "snapshot_provider": snapshot_payload.get("provider") if snapshot_payload else None,
            "snapshot_version_id": snapshot_payload.get("version_id") if snapshot_payload else None,
            "snapshot_captured_at": snapshot_payload.get("captured_at") if snapshot_payload else None,
            "snapshot_sha256": snapshot_payload.get("sha256") if snapshot_payload else None,
        }
    return rows


def _oos_audit_rows(
    fund_codes: list[str],
    oos_snapshots: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for code in fund_codes:
        snapshot = oos_snapshots.get(code)
        if snapshot is None:
            rows[code] = {"has_snapshot": False}
            continue
        rows[code] = {
            "has_snapshot": True,
            "risk_level": getattr(snapshot, "risk_level", None),
            "requested_risk_level": getattr(snapshot, "requested_risk_level", None),
            "selection_source": getattr(snapshot, "selection_source", None),
            "updated_at": getattr(snapshot, "updated_at", None),
            "snapshot_date": getattr(snapshot, "snapshot_date", None),
            "config_hash": getattr(snapshot, "config_hash", None),
            "data_version": getattr(snapshot, "data_version", None),
            "validation_window": getattr(snapshot, "validation_window", None),
            "avg_oos_ic": getattr(snapshot, "avg_oos_ic", None),
            "ic_degradation": getattr(snapshot, "ic_degradation", None),
            "total_oos_signals": getattr(snapshot, "total_oos_signals", None),
            "pbo": getattr(snapshot, "pbo", None),
            "cpcv_n_paths": getattr(snapshot, "cpcv_n_paths", None),
            "multi_objective_score": getattr(snapshot, "multi_objective_score", None),
            "multi_objective_eliminated": getattr(snapshot, "multi_objective_eliminated", None),
            "multi_objective_reasons": getattr(snapshot, "multi_objective_reasons", None),
        }
    return rows


async def build_execution_bundle(
    request: AdvisorExecutionRequest,
    session: AsyncSession,
) -> AdvisorExecutionBundle:
    """构建统一建议执行所需的上下文数据。"""
    from app.services.advisor_feedback import AdvisorFeedbackLearner
    from app.services.advisor_oos import OOSValidationStore
    from app.services.advisor_tracking import compute_engine_health_async
    from app.services.macro_factor import compute_macro_score, load_macro_data
    from app.services.trading_advisor import (
        load_fund_fees,
        load_fund_names,
        load_fund_trading_rules,
        load_fund_types,
        load_nav_data_for_advisor,
        load_nav_quality_diagnostics_for_advisor,
        load_strategy_signals_for_advisor,
    )

    request.risk_level = normalize_risk_level(request.risk_level)
    request.user_profile = dict(request.user_profile or {})
    request.user_profile.setdefault("risk_level", request.risk_level)

    user_learning_profile = None
    try:
        from app.services.advisor_user_learning import AdvisorUserLearningService

        profile_key = request.user_profile.get("user_id") or request.user_profile.get("profile_key")
        user_learning_profile = await AdvisorUserLearningService.load_or_learn(
            session,
            profile_key=profile_key,
        )
        request.user_profile = AdvisorUserLearningService.apply_to_user_profile(
            request.user_profile,
            user_learning_profile,
        )
    except Exception as exc:
        await _rollback_if_possible(session)
        request.user_profile.setdefault(
            "advisor_personalization",
            {"status": "unavailable", "error": str(exc)},
        )

    fund_codes = await _resolve_portfolio_fund_codes(request, session)
    request.fund_codes = fund_codes

    config = build_advisor_config(request.risk_level)
    personalization = request.user_profile.get("advisor_personalization") or {}
    if isinstance(personalization, dict) and float(personalization.get("confidence") or 0.0) >= 0.2:
        style = str(personalization.get("preferred_execution_style") or "neutral")
        if style in {"slower_cadence", "small_steps"}:
            config.signal_cooldown_days = min(config.signal_cooldown_days + 2, 10)
        elif style == "batch":
            config.cooldown_decay_factor = min(0.9, config.cooldown_decay_factor + 0.05)
    parameter_set = None
    parameter_set_context: dict[str, Any] = {
        "param_set_id": None,
        "kind": "default_config",
        "risk_level": request.risk_level,
        "engine_version": "5.0",
        "config_hash": None,
        "release_status": "built_in",
        "gate_status": "not_loaded",
        "gate_action": "built_in_default",
        "gate_reason": "未找到已激活的参数集；使用内置默认配置",
        "review_status": None,
        "resolution_source": "built_in_default",
    }
    try:
        from app.services.advisor_parameter_governance import (
            AdvisorParameterRegistry,
            advisor_config_from_parameter_payload,
        )

        parameter_set = AdvisorParameterRegistry.load_active_parameter_set(
            risk_level=request.risk_level,
            as_of_date=request.as_of_date,
        )
        if parameter_set is not None:
            config = advisor_config_from_parameter_payload(
                parameter_set.payload,
                fallback_risk_level=request.risk_level,
            )
            parameter_set_context = {
                "param_set_id": parameter_set.param_set_id,
                "kind": parameter_set.kind,
                "risk_level": parameter_set.risk_level,
                "engine_version": parameter_set.engine_version,
                "config_hash": parameter_set.config_hash,
                "release_status": parameter_set.release_status,
                "gate_status": parameter_set.gate_status,
                "gate_action": parameter_set.gate_action,
                "gate_reason": parameter_set.gate_reason,
                "gate_checked_at": parameter_set.gate_checked_at,
                "gate_metrics": parameter_set.gate_metrics,
                "review_status": parameter_set.review_status,
                "reviewed_at": parameter_set.reviewed_at,
                "activated_at": parameter_set.activated_at,
                "effective_from": parameter_set.effective_from,
                "effective_to": parameter_set.effective_to,
                "resolution_source": "active_registry",
            }
    except Exception as exc:
        await _rollback_if_possible(session)
        parameter_set_context["load_error"] = str(exc)

    nav_data = await load_nav_data_for_advisor(
        request.fund_codes,
        session,
        lookback_days=config.lookback_days,
        as_of_date=request.as_of_date,
    )
    nav_quality_diagnostics = await load_nav_quality_diagnostics_for_advisor(
        request.fund_codes,
        session,
        lookback_days=config.lookback_days,
        as_of_date=request.as_of_date,
    )

    strategy_signals = await load_strategy_signals_for_advisor(
        request.fund_codes,
        session,
        strategy_id=request.strategy_id,
        as_of_date=request.as_of_date,
    )
    fund_names = await load_fund_names(request.fund_codes, session)
    fund_types = await load_fund_types(request.fund_codes, session)
    fund_source_map = await _load_fund_source_map(request.fund_codes, session)
    fee_data = await load_fund_fees(request.fund_codes, session)
    fund_rules = await load_fund_trading_rules(
        request.fund_codes,
        session,
        as_of_date=request.as_of_date,
    )
    last_advices = await load_last_advices(
        request.fund_codes,
        session,
        as_of_date=request.as_of_date,
    )
    cross_sectional_scores = await _compute_cross_sectional_scores_grouped(
        request.fund_codes,
        fund_types,
        session,
        as_of_date=request.as_of_date,
    )

    macro_score = 0.0
    macro_audit: dict[str, Any] = {
        "cutoff_date": request.as_of_date.isoformat() if request.as_of_date else None,
        "benchmark_series": {},
        "valuation_indices": [],
        "data_available": False,
    }
    try:
        benchmark_returns, valuation_data = await load_macro_data(
            session,
            as_of_date=request.as_of_date,
        )
        macro_audit.update({
            "benchmark_series": {
                code: {"point_count": len(values)}
                for code, values in benchmark_returns.items()
            },
            "valuation_indices": sorted(valuation_data.keys()),
        })
        if benchmark_returns:
            macro_result = compute_macro_score(benchmark_returns, valuation_data)
            macro_score = macro_result.composite_score
            macro_audit.update({
                "data_available": macro_result.data_available,
                "market_state": macro_result.market_state,
                "valuation_state": macro_result.valuation_state,
                "score": macro_result.composite_score,
            })
    except Exception as exc:
        await _rollback_if_possible(session)
        macro_score = 0.0
        macro_audit["error"] = str(exc)

    engine_health = None
    oos_snapshots: dict[str, Any] = {}
    if request.enable_reliability_layers:
        try:
            engine_health = await compute_engine_health_async(
                session,
                as_of_date=request.as_of_date,
            )
        except Exception:
            await _rollback_if_possible(session)
            engine_health = None
        try:
            oos_snapshots = OOSValidationStore.load_many(
                request.fund_codes,
                risk_level=request.risk_level,
                as_of_date=request.as_of_date,
            )
        except Exception:
            oos_snapshots = {}

    learned_weights = None
    if request.enable_learned_weights:
        try:
            learned_weights = AdvisorFeedbackLearner.load_learned(
                as_of_date=request.as_of_date,
            )
        except Exception:
            learned_weights = None

    nav_lengths = {
        code: len(records)
        for code, records in nav_data.items()
        if records
    }
    nav_snapshot_providers: set[str] = set()
    for diagnostic in nav_quality_diagnostics.values():
        source_consistency = (diagnostic or {}).get("source_consistency") or {}
        for provider_name in (source_consistency.get("sources") or {}).keys():
            provider = str(provider_name).strip()
            if provider and provider != "unknown":
                nav_snapshot_providers.add(provider)
    meta_snapshot_providers = {
        str((info or {}).get("source")).strip()
        for info in fund_source_map.values()
        if (info or {}).get("source")
    }
    snapshot_archive = SnapshotArchive()
    nav_snapshot_versions = _build_snapshot_lookup_index(
        archive=snapshot_archive,
        endpoint="nav_history",
        fund_codes=request.fund_codes,
        providers=nav_snapshot_providers,
        as_of_date=request.as_of_date,
    )
    meta_snapshot_versions = _build_snapshot_lookup_index(
        archive=snapshot_archive,
        endpoint="fund_meta",
        fund_codes=request.fund_codes,
        providers=meta_snapshot_providers,
        as_of_date=request.as_of_date,
    )
    data_sources = {
        "nav_by_fund": _nav_audit_rows(
            request.fund_codes,
            nav_data,
            nav_quality_diagnostics,
            nav_snapshot_versions=nav_snapshot_versions,
            as_of_date=request.as_of_date,
        ),
        "signals_by_fund": _strategy_signal_audit_rows(request.fund_codes, strategy_signals),
        "rules_by_fund": _rules_audit_rows(
            request.fund_codes,
            fund_rules,
            fund_source_map=fund_source_map,
            meta_snapshot_versions=meta_snapshot_versions,
            as_of_date=request.as_of_date,
        ),
        "macro_cutoff": macro_audit,
        "oos_by_fund": _oos_audit_rows(request.fund_codes, oos_snapshots),
    }
    data_quality_warnings = []
    for code, audit in data_sources["nav_by_fund"].items():
        if not audit.get("has_data"):
            data_quality_warnings.append(f"{code} 缺少 NAV 数据")
        elif int(audit.get("point_count") or 0) < 120:
            data_quality_warnings.append(f"{code} NAV 样本不足 120 条")
        source_audit = audit.get("source_consistency") or {}
        adjustment_audit = audit.get("adjustment_consistency") or {}
        if int(source_audit.get("source_switch_count") or 0) >= 5:
            data_quality_warnings.append(f"{code} NAV 数据源切换频繁")
        if int(source_audit.get("missing_source_count") or 0) > 0:
            data_quality_warnings.append(f"{code} 存在缺少来源标识的 NAV 记录")
        cross_source_audit = audit.get("cross_source_consistency") or {}
        if cross_source_audit.get("hard_gate") or cross_source_audit.get("status") == "fail":
            data_quality_warnings.append(f"{code} 多源 NAV 原始对照失败，已触发硬门禁")
        elif cross_source_audit.get("status") == "warning":
            data_quality_warnings.append(f"{code} 多源 NAV 原始对照存在差异")
        adjusted_coverage = adjustment_audit.get("adjusted_coverage_ratio")
        if isinstance(adjusted_coverage, (int, float)) and adjusted_coverage < 0.9:
            data_quality_warnings.append(f"{code} 复权净值覆盖率偏低")
        if int(adjustment_audit.get("factor_jump_count") or 0) > 0:
            data_quality_warnings.append(f"{code} 复权因子存在异常跳变")
    for code, audit in data_sources["oos_by_fund"].items():
        if not audit.get("has_snapshot"):
            data_quality_warnings.append(f"{code} 缺少 OOS 快照")

    queue_health_payload: dict[str, Any]
    try:
        from app.services.runtime_health import check_queue_health

        queue_health_payload = check_queue_health().to_dict()
    except Exception as exc:
        queue_health_payload = {
            "status": "unknown",
            "redis_available": False,
            "broker_url_configured": False,
            "queues": {},
            "warnings": ["运行时队列健康检查不可用"],
            "error": str(exc),
        }

    trust_scores: list[float] = []
    stale_funds: list[str] = []
    missing_pit_or_oos: list[str] = []
    for code, audit in data_sources["nav_by_fund"].items():
        score = 1.0
        points = int(audit.get("point_count") or 0)
        if not audit.get("has_data"):
            score = 0.0
        elif points < 120:
            score -= 0.35
        elif points < 252:
            score -= 0.15
        max_date = audit.get("max_date")
        if max_date:
            try:
                last_date = date.fromisoformat(str(max_date)[:10])
                ref_date = request.as_of_date or date.today()
                freshness_days = max((ref_date - last_date).days, 0)
                audit["freshness_days"] = freshness_days
                if freshness_days > 10:
                    score -= 0.25
                    stale_funds.append(code)
                elif freshness_days > 3:
                    score -= 0.1
            except ValueError:
                score -= 0.1
        source_audit = audit.get("source_consistency") or {}
        adjustment_audit = audit.get("adjustment_consistency") or {}
        if int(source_audit.get("source_switch_count") or 0) >= 5:
            score -= 0.1
        if int(source_audit.get("missing_source_count") or 0) > 0:
            score -= 0.1
        if int(adjustment_audit.get("factor_jump_count") or 0) > 0:
            score -= 0.2
        if not data_sources["oos_by_fund"].get(code, {}).get("has_snapshot"):
            score -= 0.1
            missing_pit_or_oos.append(code)
        trust_scores.append(max(0.0, min(1.0, score)))
    data_trust_score = round(sum(trust_scores) / len(trust_scores), 4) if trust_scores else 0.0
    if data_trust_score >= 0.85:
        data_trust_level = "high"
    elif data_trust_score >= 0.65:
        data_trust_level = "medium"
    else:
        data_trust_level = "low"

    signal_direction_counter = Counter(
        str(signal.get("direction") or "hold")
        for signal in strategy_signals.values()
    )
    oos_selection_sources = Counter(
        str(getattr(snapshot, "selection_source", "exact") or "exact")
        for snapshot in oos_snapshots.values()
    )
    execution_context = {
        "analysis_mode": request.mode,
        "requested_as_of_date": request.as_of_date.isoformat() if request.as_of_date else None,
        "resolved_risk_level": request.risk_level,
        "strategy_id": request.strategy_id,
        "strategy_name": request.strategy_name,
        "source_result_id": request.source_result_id,
        "enable_reliability_layers": request.enable_reliability_layers,
        "enable_learned_weights": request.enable_learned_weights,
        "fund_count": len(request.fund_codes),
        "fund_codes": list(request.fund_codes),
        "nav_coverage": {
            "funds_with_nav": len(nav_data),
            "missing_funds": [code for code in request.fund_codes if code not in nav_data],
            "min_points": min(nav_lengths.values()) if nav_lengths else 0,
            "max_points": max(nav_lengths.values()) if nav_lengths else 0,
        },
        "strategy_signal_stats": {
            "total": len(strategy_signals),
            "directions": dict(signal_direction_counter),
        },
        "cross_sectional_coverage": {
            "scored_funds": len(cross_sectional_scores),
            "missing_funds": [
                code for code in request.fund_codes if code not in cross_sectional_scores
            ],
        },
        "macro_score": macro_score,
        "data_sources": data_sources,
        "data_quality_warnings": data_quality_warnings,
        "data_trust": {
            "score": data_trust_score,
            "level": data_trust_level,
            "stale_funds": stale_funds,
            "missing_oos_snapshot_funds": missing_pit_or_oos,
            "warnings": data_quality_warnings,
        },
        "parameter_set": parameter_set_context,
        "engine_health": {
            "status": getattr(engine_health, "status", None),
            "status_reason": getattr(engine_health, "status_reason", None),
            "rolling_ic_20d": getattr(engine_health, "rolling_ic_20d", None),
            "rolling_ic_samples": getattr(engine_health, "rolling_ic_samples", None),
            "ic_trend": getattr(engine_health, "ic_trend", None),
        } if engine_health is not None else None,
        "runtime_health": {
            "queue": queue_health_payload,
            "status": "healthy" if queue_health_payload.get("status") == "healthy" else "degraded",
            "warnings": list(queue_health_payload.get("warnings") or []),
        },
        "oos_context": {
            "snapshot_count": len(oos_snapshots),
            "selection_sources": dict(oos_selection_sources),
            "resolved_funds": sorted(oos_snapshots.keys()),
        },
        "user_learning": user_learning_profile.to_dict() if user_learning_profile is not None else request.user_profile.get("advisor_personalization"),
        "learned_params": {
            "version_id": getattr(learned_weights, "version_id", None) if learned_weights else None,
            "engine_version": getattr(learned_weights, "engine_version", None) if learned_weights else None,
            "learn_date": _serialize_date_like(getattr(learned_weights, "learn_date", None)) if learned_weights else None,
            "confidence": getattr(learned_weights, "confidence", None) if learned_weights else None,
            "sample_count": getattr(learned_weights, "sample_count", None) if learned_weights else None,
            "threshold_adjustment": getattr(learned_weights, "threshold_adjustment", None) if learned_weights else None,
            "config_hash": getattr(learned_weights, "config_hash", None) if learned_weights else None,
            "gate_status": getattr(learned_weights, "gate_status", None) if learned_weights else None,
            "gate_action": getattr(learned_weights, "gate_action", None) if learned_weights else None,
            "gate_reason": getattr(learned_weights, "gate_reason", None) if learned_weights else None,
            "gate_checked_at": getattr(learned_weights, "gate_checked_at", None) if learned_weights else None,
            "gate_metrics": getattr(learned_weights, "gate_metrics", None) if learned_weights else None,
            "resolved_as_of_date": request.as_of_date.isoformat() if request.as_of_date else None,
        } if learned_weights is not None else {
            "version_id": None,
            "gate_status": "not_loaded",
            "gate_action": "default_params",
            "gate_reason": "未加载已批准的学习参数；使用默认参数或 shadow 参数被门禁拦截",
            "resolved_as_of_date": request.as_of_date.isoformat() if request.as_of_date else None,
        },
    }

    return AdvisorExecutionBundle(
        config=config,
        nav_data=nav_data,
        strategy_signals=strategy_signals,
        fund_names=fund_names,
        fund_types=fund_types,
        fee_data=fee_data,
        fund_rules=fund_rules,
        last_advices=last_advices,
        cross_sectional_scores=cross_sectional_scores,
        macro_score=macro_score,
        engine_health=engine_health,
        oos_snapshots=oos_snapshots,
        learned_weights=learned_weights,
        user_learning_profile=user_learning_profile,
        nav_quality_diagnostics=nav_quality_diagnostics,
        parameter_set=parameter_set,
        execution_context=execution_context,
    )


async def execute_advisor_request(
    request: AdvisorExecutionRequest,
    session: AsyncSession,
) -> tuple[list[TradingAdvice], AdvisorExecutionBundle]:
    """统一执行建议请求。"""
    bundle = await build_execution_bundle(request, session)
    advices = run_execution_bundle(request, bundle)
    return advices, bundle



def _apply_advisor_risk_constraints(
    request: AdvisorExecutionRequest,
    advices: list[TradingAdvice],
) -> None:
    """Apply user-facing risk constraints to Advisor outputs in-place."""
    total_capital = max(float(request.total_capital or 0.0), 1.0)
    profile_caps = {
        "conservative": {"single": 0.15, "qdii": 0.10, "cash": 0.08, "trade": 0.08},
        "moderate": {"single": 0.25, "qdii": 0.18, "cash": 0.05, "trade": 0.12},
        "aggressive": {"single": 0.35, "qdii": 0.28, "cash": 0.03, "trade": 0.20},
    }
    caps = profile_caps.get(request.risk_level, profile_caps["moderate"])
    profile = request.user_profile or {}
    if profile.get("liquidity_need") == "high":
        caps = {**caps, "cash": max(caps["cash"], 0.12), "trade": min(caps["trade"], 0.08)}
    if profile.get("industry_concentration_tolerance") == "low":
        caps = {**caps, "single": min(caps["single"], 0.18)}
    if profile.get("qdii_fx_risk_tolerance") == "low":
        caps = {**caps, "qdii": min(caps["qdii"], 0.10)}

    total_current = sum(max(float(v or 0.0), 0.0) for v in request.current_positions.values())
    available_cash_after_reserve = max(total_capital * (1.0 - caps["cash"]) - total_current, 0.0)
    max_single_amount = total_capital * caps["single"]
    max_trade_amount = total_capital * caps["trade"]

    for advice in advices:
        current_value = max(float(request.current_positions.get(advice.fund_code, 0.0) or 0.0), 0.0)
        fund_type = str(advice.fund_type or "").lower()
        qdii_like = fund_type == "qdii" or "qdii" in fund_type
        fund_cap = total_capital * (caps["qdii"] if qdii_like else caps["single"])
        original_amount = max(float(advice.suggested_amount or 0.0), 0.0)
        allowed_by_position = max(fund_cap - current_value, 0.0) if advice.action == "buy" else original_amount
        allowed_amount = min(original_amount, allowed_by_position, max_trade_amount)
        if advice.action == "buy":
            allowed_amount = min(allowed_amount, available_cash_after_reserve)

        violations: list[dict[str, Any]] = []
        if original_amount > max_trade_amount > 0:
            violations.append({
                "code": "max_single_trade",
                "severity": "warning",
                "message": f"单次交易金额超过 {caps['trade']:.0%} 风控上限，已缩减",
                "limit": round(max_trade_amount, 2),
                "actual": round(original_amount, 2),
            })
        if advice.action == "buy" and current_value + original_amount > fund_cap:
            violations.append({
                "code": "max_position_weight",
                "severity": "high",
                "message": f"操作后单基金/QDII 仓位超过 {caps['qdii' if qdii_like else 'single']:.0%} 上限，已缩减或阻断",
                "limit": round(fund_cap, 2),
                "actual": round(current_value + original_amount, 2),
            })
        if advice.action == "buy" and original_amount > available_cash_after_reserve:
            violations.append({
                "code": "min_cash_reserve",
                "severity": "warning",
                "message": f"需保留至少 {caps['cash']:.0%} 现金/流动性缓冲，买入金额已缩减",
                "limit": round(available_cash_after_reserve, 2),
                "actual": round(original_amount, 2),
            })

        blocked = advice.action == "buy" and original_amount > 0 and allowed_amount <= 0
        if advice.action == "buy" and allowed_amount < original_amount:
            advice.suggested_amount = round(max(allowed_amount, 0.0), 2)
            advice.suggested_pct = round(advice.suggested_amount / total_capital, 6)
            advice.position_after = round((current_value + advice.suggested_amount) / total_capital, 6)
            if advice.trade_plan is not None:
                advice.trade_plan.suggested_amount = advice.suggested_amount
                advice.trade_plan.max_amount = min(advice.trade_plan.max_amount, advice.suggested_amount)
                advice.trade_plan.target_weight = advice.position_after
            if blocked:
                advice.action = "hold"
                advice.support_action = "risk_alert"
                advice.support_label = "风控阻断"
                advice.risk_warnings.append("风控约束阻断本次买入：仓位、现金缓冲或 QDII 暴露已达上限")
            else:
                advice.risk_warnings.append("建议金额已按仓位/现金/单次交易风控上限自动缩减")

        advice.risk_constraints = {
            "status": "blocked" if blocked else ("adjusted" if violations else "passed"),
            "constraints": {
                "max_single_weight": caps["single"],
                "max_qdii_weight": caps["qdii"],
                "min_cash_reserve": caps["cash"],
                "max_single_trade_weight": caps["trade"],
            },
            "violations": violations,
            "original_suggested_amount": round(original_amount, 2),
            "adjusted_suggested_amount": round(float(advice.suggested_amount or 0.0), 2),
            "blocked_actions": ["buy"] if blocked else [],
        }


def run_execution_bundle(
    request: AdvisorExecutionRequest,
    bundle: AdvisorExecutionBundle,
) -> list[TradingAdvice]:
    """使用已准备好的 bundle 运行建议引擎。"""
    advisor = TradingAdvisor(
        config=bundle.config,
        total_capital=request.total_capital,
        current_positions=request.current_positions,
        positions_detail=request.positions_detail,
        last_advices=bundle.last_advices,
        cross_sectional_scores=bundle.cross_sectional_scores,
        macro_score=bundle.macro_score,
        user_profile=request.user_profile,
        engine_health=bundle.engine_health,
        oos_snapshots=bundle.oos_snapshots,
        learned_weights=bundle.learned_weights,
        as_of_date=request.as_of_date,
        nav_quality_diagnostics=bundle.nav_quality_diagnostics,
    )
    advices = advisor.generate_advice(
        fund_codes=request.fund_codes,
        nav_data=bundle.nav_data,
        strategy_signals=bundle.strategy_signals,
        fund_names=bundle.fund_names,
        fund_types=bundle.fund_types,
        fee_data=bundle.fee_data,
        fund_rules=bundle.fund_rules,
    )
    _apply_advisor_risk_constraints(request, advices)
    return advices


def build_result_execution_context(
    request: AdvisorExecutionRequest,
    bundle: AdvisorExecutionBundle,
    advices: list[TradingAdvice],
) -> dict[str, Any]:
    """构建落库到 advisor_results 的执行审计上下文。"""
    action_counts = Counter(advice.action for advice in advices)
    non_hold_advices = [advice for advice in advices if advice.action != "hold"]
    top_signals = [
        {
            "fund_code": advice.fund_code,
            "action": advice.action,
            "confidence": round(float(advice.confidence), 4),
            "composite_score": round(float(advice.composite_score), 4),
        }
        for advice in sorted(
            advices,
            key=lambda item: abs(float(item.composite_score)),
            reverse=True,
        )[:5]
    ]
    payload = dict(getattr(bundle, "execution_context", {}) or {})
    payload.update(
        {
            "action_summary": dict(action_counts),
            "non_hold_count": len(non_hold_advices),
            "top_signals": top_signals,
            "advisor_output": {
                "advice_count": len(advices),
                "buy_count": action_counts.get("buy", 0),
                "sell_count": action_counts.get("sell", 0),
                "hold_count": action_counts.get("hold", 0),
                "max_abs_composite_score": round(
                    max((abs(float(advice.composite_score)) for advice in advices), default=0.0),
                    4,
                ),
            },
        }
    )

    learned_weights = getattr(bundle, "learned_weights", None)
    learned_params = dict(payload.get("learned_params") or {})
    if learned_weights is not None or learned_params:
        defaults = {
            "version_id": getattr(learned_weights, "version_id", None) if learned_weights else None,
            "engine_version": getattr(learned_weights, "engine_version", None) if learned_weights else None,
            "learn_date": _serialize_date_like(getattr(learned_weights, "learn_date", None)) if learned_weights else None,
            "confidence": getattr(learned_weights, "confidence", None) if learned_weights else None,
            "sample_count": getattr(learned_weights, "sample_count", None) if learned_weights else None,
            "threshold_adjustment": getattr(learned_weights, "threshold_adjustment", None) if learned_weights else None,
            "config_hash": getattr(learned_weights, "config_hash", None) if learned_weights else None,
            "gate_status": getattr(learned_weights, "gate_status", "not_evaluated") if learned_weights else "not_evaluated",
            "gate_action": getattr(learned_weights, "gate_action", "shadow_only") if learned_weights else "shadow_only",
            "gate_reason": getattr(learned_weights, "gate_reason", None) if learned_weights else None,
            "gate_checked_at": getattr(learned_weights, "gate_checked_at", None) if learned_weights else None,
            "gate_metrics": getattr(learned_weights, "gate_metrics", None) if learned_weights else None,
            "resolved_as_of_date": request.as_of_date.isoformat() if request.as_of_date else None,
        }
        defaults.update({k: v for k, v in learned_params.items() if v is not None})
        payload["learned_params"] = defaults
    return payload


__all__ = [
    "AdvisorExecutionRequest",
    "AdvisorExecutionBundle",
    "load_last_advices",
    "build_execution_bundle",
    "run_execution_bundle",
    "execute_advisor_request",
    "build_result_execution_context",
]
