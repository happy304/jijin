"""User execution records for saved advisor results.

This module keeps the advisor outcome loop honest by separating three things:

1. what the model recommended,
2. what the user actually executed,
3. what later market data did after the recommendation.
"""

from __future__ import annotations

import csv
import io
import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.data.models.advisor_execution_records import AdvisorExecutionRecord

EXECUTION_STATUS_PLANNED = "planned"
EXECUTION_STATUS_EXECUTED = "executed"
EXECUTION_STATUS_PARTIAL = "partial"
EXECUTION_STATUS_NOT_EXECUTED = "not_executed"

EXECUTION_STATUSES = {
    EXECUTION_STATUS_PLANNED,
    EXECUTION_STATUS_EXECUTED,
    EXECUTION_STATUS_PARTIAL,
    EXECUTION_STATUS_NOT_EXECUTED,
}

EXECUTION_STATUS_ALIASES = {
    "计划执行": EXECUTION_STATUS_PLANNED,
    "计划": EXECUTION_STATUS_PLANNED,
    "planned": EXECUTION_STATUS_PLANNED,
    "已执行": EXECUTION_STATUS_EXECUTED,
    "执行": EXECUTION_STATUS_EXECUTED,
    "成交": EXECUTION_STATUS_EXECUTED,
    "executed": EXECUTION_STATUS_EXECUTED,
    "部分执行": EXECUTION_STATUS_PARTIAL,
    "部分成交": EXECUTION_STATUS_PARTIAL,
    "partial": EXECUTION_STATUS_PARTIAL,
    "未执行": EXECUTION_STATUS_NOT_EXECUTED,
    "未成交": EXECUTION_STATUS_NOT_EXECUTED,
    "not_executed": EXECUTION_STATUS_NOT_EXECUTED,
    "not executed": EXECUTION_STATUS_NOT_EXECUTED,
}

TRADE_INTENT_ALIASES = {
    "申购": "subscribe",
    "买入": "subscribe",
    "buy": "subscribe",
    "subscribe": "subscribe",
    "赎回": "redeem",
    "卖出": "redeem",
    "sell": "redeem",
    "redeem": "redeem",
    "持有": "hold",
    "不操作": "hold",
    "hold": "hold",
}

ACTION_ALIASES = {
    "申购": "buy",
    "买入": "buy",
    "buy": "buy",
    "subscribe": "buy",
    "赎回": "sell",
    "卖出": "sell",
    "sell": "sell",
    "redeem": "sell",
    "持有": "hold",
    "不操作": "hold",
    "hold": "hold",
}

ACTIONABLE_ACTIONS = {"buy", "sell"}
ADOPTED_STATUSES = {EXECUTION_STATUS_EXECUTED, EXECUTION_STATUS_PARTIAL}


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\ufeff", "").strip()


def _normalize_key(value: Any) -> str:
    return re.sub(r"[\s_\-（）()]+", "", _normalize_text(value).lower())


def _is_blank_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def parse_execution_date(value: str | date | datetime | None, *, field_name: str = "executed_date") -> date | None:
    """Parse an optional date value and raise ``ValueError`` with a user-facing message."""
    if _is_blank_value(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _normalize_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} 日期格式必须为 YYYY-MM-DD") from exc


def normalize_execution_status(value: str | None) -> str:
    """Normalize and validate user execution status."""
    raw = _normalize_text(value)
    normalized = raw.lower() or EXECUTION_STATUS_PLANNED
    normalized = EXECUTION_STATUS_ALIASES.get(raw, EXECUTION_STATUS_ALIASES.get(normalized, normalized))
    if normalized not in EXECUTION_STATUSES:
        allowed = ", ".join(sorted(EXECUTION_STATUSES))
        raise ValueError(f"execution_status 必须是以下之一: {allowed}")
    return normalized


def normalize_advice_action(value: str | None) -> str:
    """Normalize persisted advice action values."""
    raw = _normalize_text(value)
    normalized = raw.lower()
    return ACTION_ALIASES.get(raw, ACTION_ALIASES.get(normalized, "hold"))


