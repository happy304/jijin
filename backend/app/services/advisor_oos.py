"""交易建议引擎样本外验证结果缓存与复用。

默认优先使用数据库表 ``advisor_oos_snapshots`` 持久化最近一次
Walk-Forward 验证结果；当数据库表尚未迁移、数据库不可用，或测试中
显式覆写 ``_path`` 时，会回退到旧版 JSON 文件后端以保持兼容。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.data.models.advisor_oos_snapshots import AdvisorOOSSnapshot

DEFAULT_RISK_LEVEL = "moderate"
_VALID_RISK_LEVELS = {"conservative", "moderate", "aggressive"}
_SNAPSHOT_KEYS = {
    "fund_code",
    "risk_level",
    "updated_at",
    "requested_days",
    "actual_trading_days",
    "avg_oos_ic",
    "avg_is_ic",
    "ic_degradation",
    "avg_oos_buy_hit_rate",
    "avg_oos_sell_hit_rate",
    "total_oos_signals",
    "total_oos_buy",
    "total_oos_sell",
    "warnings",
    "snapshot_date",
    "config_hash",
    "data_version",
    "validation_window",
    "pbo",
    "cpcv_n_paths",
    "cpcv_avg_oos_sharpe",
    "cpcv_std_oos_sharpe",
    "cpcv_avg_is_sharpe",
    "multi_objective_score",
    "multi_objective_components",
    "multi_objective_eliminated",
    "multi_objective_reasons",
    "baseline_adjusted_score",
    "baseline_comparison",
    "baseline_passed",
    "baseline_reasons",
}



def _normalize_risk_level(value: str | None) -> str:
    risk_level = str(value or DEFAULT_RISK_LEVEL).strip().lower()
    if risk_level not in _VALID_RISK_LEVELS:
        return DEFAULT_RISK_LEVEL
    return risk_level



def _snapshot_sort_key(snapshot: "OOSValidationSnapshot") -> tuple[date, str]:
    try:
        updated = date.fromisoformat(str(snapshot.updated_at))
    except Exception:
        updated = date.min
    return updated, snapshot.risk_level


@dataclass
class OOSValidationSnapshot:
    """单只基金最近一次样本外验证快照。"""

    fund_code: str
    risk_level: str = DEFAULT_RISK_LEVEL
    updated_at: str = ""
    requested_days: int | None = None
    actual_trading_days: int = 0
    avg_oos_ic: float | None = None
    avg_is_ic: float | None = None
    ic_degradation: float | None = None
    avg_oos_buy_hit_rate: float | None = None
    avg_oos_sell_hit_rate: float | None = None
    total_oos_signals: int = 0
    total_oos_buy: int = 0
    total_oos_sell: int = 0
    warnings: list[str] | None = None
    snapshot_date: str | None = None
    config_hash: str | None = None
    data_version: str | None = None
    validation_window: str | None = None
    pbo: float | None = None
    cpcv_n_paths: int = 0
    cpcv_avg_oos_sharpe: float | None = None
    cpcv_std_oos_sharpe: float | None = None
    cpcv_avg_is_sharpe: float | None = None
    multi_objective_score: float | None = None
    multi_objective_components: dict[str, Any] | None = None
    multi_objective_eliminated: bool = False
    multi_objective_reasons: list[str] | None = None
    baseline_adjusted_score: float | None = None
    baseline_comparison: dict[str, Any] | None = None
    baseline_passed: bool | None = None
    baseline_reasons: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fund_code": self.fund_code,
            "risk_level": _normalize_risk_level(self.risk_level),
            "updated_at": self.updated_at,
            "snapshot_date": self.snapshot_date or self.updated_at,
            "config_hash": self.config_hash,
            "data_version": self.data_version,
            "validation_window": self.validation_window,
            "requested_days": self.requested_days,
            "actual_trading_days": self.actual_trading_days,
            "pbo": self.pbo,
            "cpcv_n_paths": self.cpcv_n_paths,
            "cpcv_avg_oos_sharpe": self.cpcv_avg_oos_sharpe,
            "cpcv_std_oos_sharpe": self.cpcv_std_oos_sharpe,
            "cpcv_avg_is_sharpe": self.cpcv_avg_is_sharpe,
            "multi_objective_score": self.multi_objective_score,
            "multi_objective_components": self.multi_objective_components or {},
            "multi_objective_eliminated": bool(self.multi_objective_eliminated),
            "multi_objective_reasons": self.multi_objective_reasons or [],
            "baseline_adjusted_score": self.baseline_adjusted_score,
            "baseline_comparison": self.baseline_comparison or {},
            "baseline_passed": self.baseline_passed,
            "baseline_reasons": self.baseline_reasons or [],
            "avg_oos_ic": self.avg_oos_ic,
            "avg_is_ic": self.avg_is_ic,
            "ic_degradation": self.ic_degradation,
            "avg_oos_buy_hit_rate": self.avg_oos_buy_hit_rate,
            "avg_oos_sell_hit_rate": self.avg_oos_sell_hit_rate,
            "total_oos_signals": self.total_oos_signals,
            "total_oos_buy": self.total_oos_buy,
            "total_oos_sell": self.total_oos_sell,
            "warnings": self.warnings or [],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OOSValidationSnapshot":
        return cls(
            fund_code=str(data.get("fund_code") or ""),
            risk_level=_normalize_risk_level(data.get("risk_level")),
            updated_at=str(data.get("updated_at") or data.get("snapshot_date") or ""),
            requested_days=data.get("requested_days"),
            actual_trading_days=int(data.get("actual_trading_days") or 0),
            avg_oos_ic=data.get("avg_oos_ic"),
            avg_is_ic=data.get("avg_is_ic"),
            ic_degradation=data.get("ic_degradation"),
            avg_oos_buy_hit_rate=data.get("avg_oos_buy_hit_rate"),
            avg_oos_sell_hit_rate=data.get("avg_oos_sell_hit_rate"),
            total_oos_signals=int(data.get("total_oos_signals") or 0),
            total_oos_buy=int(data.get("total_oos_buy") or 0),
            total_oos_sell=int(data.get("total_oos_sell") or 0),
            warnings=list(data.get("warnings") or []),
            snapshot_date=str(data.get("snapshot_date") or data.get("updated_at") or "") or None,
            config_hash=data.get("config_hash"),
            data_version=data.get("data_version"),
            validation_window=data.get("validation_window"),
            pbo=data.get("pbo"),
            cpcv_n_paths=int(data.get("cpcv_n_paths") or 0),
            cpcv_avg_oos_sharpe=data.get("cpcv_avg_oos_sharpe"),
            cpcv_std_oos_sharpe=data.get("cpcv_std_oos_sharpe"),
            cpcv_avg_is_sharpe=data.get("cpcv_avg_is_sharpe"),
            multi_objective_score=data.get("multi_objective_score"),
            multi_objective_components=dict(data.get("multi_objective_components") or {}),
            multi_objective_eliminated=bool(data.get("multi_objective_eliminated") or False),
            multi_objective_reasons=list(data.get("multi_objective_reasons") or []),
            baseline_adjusted_score=data.get("baseline_adjusted_score"),
            baseline_comparison=dict(data.get("baseline_comparison") or {}),
            baseline_passed=data.get("baseline_passed"),
            baseline_reasons=list(data.get("baseline_reasons") or []),
        )


class OOSValidationStore:
    """数据库优先、文件兼容回退的样本外验证结果存储。"""

    _legacy_migration_done: bool = False

    @staticmethod
    def _default_path() -> Path:
        return Path(__file__).parent.parent / "data" / "oos_validation_snapshots.json"

    @staticmethod
    def _path() -> Path:
        return OOSValidationStore._default_path()

    @staticmethod
    def _is_snapshot_payload(payload: Any) -> bool:
        return isinstance(payload, dict) and any(key in payload for key in _SNAPSHOT_KEYS)

    @classmethod
    def _normalize_snapshot(
        cls,
        fund_code: str,
        payload: Any,
        *,
        fallback_risk_level: str | None = None,
    ) -> OOSValidationSnapshot | None:
        if isinstance(payload, OOSValidationSnapshot):
            snapshot = payload
        elif isinstance(payload, dict):
            snapshot = OOSValidationSnapshot.from_dict(payload)
        else:
            return None

        snapshot.fund_code = str(snapshot.fund_code or fund_code)
        snapshot.risk_level = _normalize_risk_level(snapshot.risk_level or fallback_risk_level)
        return snapshot

    @classmethod
    def _coerce_payload(
        cls,
        raw: Any,
    ) -> dict[str, dict[str, list[OOSValidationSnapshot]]]:
        if not isinstance(raw, dict):
            return {}

        result: dict[str, dict[str, list[OOSValidationSnapshot]]] = {}
        for fund_code, payload in dict(raw).items():
            code = str(fund_code)

            if cls._is_snapshot_payload(payload):
                snapshot = cls._normalize_snapshot(code, payload)
                if snapshot is None:
                    continue
                result.setdefault(code, {}).setdefault(snapshot.risk_level, []).append(snapshot)
                continue

            if not isinstance(payload, dict):
                continue

            for risk_level, snapshot_payload in payload.items():
                if isinstance(snapshot_payload, list):
                    candidates = snapshot_payload
                elif isinstance(snapshot_payload, dict) and isinstance(snapshot_payload.get("versions"), list):
                    candidates = snapshot_payload.get("versions") or []
                else:
                    candidates = [snapshot_payload]

                for item in candidates:
                    if not cls._is_snapshot_payload(item):
                        continue
                    snapshot = cls._normalize_snapshot(
                        code,
                        item,
                        fallback_risk_level=str(risk_level),
                    )
                    if snapshot is None:
                        continue
                    result.setdefault(code, {}).setdefault(snapshot.risk_level, []).append(snapshot)

        for snapshots_by_risk in result.values():
            for snapshots in snapshots_by_risk.values():
                snapshots.sort(key=_snapshot_sort_key)
        return result

    @classmethod
    def _select_latest_by_risk(
        cls,
        data: dict[str, dict[str, list[OOSValidationSnapshot]]],
    ) -> dict[str, dict[str, OOSValidationSnapshot]]:
        payload: dict[str, dict[str, OOSValidationSnapshot]] = {}
        for fund_code, snapshots_by_risk in data.items():
            for risk_level, snapshots in snapshots_by_risk.items():
                if not snapshots:
                    continue
                payload.setdefault(fund_code, {})[risk_level] = max(snapshots, key=_snapshot_sort_key)
        return payload

    @classmethod
    def _serialize_payload(
        cls,
        data: dict[str, dict[str, list[OOSValidationSnapshot]]],
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        payload: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for fund_code, snapshots_by_risk in data.items():
            if not snapshots_by_risk:
                continue
            payload[fund_code] = {
                risk_level: [snapshot.to_dict() for snapshot in sorted(snapshots, key=_snapshot_sort_key)]
                for risk_level, snapshots in sorted(snapshots_by_risk.items())
                if snapshots
            }
        return payload

    @classmethod
    def _prefer_file_backend(cls) -> bool:
        try:
            return cls._path() != cls._default_path()
        except Exception:
            return False

    @classmethod
    def _file_load_all(
        cls,
        *,
        as_of_date: date | None = None,
    ) -> dict[str, dict[str, OOSValidationSnapshot]]:
        path = cls._path()
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        versioned = cls._coerce_payload(raw)
        if as_of_date is None:
            return cls._select_latest_by_risk(versioned)

        filtered: dict[str, dict[str, list[OOSValidationSnapshot]]] = {}
        for fund_code, snapshots_by_risk in versioned.items():
            for risk_level, snapshots in snapshots_by_risk.items():
                for snapshot in snapshots:
                    try:
                        updated_at = date.fromisoformat(str(snapshot.updated_at))
                    except Exception:
                        continue
                    if updated_at <= as_of_date:
                        filtered.setdefault(fund_code, {}).setdefault(risk_level, []).append(snapshot)
        return cls._select_latest_by_risk(filtered)

    @classmethod
    def _file_save(cls, snapshot: OOSValidationSnapshot) -> None:
        path = cls._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            raw = {}
        current = cls._coerce_payload(raw)

        snapshot.fund_code = str(snapshot.fund_code)
        snapshot.risk_level = _normalize_risk_level(snapshot.risk_level)
        if not snapshot.updated_at:
            snapshot.updated_at = date.today().isoformat()
        if not snapshot.snapshot_date:
            snapshot.snapshot_date = snapshot.updated_at

        versions = current.setdefault(snapshot.fund_code, {}).setdefault(snapshot.risk_level, [])
        incoming_key = cls._snapshot_identity(snapshot)
        for idx, existing in enumerate(versions):
            if cls._snapshot_identity(existing) == incoming_key:
                versions[idx] = snapshot
                break
        else:
            versions.append(snapshot)
        payload = cls._serialize_payload(current)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def _db_engine(cls):
        settings = get_settings()
        return create_engine(settings.database_sync_url)

    @classmethod
    def _db_available(cls) -> bool:
        try:
            engine = cls._db_engine()
            try:
                inspector = inspect(engine)
                return inspector.has_table("advisor_oos_snapshots")
            finally:
                engine.dispose()
        except Exception:
            return False

    @staticmethod
    def _snapshot_identity(snapshot: OOSValidationSnapshot) -> tuple[str, str, str, str, str, str]:
        return (
            str(snapshot.fund_code),
            _normalize_risk_level(snapshot.risk_level),
            str(snapshot.snapshot_date or snapshot.updated_at or ""),
            str(snapshot.config_hash or ""),
            str(snapshot.data_version or ""),
            str(snapshot.validation_window or ""),
        )

    @staticmethod
    def _row_to_snapshot(row: AdvisorOOSSnapshot) -> OOSValidationSnapshot:
        return OOSValidationSnapshot(
            fund_code=str(row.fund_code),
            risk_level=_normalize_risk_level(row.risk_level),
            updated_at=str(row.updated_at or ""),
            snapshot_date=str(row.snapshot_date or row.updated_at or "") or None,
            config_hash=row.config_hash,
            data_version=row.data_version,
            validation_window=row.validation_window,
            requested_days=row.requested_days,
            actual_trading_days=int(row.actual_trading_days or 0),
            pbo=getattr(row, "pbo", None),
            cpcv_n_paths=int(getattr(row, "cpcv_n_paths", 0) or 0),
            cpcv_avg_oos_sharpe=getattr(row, "cpcv_avg_oos_sharpe", None),
            cpcv_std_oos_sharpe=getattr(row, "cpcv_std_oos_sharpe", None),
            cpcv_avg_is_sharpe=getattr(row, "cpcv_avg_is_sharpe", None),
            multi_objective_score=getattr(row, "multi_objective_score", None),
            multi_objective_components=dict(getattr(row, "multi_objective_components", None) or {}),
            multi_objective_eliminated=bool(getattr(row, "multi_objective_eliminated", False) or False),
            multi_objective_reasons=list(getattr(row, "multi_objective_reasons", None) or []),
            baseline_adjusted_score=getattr(row, "baseline_adjusted_score", None),
            baseline_comparison=dict(getattr(row, "baseline_comparison", None) or {}),
            baseline_passed=getattr(row, "baseline_passed", None),
            baseline_reasons=list(getattr(row, "baseline_reasons", None) or []),
            avg_oos_ic=row.avg_oos_ic,
            avg_is_ic=row.avg_is_ic,
            ic_degradation=row.ic_degradation,
            avg_oos_buy_hit_rate=row.avg_oos_buy_hit_rate,
            avg_oos_sell_hit_rate=row.avg_oos_sell_hit_rate,
            total_oos_signals=int(row.total_oos_signals or 0),
            total_oos_buy=int(row.total_oos_buy or 0),
            total_oos_sell=int(row.total_oos_sell or 0),
            warnings=list(row.warnings_json or []),
        )

    @classmethod
    def _db_load_all(
        cls,
        *,
        as_of_date: date | None = None,
    ) -> dict[str, dict[str, OOSValidationSnapshot]]:
        engine = cls._db_engine()
        try:
            with Session(engine) as session:
                stmt = select(AdvisorOOSSnapshot)
                if as_of_date is not None:
                    stmt = stmt.where(AdvisorOOSSnapshot.updated_at <= as_of_date)
                rows = session.execute(stmt).scalars().all()
                payload: dict[str, dict[str, OOSValidationSnapshot]] = {}
                for row in rows:
                    snapshot = cls._row_to_snapshot(row)
                    current = payload.setdefault(snapshot.fund_code, {}).get(snapshot.risk_level)
                    if current is None or _snapshot_sort_key(snapshot) > _snapshot_sort_key(current):
                        payload.setdefault(snapshot.fund_code, {})[snapshot.risk_level] = snapshot
                return payload
        finally:
            engine.dispose()

    @classmethod
    def import_legacy_file_if_needed(cls) -> int:
        """当数据库可用且表为空时，将旧 JSON 数据导入数据库。"""
        if cls._prefer_file_backend() or cls._legacy_migration_done:
            return 0
        if not cls._db_available():
            return 0

        legacy_path = cls._default_path()
        if not legacy_path.exists():
            cls._legacy_migration_done = True
            return 0

        try:
            db_data = cls._db_load_all()
        except Exception:
            return 0

        if db_data:
            cls._legacy_migration_done = True
            return 0

        legacy_data = cls._file_load_all()
        imported = 0
        for snapshots_by_risk in legacy_data.values():
            for snapshot in snapshots_by_risk.values():
                cls._db_save(snapshot)
                imported += 1

        cls._legacy_migration_done = True
        return imported

    @classmethod
    def _db_save(cls, snapshot: OOSValidationSnapshot) -> None:
        engine = cls._db_engine()
        try:
            with Session(engine) as session:
                snapshot.fund_code = str(snapshot.fund_code)
                snapshot.risk_level = _normalize_risk_level(snapshot.risk_level)
                if not snapshot.updated_at:
                    snapshot.updated_at = date.today().isoformat()
                if not snapshot.snapshot_date:
                    snapshot.snapshot_date = snapshot.updated_at

                updated_at = date.fromisoformat(str(snapshot.updated_at))
                snapshot_date = date.fromisoformat(str(snapshot.snapshot_date or snapshot.updated_at))
                stmt = select(AdvisorOOSSnapshot).where(
                    AdvisorOOSSnapshot.fund_code == snapshot.fund_code,
                    AdvisorOOSSnapshot.risk_level == snapshot.risk_level,
                    AdvisorOOSSnapshot.snapshot_date == snapshot_date,
                )
                if snapshot.config_hash is None:
                    stmt = stmt.where(AdvisorOOSSnapshot.config_hash.is_(None))
                else:
                    stmt = stmt.where(AdvisorOOSSnapshot.config_hash == snapshot.config_hash)
                if snapshot.data_version is None:
                    stmt = stmt.where(AdvisorOOSSnapshot.data_version.is_(None))
                else:
                    stmt = stmt.where(AdvisorOOSSnapshot.data_version == snapshot.data_version)
                if snapshot.validation_window is None:
                    stmt = stmt.where(AdvisorOOSSnapshot.validation_window.is_(None))
                else:
                    stmt = stmt.where(AdvisorOOSSnapshot.validation_window == snapshot.validation_window)
                existing = session.execute(stmt).scalar_one_or_none()
                if existing is None:
                    existing = AdvisorOOSSnapshot(
                        fund_code=snapshot.fund_code,
                        risk_level=snapshot.risk_level,
                        updated_at=updated_at,
                        snapshot_date=snapshot_date,
                        config_hash=snapshot.config_hash,
                        data_version=snapshot.data_version,
                        validation_window=snapshot.validation_window,
                        actual_trading_days=0,
                        cpcv_n_paths=0,
                        total_oos_signals=0,
                        total_oos_buy=0,
                        total_oos_sell=0,
                    )
                    session.add(existing)

                existing.fund_code = snapshot.fund_code
                existing.risk_level = snapshot.risk_level
                existing.updated_at = updated_at
                existing.snapshot_date = snapshot_date
                existing.config_hash = snapshot.config_hash
                existing.data_version = snapshot.data_version
                existing.validation_window = snapshot.validation_window
                existing.requested_days = snapshot.requested_days
                existing.actual_trading_days = int(snapshot.actual_trading_days or 0)
                existing.pbo = snapshot.pbo
                existing.cpcv_n_paths = int(snapshot.cpcv_n_paths or 0)
                existing.cpcv_avg_oos_sharpe = snapshot.cpcv_avg_oos_sharpe
                existing.cpcv_std_oos_sharpe = snapshot.cpcv_std_oos_sharpe
                existing.cpcv_avg_is_sharpe = snapshot.cpcv_avg_is_sharpe
                if hasattr(existing, "multi_objective_score"):
                    existing.multi_objective_score = snapshot.multi_objective_score
                    existing.multi_objective_components = dict(snapshot.multi_objective_components or {})
                    existing.multi_objective_eliminated = bool(snapshot.multi_objective_eliminated)
                    existing.multi_objective_reasons = list(snapshot.multi_objective_reasons or [])
                if hasattr(existing, "baseline_adjusted_score"):
                    existing.baseline_adjusted_score = snapshot.baseline_adjusted_score
                    existing.baseline_comparison = dict(snapshot.baseline_comparison or {})
                    existing.baseline_passed = snapshot.baseline_passed
                    existing.baseline_reasons = list(snapshot.baseline_reasons or [])
                existing.avg_oos_ic = snapshot.avg_oos_ic
                existing.avg_is_ic = snapshot.avg_is_ic
                existing.ic_degradation = snapshot.ic_degradation
                existing.avg_oos_buy_hit_rate = snapshot.avg_oos_buy_hit_rate
                existing.avg_oos_sell_hit_rate = snapshot.avg_oos_sell_hit_rate
                existing.total_oos_signals = int(snapshot.total_oos_signals or 0)
                existing.total_oos_buy = int(snapshot.total_oos_buy or 0)
                existing.total_oos_sell = int(snapshot.total_oos_sell or 0)
                existing.warnings_json = list(snapshot.warnings or [])
                session.commit()
        finally:
            engine.dispose()

    @classmethod
    def load_all(
        cls,
        *,
        as_of_date: date | None = None,
    ) -> dict[str, dict[str, OOSValidationSnapshot]]:
        if cls._prefer_file_backend():
            return cls._file_load_all(as_of_date=as_of_date)
        if cls._db_available():
            try:
                cls.import_legacy_file_if_needed()
                return cls._db_load_all(as_of_date=as_of_date)
            except Exception:
                pass
        return cls._file_load_all(as_of_date=as_of_date)

    @classmethod
    def load_exact(
        cls,
        fund_code: str,
        risk_level: str = DEFAULT_RISK_LEVEL,
        *,
        as_of_date: date | None = None,
    ) -> OOSValidationSnapshot | None:
        snapshots_by_risk = cls.load_all(as_of_date=as_of_date).get(str(fund_code), {})
        return snapshots_by_risk.get(_normalize_risk_level(risk_level))

    @classmethod
    def _select_snapshot(
        cls,
        snapshots_by_risk: dict[str, OOSValidationSnapshot],
        risk_level: str = DEFAULT_RISK_LEVEL,
    ) -> OOSValidationSnapshot | None:
        normalized_risk = _normalize_risk_level(risk_level)
        selected: OOSValidationSnapshot | None = None
        selection_source: str | None = None

        if normalized_risk in snapshots_by_risk:
            selected = snapshots_by_risk[normalized_risk]
            selection_source = "exact"
        elif normalized_risk != DEFAULT_RISK_LEVEL and DEFAULT_RISK_LEVEL in snapshots_by_risk:
            selected = snapshots_by_risk[DEFAULT_RISK_LEVEL]
            selection_source = "moderate_fallback"
        elif snapshots_by_risk:
            selected = max(snapshots_by_risk.values(), key=_snapshot_sort_key)
            selection_source = "latest_fallback"

        if selected is None:
            return None

        snapshot = replace(selected)
        setattr(snapshot, "selection_source", selection_source)
        setattr(snapshot, "requested_risk_level", normalized_risk)
        return snapshot

    @classmethod
    def load(
        cls,
        fund_code: str,
        risk_level: str = DEFAULT_RISK_LEVEL,
        *,
        as_of_date: date | None = None,
    ) -> OOSValidationSnapshot | None:
        snapshots_by_risk = cls.load_all(as_of_date=as_of_date).get(str(fund_code), {})
        return cls._select_snapshot(snapshots_by_risk, risk_level=risk_level)

    @classmethod
    def load_many(
        cls,
        fund_codes: list[str],
        risk_level: str = DEFAULT_RISK_LEVEL,
        *,
        as_of_date: date | None = None,
    ) -> dict[str, OOSValidationSnapshot]:
        all_data = cls.load_all(as_of_date=as_of_date)
        snapshots: dict[str, OOSValidationSnapshot] = {}
        for fund_code in dict.fromkeys(str(code) for code in fund_codes if code):
            snapshot = cls._select_snapshot(
                all_data.get(fund_code, {}),
                risk_level=risk_level,
            )
            if snapshot is not None:
                snapshots[fund_code] = snapshot
        return snapshots

    @staticmethod
    def is_stale(
        snapshot: OOSValidationSnapshot | None,
        *,
        as_of: date | None = None,
        max_age_days: int = 1,
    ) -> bool:
        if snapshot is None:
            return True
        as_of = as_of or date.today()
        try:
            updated_at = date.fromisoformat(str(snapshot.updated_at))
        except Exception:
            return True
        return (as_of - updated_at).days >= max_age_days

    @classmethod
    def stale_fund_codes(
        cls,
        fund_codes: list[str],
        risk_level: str = DEFAULT_RISK_LEVEL,
        *,
        as_of: date | None = None,
        max_age_days: int = 1,
    ) -> list[str]:
        all_data = cls.load_all()
        normalized_risk = _normalize_risk_level(risk_level)
        stale_codes: list[str] = []
        for fund_code in dict.fromkeys(str(code) for code in fund_codes if code):
            snapshot = all_data.get(fund_code, {}).get(normalized_risk)
            if cls.is_stale(snapshot, as_of=as_of, max_age_days=max_age_days):
                stale_codes.append(fund_code)
        return stale_codes

    @classmethod
    def save(cls, snapshot: OOSValidationSnapshot) -> None:
        snapshot.fund_code = str(snapshot.fund_code)
        snapshot.risk_level = _normalize_risk_level(snapshot.risk_level)
        if cls._prefer_file_backend():
            cls._file_save(snapshot)
            return
        if cls._db_available():
            try:
                cls._db_save(snapshot)
                return
            except Exception:
                pass
        cls._file_save(snapshot)

    @classmethod
    def save_from_walk_forward_result(
        cls,
        fund_code: str,
        risk_level: str,
        result_dict: dict[str, Any],
    ) -> OOSValidationSnapshot:
        summary = dict(result_dict.get("summary") or {})
        data_info = dict(result_dict.get("data_info") or {})
        cpcv = dict(result_dict.get("cpcv") or result_dict.get("pbo_diagnostics") or {})
        today = date.today().isoformat()
        data_start = data_info.get("data_start_date")
        data_end = data_info.get("data_end_date")
        requested_days = data_info.get("requested_days")
        n_folds = result_dict.get("n_folds")
        train_window = result_dict.get("train_window_days")
        test_window = result_dict.get("test_window_days")
        validation_window = (
            f"{data_start or '?'}~{data_end or '?'};"
            f"requested={requested_days or 'all'};folds={n_folds or 0};"
            f"train={train_window or 0};test={test_window or 0}"
        )
        data_version = f"{data_start or '?'}~{data_end or '?'};n={int(data_info.get('actual_trading_days') or 0)}"
        config_payload = json.dumps(
            {
                "risk_level": _normalize_risk_level(risk_level),
                "requested_days": requested_days,
                "n_folds": n_folds,
                "train_window_days": train_window,
                "test_window_days": test_window,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        config_hash = hashlib.sha256(config_payload.encode("utf-8")).hexdigest()[:16]
        from app.services.optimization import (
            compare_against_baselines,
            compute_multi_objective_score,
        )

        multi_metrics = {
            "avg_oos_ic": summary.get("avg_oos_ic"),
            "avg_oos_buy_hit_rate": summary.get("avg_oos_buy_hit_rate"),
            "avg_oos_sell_hit_rate": summary.get("avg_oos_sell_hit_rate"),
            "ic_degradation": summary.get("ic_degradation"),
            "total_oos_signals": summary.get("total_oos_signals"),
            "pbo": cpcv.get("pbo"),
            "avg_oos_sharpe": cpcv.get("avg_oos_sharpe"),
            "avg_is_sharpe": cpcv.get("avg_is_sharpe"),
            "sample_count": summary.get("total_oos_signals") or data_info.get("actual_trading_days"),
        }
        multi_score = compute_multi_objective_score(multi_metrics)
        baseline_input = dict(result_dict.get("baseline_metrics") or result_dict.get("baselines") or {})
        baseline_result = None
        if baseline_input:
            baseline_result = compare_against_baselines(
                {**multi_metrics, **multi_score.to_metrics()},
                baseline_metrics=baseline_input,
            )

        snapshot = OOSValidationSnapshot(
            fund_code=fund_code,
            risk_level=_normalize_risk_level(risk_level),
            updated_at=today,
            snapshot_date=today,
            config_hash=config_hash,
            data_version=data_version,
            validation_window=validation_window,
            requested_days=requested_days,
            actual_trading_days=int(data_info.get("actual_trading_days") or 0),
            pbo=cpcv.get("pbo"),
            cpcv_n_paths=int(cpcv.get("n_paths") or cpcv.get("cpcv_n_paths") or 0),
            cpcv_avg_oos_sharpe=cpcv.get("avg_oos_sharpe"),
            cpcv_std_oos_sharpe=cpcv.get("std_oos_sharpe"),
            cpcv_avg_is_sharpe=cpcv.get("avg_is_sharpe"),
            multi_objective_score=round(float(multi_score.score), 6),
            multi_objective_components=multi_score.components,
            multi_objective_eliminated=multi_score.eliminated,
            multi_objective_reasons=multi_score.reasons,
            baseline_adjusted_score=(
                round(float(baseline_result.adjusted_score), 6)
                if baseline_result is not None else None
            ),
            baseline_comparison=(baseline_result.comparisons if baseline_result is not None else {}),
            baseline_passed=(baseline_result.passed if baseline_result is not None else None),
            baseline_reasons=(baseline_result.reasons if baseline_result is not None else []),
            avg_oos_ic=summary.get("avg_oos_ic"),
            avg_is_ic=summary.get("avg_is_ic"),
            ic_degradation=summary.get("ic_degradation"),
            avg_oos_buy_hit_rate=summary.get("avg_oos_buy_hit_rate"),
            avg_oos_sell_hit_rate=summary.get("avg_oos_sell_hit_rate"),
            total_oos_signals=int(summary.get("total_oos_signals") or 0),
            total_oos_buy=int(summary.get("total_oos_buy") or 0),
            total_oos_sell=int(summary.get("total_oos_sell") or 0),
            warnings=list(result_dict.get("warnings") or []),
        )
        cls.save(snapshot)
        return snapshot


__all__ = ["DEFAULT_RISK_LEVEL", "OOSValidationSnapshot", "OOSValidationStore"]