def normalize_trade_intent(value: str | None, action: str | None = None) -> str:
    """Normalize user-facing fund trade intent."""
    raw = _normalize_text(value)
    normalized = raw.lower()
    if raw in TRADE_INTENT_ALIASES:
        return TRADE_INTENT_ALIASES[raw]
    if normalized in TRADE_INTENT_ALIASES:
        return TRADE_INTENT_ALIASES[normalized]
    action_norm = normalize_advice_action(action)
    if action_norm == "buy":
        return "subscribe"
    if action_norm == "sell":
        return "redeem"
    return "hold"


def _to_float(value: Any) -> float | None:
    if _is_blank_value(value):
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        value = value.replace(",", "").replace("¥", "").replace("￥", "").strip()
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def _iso_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _iso_date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def find_advice_snapshot(advices: list[dict[str, Any]] | None, fund_code: str) -> dict[str, Any] | None:
    """Find the saved advice payload for a fund code."""
    for advice in advices or []:
        if str(advice.get("fund_code")) == str(fund_code):
            return advice
    return None


def validate_execution_payload(
    *,
    execution_status: str,
    executed_date: date | None,
    not_executed_reason: str | None,
) -> None:
    """Validate cross-field requirements shared by manual and imported records."""
    if execution_status in {EXECUTION_STATUS_EXECUTED, EXECUTION_STATUS_PARTIAL} and executed_date is None:
        raise ValueError("executed/partial 状态必须填写 executed_date")
    if execution_status == EXECUTION_STATUS_NOT_EXECUTED and not _normalize_text(not_executed_reason):
        raise ValueError("not_executed 状态必须填写 not_executed_reason")


def build_execution_snapshot_from_advice(advice: dict[str, Any]) -> dict[str, Any]:
    """Extract immutable recommendation fields to store beside execution facts."""
    action = normalize_advice_action(advice.get("action"))
    trade_plan = advice.get("trade_plan") if isinstance(advice.get("trade_plan"), dict) else {}
    return {
        "advice_action": action,
        "trade_intent": normalize_trade_intent(advice.get("trade_intent"), action),
        "suggested_amount": _to_float(advice.get("suggested_amount") or trade_plan.get("suggested_amount")),
        "suggested_shares": _to_float(advice.get("suggested_shares")),
        "suggested_pct": _to_float(advice.get("suggested_pct")),
        "confidence": _to_float(advice.get("confidence")),
    }


EXECUTION_IMPORT_COLUMN_ALIASES = {
    "fund_code": {
        "fundcode",
        "code",
        "基金代码",
        "基金编号",
        "代码",
    },
    "execution_status": {
        "executionstatus",
        "status",
        "执行状态",
        "成交状态",
        "状态",
    },
    "advice_action": {
        "adviceaction",
        "action",
        "原建议动作",
        "建议动作",
        "动作",
    },
    "trade_intent": {
        "tradeintent",
        "intent",
        "交易意图",
        "交易方向",
        "申购赎回",
    },
    "executed_date": {
        "executeddate",
        "tradedate",
        "date",
        "成交日期",
        "执行日期",
        "交易日期",
        "日期",
    },
    "executed_amount": {
        "executedamount",
        "tradeamount",
        "amount",
        "成交金额",
        "执行金额",
        "交易金额",
        "金额",
    },
    "executed_shares": {
        "executedshares",
        "shares",
        "份额",
        "成交份额",
        "执行份额",
    },
    "executed_nav": {
        "executednav",
        "nav",
        "成交净值",
        "净值",
        "成交价格",
    },
    "executed_fee": {
        "executedfee",
        "fee",
        "费用",
        "手续费",
        "成交费用",
    },
    "execution_channel": {
        "executionchannel",
        "channel",
        "渠道",
        "执行渠道",
        "交易平台",
        "平台",
    },
    "not_executed_reason": {
        "notexecutedreason",
        "未执行原因",
        "未成交原因",
    },
    "deviation_reason": {
        "deviationreason",
        "偏离原因",
        "偏离建议原因",
        "差异原因",
    },
    "user_note": {
        "usernote",
        "note",
        "备注",
        "用户备注",
        "说明",
    },
}

_CANONICAL_IMPORT_HEADERS: dict[str, str] = {}
for _canonical, _aliases in EXECUTION_IMPORT_COLUMN_ALIASES.items():
    _CANONICAL_IMPORT_HEADERS[_normalize_key(_canonical)] = _canonical
    for _alias in _aliases:
        _CANONICAL_IMPORT_HEADERS[_normalize_key(_alias)] = _canonical


def canonicalize_execution_import_row(raw_row: dict[str, Any]) -> dict[str, Any]:
    """Map CSV/Excel row headers into canonical execution-record fields."""
    row: dict[str, Any] = {}
    extras: dict[str, Any] = {}
    for key, value in raw_row.items():
        if _is_blank_value(key):
            continue
        canonical = _CANONICAL_IMPORT_HEADERS.get(_normalize_key(key))
        if canonical:
            row[canonical] = None if _is_blank_value(value) else value
        elif not _is_blank_value(value):
            extras[_normalize_text(key)] = value
    if extras:
        row["extra_columns"] = extras
    return row


def parse_execution_import_file(filename: str, content: bytes) -> list[dict[str, Any]]:
    """Parse CSV/XLS/XLSX bytes into canonical row dictionaries."""
    suffix = (filename.rsplit(".", 1)[-1] if "." in filename else "csv").lower()
    if suffix == "csv":
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("导入文件缺少表头")
        return [canonicalize_execution_import_row(row) for row in reader]

    if suffix in {"xls", "xlsx"}:
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - dependency is declared at runtime
            raise ValueError("Excel 导入需要安装 pandas/openpyxl") from exc
        try:
            frame = pd.read_excel(io.BytesIO(content))
        except Exception as exc:  # noqa: BLE001 - convert parser errors into user-facing message
            raise ValueError(f"无法读取 Excel 文件: {exc}") from exc
        return [canonicalize_execution_import_row(row) for row in frame.to_dict(orient="records")]

    raise ValueError("仅支持 CSV、XLS、XLSX 文件")


def build_execution_record_from_import_row(
    *,
    advisor_result: Any,
    row: dict[str, Any],
    source_filename: str,
    row_number: int,
) -> AdvisorExecutionRecord:
    """Build a validated execution record from one imported row."""
    fund_code = _normalize_text(row.get("fund_code"))
    if not fund_code:
        raise ValueError("fund_code/基金代码 不能为空")
    advice_snapshot = find_advice_snapshot(advisor_result.advices, fund_code)
    if advice_snapshot is None:
        raise ValueError("该历史建议中未找到对应基金")

    execution_status = normalize_execution_status(row.get("execution_status"))
    executed_date = parse_execution_date(row.get("executed_date"))
    not_executed_reason = _normalize_text(row.get("not_executed_reason")) or None
    validate_execution_payload(
        execution_status=execution_status,
        executed_date=executed_date,
        not_executed_reason=not_executed_reason,
    )

    snapshot = build_execution_snapshot_from_advice(advice_snapshot)
    advice_action = normalize_advice_action(row.get("advice_action") or snapshot["advice_action"])
    trade_intent = normalize_trade_intent(row.get("trade_intent") or snapshot["trade_intent"], advice_action)
    extra_columns = row.get("extra_columns") if isinstance(row.get("extra_columns"), dict) else {}
    metadata = {
        "import_filename": source_filename,
        "import_row_number": row_number,
    }
    if extra_columns:
        metadata["extra_columns"] = extra_columns

    return AdvisorExecutionRecord(
        advisor_result_id=advisor_result.id,
        advice_date=advisor_result.advice_date,
        fund_code=fund_code,
        advice_action=advice_action,
        trade_intent=trade_intent,
        suggested_amount=snapshot["suggested_amount"],
        suggested_shares=snapshot["suggested_shares"],
        suggested_pct=snapshot["suggested_pct"],
        confidence=snapshot["confidence"],
        execution_status=execution_status,
        executed_date=executed_date,
        executed_amount=_to_float(row.get("executed_amount")),
        executed_shares=_to_float(row.get("executed_shares")),
        executed_nav=_to_float(row.get("executed_nav")),
        executed_fee=_to_float(row.get("executed_fee")),
        execution_channel=_normalize_text(row.get("execution_channel")) or None,
        not_executed_reason=not_executed_reason,
        deviation_reason=_normalize_text(row.get("deviation_reason")) or None,
        user_note=_normalize_text(row.get("user_note")) or None,
        source="import",
        metadata_json=metadata,
    )


def serialize_execution_record(record: AdvisorExecutionRecord) -> dict[str, Any]:
    """Serialize an execution record for API responses."""
    return {
        "id": record.id,
        "advisor_result_id": record.advisor_result_id,
        "advice_date": _iso_date(record.advice_date),
        "fund_code": record.fund_code,
        "advice_action": record.advice_action,
        "trade_intent": record.trade_intent,
        "suggested_amount": _to_float(record.suggested_amount),
        "suggested_shares": _to_float(record.suggested_shares),
        "suggested_pct": _to_float(record.suggested_pct),
        "confidence": _to_float(record.confidence),
        "execution_status": record.execution_status,
        "executed_date": _iso_date(record.executed_date),
        "executed_amount": _to_float(record.executed_amount),
        "executed_shares": _to_float(record.executed_shares),
        "executed_nav": _to_float(record.executed_nav),
        "executed_fee": _to_float(record.executed_fee),
        "execution_channel": record.execution_channel,
        "not_executed_reason": record.not_executed_reason,
        "deviation_reason": record.deviation_reason,
        "user_note": record.user_note,
        "source": record.source,
        "metadata": record.metadata_json,
        "created_at": _iso_datetime(record.created_at),
        "updated_at": _iso_datetime(record.updated_at),
    }


async def load_execution_records_for_result(
    session: Any,
    advisor_result_id: int,
) -> list[AdvisorExecutionRecord]:
    """Load execution records for a saved advisor result."""
    result = await session.execute(
        select(AdvisorExecutionRecord)
        .where(AdvisorExecutionRecord.advisor_result_id == advisor_result_id)
        .order_by(
            AdvisorExecutionRecord.fund_code.asc(),
            AdvisorExecutionRecord.executed_date.asc().nullslast(),
            AdvisorExecutionRecord.created_at.asc().nullslast(),
        )
    )
    return list(result.scalars().all())


async def load_execution_record_by_id(session: Any, execution_id: int) -> AdvisorExecutionRecord | None:
    """Load a single execution record by primary key."""
    result = await session.execute(
        select(AdvisorExecutionRecord).where(AdvisorExecutionRecord.id == execution_id)
    )
    return result.scalar_one_or_none()


def _expected_actionable_count(advices: list[dict[str, Any]] | None) -> int:
    return sum(1 for advice in advices or [] if normalize_advice_action(advice.get("action")) in ACTIONABLE_ACTIONS)


def _empty_fund_summary(fund_code: str) -> dict[str, Any]:
    return {
        "fund_code": fund_code,
        "record_count": 0,
        "statuses": [],
        "adopted": False,
        "latest_status": "no_record",
        "latest_executed_date": None,
        "total_executed_amount": 0.0,
        "total_executed_shares": 0.0,
        "suggested_amount": None,
        "suggested_shares": None,
        "amount_execution_ratio": None,
        "amount_deviation_pct": None,
        "drift_level": "unknown",
        "not_executed_reasons": [],
        "deviation_reasons": [],
    }


def summarize_execution_records(
    advices: list[dict[str, Any]] | None,
    records: list[AdvisorExecutionRecord],
) -> dict[str, Any]:
    """Build adoption and execution-drift summary for one advisor result."""
    actionable_count = _expected_actionable_count(advices)
    by_fund: dict[str, dict[str, Any]] = {}

    for advice in advices or []:
        fund_code = str(advice.get("fund_code") or "")
        if not fund_code:
            continue
        snapshot = build_execution_snapshot_from_advice(advice)
        fund_summary = _empty_fund_summary(fund_code)
        fund_summary["suggested_amount"] = snapshot["suggested_amount"]
        fund_summary["suggested_shares"] = snapshot["suggested_shares"]
        fund_summary["advice_action"] = snapshot["advice_action"]
        fund_summary["trade_intent"] = snapshot["trade_intent"]
        by_fund[fund_code] = fund_summary

    status_counts = {status: 0 for status in sorted(EXECUTION_STATUSES)}
    adopted_funds: set[str] = set()
    recorded_actionable_funds: set[str] = set()
    significant_deviation_count = 0
    abs_deviations: list[float] = []

    records_by_fund: dict[str, list[AdvisorExecutionRecord]] = defaultdict(list)
    for record in records:
        records_by_fund[record.fund_code].append(record)

    for fund_code, fund_records in records_by_fund.items():
        fund_summary = by_fund.setdefault(fund_code, _empty_fund_summary(fund_code))
        fund_records_sorted = sorted(
            fund_records,
            key=lambda item: (
                item.executed_date or date.min,
                item.created_at.timestamp() if item.created_at else 0.0,
                item.id,
            ),
        )
        latest = fund_records_sorted[-1]
        statuses = [record.execution_status for record in fund_records_sorted]
        adopted = any(status in ADOPTED_STATUSES for status in statuses)
        total_amount = sum(_to_float(record.executed_amount) or 0.0 for record in fund_records_sorted)
        total_shares = sum(_to_float(record.executed_shares) or 0.0 for record in fund_records_sorted)
        latest_executed_dates = [record.executed_date for record in fund_records_sorted if record.executed_date]
        suggested_amount = fund_summary.get("suggested_amount")
        suggested_shares = fund_summary.get("suggested_shares")
        if suggested_amount is None:
            suggested_amount = _to_float(latest.suggested_amount)
        if suggested_shares is None:
            suggested_shares = _to_float(latest.suggested_shares)

        ratio = None
        deviation_pct = None
        drift_level = "unknown"
        if suggested_amount and suggested_amount > 0:
            ratio = total_amount / suggested_amount
            deviation_pct = ratio - 1
            abs_deviation = abs(deviation_pct)
            abs_deviations.append(abs_deviation)
            if abs_deviation <= 0.1:
                drift_level = "aligned"
            elif abs_deviation <= 0.5:
                drift_level = "moderate_deviation"
            else:
                drift_level = "large_deviation"
                significant_deviation_count += 1
        elif adopted:
            drift_level = "adopted_without_amount"

        for status in statuses:
            if status in status_counts:
                status_counts[status] += 1
        if normalize_advice_action(latest.advice_action) in ACTIONABLE_ACTIONS:
            recorded_actionable_funds.add(fund_code)
        if adopted:
            adopted_funds.add(fund_code)

        fund_summary.update(
            {
                "record_count": len(fund_records_sorted),
                "statuses": statuses,
                "adopted": adopted,
                "latest_status": latest.execution_status,
                "latest_executed_date": max(latest_executed_dates).isoformat() if latest_executed_dates else None,
                "total_executed_amount": round(total_amount, 2),
                "total_executed_shares": round(total_shares, 4),
                "suggested_amount": suggested_amount,
                "suggested_shares": suggested_shares,
                "amount_execution_ratio": round(ratio, 4) if ratio is not None else None,
                "amount_deviation_pct": round(deviation_pct, 4) if deviation_pct is not None else None,
                "drift_level": drift_level,
                "not_executed_reasons": [
                    record.not_executed_reason
                    for record in fund_records_sorted
                    if record.not_executed_reason
                ],
                "deviation_reasons": [
                    record.deviation_reason
                    for record in fund_records_sorted
                    if record.deviation_reason
                ],
            }
        )

    adopted_count = len(adopted_funds)
    adoption_rate = round(adopted_count / actionable_count, 4) if actionable_count else None
    if actionable_count == 0:
        attribution_status = "no_actionable_advice"
    elif not records:
        attribution_status = "no_execution_records"
    elif adopted_count >= actionable_count:
        attribution_status = "fully_adopted"
    elif adopted_count > 0:
        attribution_status = "partially_adopted"
    elif recorded_actionable_funds:
        attribution_status = "not_adopted"
    else:
        attribution_status = "unmatched_records"

    return {
        "status": attribution_status,
        "actionable_advice_count": actionable_count,
        "record_count": len(records),
        "recorded_actionable_count": len(recorded_actionable_funds),
        "adopted_count": adopted_count,
        "adoption_rate": adoption_rate,
        "status_counts": status_counts,
        "avg_abs_amount_deviation_pct": (
            round(sum(abs_deviations) / len(abs_deviations), 4) if abs_deviations else None
        ),
        "significant_deviation_count": significant_deviation_count,
        "by_fund": by_fund,
        "interpretation": _build_execution_interpretation(
            attribution_status=attribution_status,
            adoption_rate=adoption_rate,
            significant_deviation_count=significant_deviation_count,
        ),
    }


def _build_execution_interpretation(
    *,
    attribution_status: str,
    adoption_rate: float | None,
    significant_deviation_count: int,
) -> str:
    if attribution_status == "no_actionable_advice":
        return "本次建议没有买入或卖出动作，主要用于观察。"
    if attribution_status == "no_execution_records":
        return "尚未记录用户是否执行，后续收益只能评价模型建议，不能判断实际采纳效果。"
    if attribution_status == "fully_adopted":
        base = "本次可执行建议均已记录为执行或部分执行，可用于采纳后复盘。"
    elif attribution_status == "partially_adopted":
        base = f"部分建议已执行，采纳率约 {adoption_rate:.0%}，复盘时需区分未执行建议。"
    elif attribution_status == "not_adopted":
        base = "用户记录显示可执行建议未被采纳，若后续表现偏离，不能简单归因于模型错误。"
    else:
        base = "执行记录与原建议匹配度不足，请检查基金代码和建议动作。"
    if significant_deviation_count > 0:
        base += f" 其中 {significant_deviation_count} 只基金实际金额与建议金额偏离较大。"
    return base


def _shift_iso_date(value: str | None, days: int) -> str:
    if not value:
        return ""
    try:
        return (date.fromisoformat(value) + timedelta(days=days)).isoformat()
    except ValueError:
        return value


def _trade_plan_trigger_label(trigger_type: str | None) -> str:
    mapping = {
        "pause_buy": "暂停加仓",
        "stop_buy": "停止买入",
        "reduce_position": "控制减仓",
        "review": "复核条件",
        "refresh": "刷新建议",
    }
    normalized = str(trigger_type or "")
    return mapping.get(normalized, normalized or "-")


def _build_execution_plan_tasks_for_advice(advice: dict[str, Any]) -> list[dict[str, Any]]:
    trade_plan = advice.get("trade_plan") if isinstance(advice.get("trade_plan"), dict) else None
    if not trade_plan:
        return []

    execution_type = str(trade_plan.get("execution_type") or "")
    if execution_type == "hold":
        return []

    fund_code = str(advice.get("fund_code") or "")
    advice_date = str(advice.get("advice_date") or "")
    action = normalize_advice_action(advice.get("action"))
    action_label = {"buy": "买入", "sell": "卖出", "hold": "持有", "watch": "观察"}.get(action, action or "-")
    anchor_date = (
        ((advice.get("trade_timing") or {}).get("accepted_trade_date") if isinstance(advice.get("trade_timing"), dict) else None)
        or ((advice.get("validity") or {}).get("data_as_of") if isinstance(advice.get("validity"), dict) else None)
        or advice_date
    )
    base_title = f"{fund_code} {action_label}".strip()
    triggers = trade_plan.get("triggers") if isinstance(trade_plan.get("triggers"), list) else []
    trigger_summary = None
    if triggers:
        trigger_parts = []
        for trigger in triggers[:2]:
            if not isinstance(trigger, dict):
                continue
            trigger_parts.append(
                f"{_trade_plan_trigger_label(trigger.get('trigger_type'))}：{str(trigger.get('condition') or '-') }"
            )
        trigger_summary = "；".join([part for part in trigger_parts if part]) or None

    suggested_amount = _to_float(advice.get("suggested_amount")) or 0.0
    amount_min = _to_float(advice.get("trade_amount_min"))
    if amount_min is None:
        amount_min = _to_float(trade_plan.get("min_amount"))
    if amount_min is None:
        amount_min = suggested_amount
    amount_max = _to_float(advice.get("trade_amount_max"))
    if amount_max is None:
        amount_max = _to_float(trade_plan.get("max_amount"))
    if amount_max is None:
        amount_max = suggested_amount

    if execution_type == "batch" and int(trade_plan.get("batch_count") or 0) > 1:
        batch_count = int(trade_plan.get("batch_count") or 0)
        interval_days = int(trade_plan.get("batch_interval_days") or 7)
        per_min = round(amount_min / batch_count, 2) if batch_count > 0 else amount_min
        per_max = round(amount_max / batch_count, 2) if batch_count > 0 else amount_max
        return [
            {
                "task_key": f"{fund_code}:{advice_date}:batch:{index}:{scheduled_date}",
                "title": f"{base_title}第 {index} 批",
                "scheduled_date": scheduled_date,
                "amount_min": per_min,
                "amount_max": per_max,
                "description": f"按 {interval_days} 天左右的节奏执行第 {index} / {batch_count} 批，并在执行前复核最新信号。",
                "trigger_summary": trigger_summary,
                "index": index,
                "execution_type": execution_type,
            }
            for index in range(1, batch_count + 1)
            for scheduled_date in [_shift_iso_date(anchor_date, interval_days * (index - 1)) or anchor_date]
        ]

    if execution_type == "fixed_investment":
        return [{
            "task_key": f"{fund_code}:{advice_date}:fixed:{anchor_date}",
            "title": f"{base_title}首期定投",
            "scheduled_date": anchor_date,
            "amount_min": amount_min,
            "amount_max": amount_max,
            "description": "先按当前金额区间启动首期定投，后续继续按固定节奏执行，并定期刷新建议。",
            "trigger_summary": trigger_summary,
            "index": 1,
            "execution_type": execution_type,
        }]

    return [{
        "task_key": f"{fund_code}:{advice_date}:once:{anchor_date}",
        "title": base_title,
        "scheduled_date": anchor_date,
        "amount_min": amount_min,
        "amount_max": amount_max,
        "description": (
            "建议按本次时点一次性执行，执行前确认时效与交易规则。"
            if execution_type == "one_time"
            else "建议按当前时点执行，并结合下方说明复核。"
        ),
        "trigger_summary": trigger_summary,
        "index": 1,
        "execution_type": execution_type,
    }]


def build_execution_plan_statuses(
    advices: list[dict[str, Any]] | None,
    records: list[AdvisorExecutionRecord],
) -> dict[str, Any]:
    records_by_task_key: dict[str, list[AdvisorExecutionRecord]] = defaultdict(list)
    for record in records:
        metadata = record.metadata_json if isinstance(record.metadata_json, dict) else {}
        task_key = metadata.get("execution_plan_task_key")
        if task_key:
            records_by_task_key[str(task_key)].append(record)

    by_fund: dict[str, dict[str, Any]] = {}
    total_count = 0
    pending_count = 0
    done_count = 0
    skipped_count = 0

    for advice in advices or []:
        fund_code = str(advice.get("fund_code") or "")
        if not fund_code:
            continue
        tasks = []
        for task in _build_execution_plan_tasks_for_advice(advice):
            total_count += 1
            matched_records = sorted(
                records_by_task_key.get(str(task["task_key"]), []),
                key=lambda item: (
                    item.executed_date or date.min,
                    item.created_at.timestamp() if item.created_at else 0.0,
                    item.id,
                ),
            )
            latest = matched_records[-1] if matched_records else None
            latest_status = latest.execution_status if latest is not None else "pending"
            derived_status = "pending"
            if latest_status in {EXECUTION_STATUS_EXECUTED, EXECUTION_STATUS_PARTIAL}:
                derived_status = "done"
                done_count += 1
            elif latest_status == EXECUTION_STATUS_NOT_EXECUTED:
                derived_status = "skipped"
                skipped_count += 1
            else:
                pending_count += 1
            tasks.append({
                **task,
                "status": derived_status,
                "matched_execution_id": latest.id if latest is not None else None,
                "matched_execution_status": latest.execution_status if latest is not None else None,
                "matched_executed_date": _iso_date(latest.executed_date) if latest is not None else None,
                "matched_record_count": len(matched_records),
            })
        by_fund[fund_code] = {
            "fund_code": fund_code,
            "tasks": tasks,
            "pending_count": sum(1 for task in tasks if task["status"] == "pending"),
            "done_count": sum(1 for task in tasks if task["status"] == "done"),
            "skipped_count": sum(1 for task in tasks if task["status"] == "skipped"),
        }

    return {
        "by_fund": by_fund,
        "summary": {
            "task_count": total_count,
            "pending_count": pending_count,
            "done_count": done_count,
            "skipped_count": skipped_count,
        },
    }


def attach_execution_attribution(
    tracked_returns: dict[str, dict[str, Any]] | None,
    advices: list[dict[str, Any]] | None,
    records: list[AdvisorExecutionRecord],
) -> dict[str, dict[str, Any]] | None:
    """Attach compact execution attribution to tracked-return payloads."""
    if tracked_returns is None:
        return None
    summary = summarize_execution_records(advices, records)
    by_fund = summary.get("by_fund", {})
    enriched: dict[str, dict[str, Any]] = {}
    for fund_code, data in tracked_returns.items():
        item = dict(data)
        fund_summary = by_fund.get(fund_code)
        if fund_summary is not None:
            item["execution_attribution"] = {
                "latest_status": fund_summary.get("latest_status"),
                "adopted": fund_summary.get("adopted"),
                "record_count": fund_summary.get("record_count"),
                "latest_executed_date": fund_summary.get("latest_executed_date"),
                "total_executed_amount": fund_summary.get("total_executed_amount"),
                "total_executed_shares": fund_summary.get("total_executed_shares"),
                "amount_execution_ratio": fund_summary.get("amount_execution_ratio"),
                "drift_level": fund_summary.get("drift_level"),
            }
        else:
            item["execution_attribution"] = {
                "latest_status": "no_record",
                "adopted": False,
                "record_count": 0,
            }
        enriched[fund_code] = item
    return enriched


__all__ = [
    "ADOPTED_STATUSES",
    "EXECUTION_STATUS_EXECUTED",
    "EXECUTION_STATUS_NOT_EXECUTED",
    "EXECUTION_STATUS_PARTIAL",
    "EXECUTION_STATUS_PLANNED",
    "EXECUTION_STATUSES",
    "attach_execution_attribution",
    "build_execution_plan_statuses",
    "build_execution_snapshot_from_advice",
    "find_advice_snapshot",
    "load_execution_record_by_id",
    "build_execution_record_from_import_row",
    "canonicalize_execution_import_row",
    "load_execution_records_for_result",
    "normalize_execution_status",
    "parse_execution_date",
    "parse_execution_import_file",
    "serialize_execution_record",
    "summarize_execution_records",
    "validate_execution_payload",
]
