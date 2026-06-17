"""组合检查 API 端点（v5 智能增强版）。

提供：
- ``POST /advisor/analyze``       — 为指定基金生成组合检查结果
- ``POST /advisor/portfolio``     — 为整个组合生成调仓参考
- ``POST /advisor/save``          — 保存检查结果
- ``GET /advisor/history``        — 查询历史检查记录
- ``GET /advisor/history/{id}``   — 获取单条历史检查详情
- ``DELETE /advisor/history/{id}`` — 删除历史检查记录
- ``GET /advisor/signals``        — 查询历史信号记录
- ``GET /advisor/config``         — 获取检查引擎默认配置

v5 改进：
- 自适应权重重分配：信号源不可用时自动调整权重
- 动态阈值：根据可用信号源数量调整关注阈值
- Bootstrap 预测恢复参与辅助评分
- 非线性信号共识加成
- Regime 调整温和化
- 技术分析对所有基金类型启用（非 ETF 降权但不归零）
- 调整基础关注阈值，让检查结论更有区分度
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.advisor_results import AdvisorResult
from app.data.providers.snapshot import SnapshotArchive
from app.data.session import get_session
from app.services.advisor_execution import (
    AdvisorExecutionRequest,
    build_result_execution_context,
    execute_advisor_request,
)
from app.services.advisor_profiles import RISK_PROFILES, build_advisor_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/advisor", tags=["advisor"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class PositionDetailPayload(BaseModel):
    """持仓详情输入。

    新字段：
    - market_value: 当前持仓市值
    - shares: 当前持有份额
    - cost_basis: 持仓成本金额

    兼容旧字段：
    - amount -> shares
    - cost -> cost_basis
    """

    market_value: float | None = Field(None, ge=0, description="当前持仓市值（元）")
    shares: float | None = Field(None, ge=0, description="当前持有份额")
    cost_basis: float | None = Field(None, ge=0, description="持仓成本金额（元）")
    buy_date: str | None = Field(None, description="买入日期")

    # 兼容旧字段
    amount: float | None = Field(None, ge=0, description="兼容旧字段：持仓份额")
    cost: float | None = Field(None, ge=0, description="兼容旧字段：持仓成本金额")

    @model_validator(mode="after")
    def _normalize_legacy_fields(self) -> "PositionDetailPayload":
        if self.shares is None and self.amount is not None:
            self.shares = self.amount
        if self.cost_basis is None and self.cost is not None:
            self.cost_basis = self.cost
        return self

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "market_value": self.market_value,
            "shares": self.shares,
            "cost_basis": self.cost_basis,
            "buy_date": self.buy_date,
            # 兼容旧逻辑
            "amount": self.shares,
            "cost": self.cost_basis,
        }


class AdvisorAnalyzeRequest(BaseModel):
    """组合检查分析请求。"""

    fund_codes: list[str] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="基金代码列表（最多20只）",
    )
    total_capital: float = Field(
        default=100000.0,
        gt=0,
        description="总可用资金（元）",
    )
    current_positions: dict[str, float] = Field(
        default_factory=dict,
        description="当前持仓 {fund_code: market_value}",
    )
    positions_detail: dict[str, PositionDetailPayload] = Field(
        default_factory=dict,
        description="持仓详情 {fund_code: {market_value, shares, cost_basis, buy_date}}",
    )
    # 风险偏好
    risk_level: str = Field(
        default="moderate",
        description="风险偏好: conservative/moderate/aggressive",
    )
    investment_goal: str | None = Field(
        None,
        description="投资目标: cash_management/stable_growth/balanced/long_term_growth",
    )
    investment_horizon: str | None = Field(
        None,
        description="投资期限: within_3_months/3_to_12_months/1_to_3_years/over_3_years",
    )
    liquidity_need: str | None = Field(
        None,
        description="流动性需求: high/medium/low",
    )
    max_drawdown_tolerance: float | None = Field(
        None,
        ge=0,
        le=1,
        description="最大可接受回撤，例如 0.08 表示 8%",
    )
    monthly_invest_amount: float | None = Field(None, ge=0, description="每月可投资金额（元）")
    industry_concentration_tolerance: str | None = Field(None, description="行业/单主题集中容忍度: low/medium/high")
    qdii_fx_risk_tolerance: str | None = Field(None, description="QDII 汇率风险接受度: low/medium/high")
    fee_sensitivity: str | None = Field(None, description="费率敏感度: low/medium/high")
    compare_risk_levels: bool = Field(default=False, description="是否同时返回三档风险偏好的对比结果")


class PortfolioAdviceRequest(BaseModel):
    """组合调仓参考请求。"""

    strategy_id: int = Field(..., description="策略 ID")
    total_capital: float = Field(default=100000.0, gt=0, description="总可用资金")
    current_positions: dict[str, float] = Field(
        default_factory=dict,
        description="当前持仓 {fund_code: market_value}",
    )
    positions_detail: dict[str, PositionDetailPayload] = Field(
        default_factory=dict,
        description="持仓详情 {fund_code: {market_value, shares, cost_basis, buy_date}}",
    )
    risk_level: str = Field(default="moderate", description="风险偏好")
    investment_goal: str | None = Field(
        None,
        description="投资目标: cash_management/stable_growth/balanced/long_term_growth",
    )
    investment_horizon: str | None = Field(
        None,
        description="投资期限: within_3_months/3_to_12_months/1_to_3_years/over_3_years",
    )
    liquidity_need: str | None = Field(
        None,
        description="流动性需求: high/medium/low",
    )
    max_drawdown_tolerance: float | None = Field(
        None,
        ge=0,
        le=1,
        description="最大可接受回撤，例如 0.08 表示 8%",
    )
    monthly_invest_amount: float | None = Field(None, ge=0, description="每月可投资金额（元）")
    industry_concentration_tolerance: str | None = Field(None, description="行业/单主题集中容忍度: low/medium/high")
    qdii_fx_risk_tolerance: str | None = Field(None, description="QDII 汇率风险接受度: low/medium/high")
    fee_sensitivity: str | None = Field(None, description="费率敏感度: low/medium/high")
    compare_risk_levels: bool = Field(default=False, description="是否同时返回三档风险偏好的对比结果")


class SignalQueryParams(BaseModel):
    """信号查询参数。"""

    fund_code: str | None = None
    strategy_id: int | None = None
    direction: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    page: int = 1
    page_size: int = 20


class ParameterSetReviewRequest(BaseModel):
    """参数集人工审核请求。"""

    review_status: str = Field(..., description="审核状态: pending/approved/rejected")
    reviewed_by: str | None = Field(None, description="审核人")
    review_notes: str | None = Field(None, description="审核备注")


class ParameterSetActivateRequest(BaseModel):
    """参数集激活请求。"""

    reason: str | None = Field(None, description="激活原因")
    effective_from: str | None = Field(None, description="生效日期 YYYY-MM-DD")


class ParameterSetRollbackRequest(BaseModel):
    """参数集回滚请求。"""

    risk_level: str = Field(default="moderate", description="风险档")
    target_param_set_id: str | None = Field(None, description="目标参数集 ID；为空则回滚到最近可用版本")
    reason: str | None = Field(None, description="回滚原因")


class RegisterDefaultParameterSetRequest(BaseModel):
    """注册当前内置默认参数集请求。"""

    risk_level: str = Field(default="moderate", description="风险档")
    name: str | None = Field(None, description="参数集名称")
    description: str | None = Field(None, description="参数集说明")
    created_reason: str | None = Field(None, description="创建原因")
    review_status: str | None = Field(None, description="可选初始审核状态")
    release_status: str | None = Field(None, description="可选初始发布状态")
    fund_codes: list[str] | None = Field(None, description="用于发布门禁的基金池；为空则读取活跃策略基金池")
    evaluate_gate: bool = Field(default=True, description="是否执行 OOS/PBO 发布门禁")


class AdvisorExecutionRecordRequest(BaseModel):
    """用户实际执行记录请求。"""

    model_config = ConfigDict(extra="forbid")

    fund_code: str = Field(..., description="基金代码")
    execution_status: str = Field(
        default="executed",
        description="执行状态: planned/executed/partial/not_executed",
    )
    advice_action: str | None = Field(None, description="原建议动作；不传则从历史建议中读取")
    trade_intent: str | None = Field(None, description="交易意图: subscribe/redeem/hold；不传则按建议动作推断")
    executed_date: str | None = Field(None, description="实际成交日期 YYYY-MM-DD")
    executed_amount: float | None = Field(None, ge=0, description="实际成交金额")
    executed_shares: float | None = Field(None, ge=0, description="实际成交份额")
    executed_nav: float | None = Field(None, ge=0, description="实际成交净值")
    executed_fee: float | None = Field(None, ge=0, description="实际成交费用")
    execution_channel: str | None = Field(None, description="执行渠道/平台")
    not_executed_reason: str | None = Field(None, description="未执行原因")
    deviation_reason: str | None = Field(None, description="偏离建议金额/份额的原因")
    user_note: str | None = Field(None, description="用户备注")
    source: str = Field(default="manual", description="记录来源: manual/import/api")
    metadata: dict[str, Any] | None = Field(None, description="扩展元数据")


class AdvisorExecutionRecordUpdateRequest(BaseModel):
    """用户实际执行记录更新请求。"""

    model_config = ConfigDict(extra="forbid")

    execution_status: str | None = Field(None, description="执行状态: planned/executed/partial/not_executed")
    executed_date: str | None = Field(None, description="实际成交日期 YYYY-MM-DD")
    executed_amount: float | None = Field(None, ge=0, description="实际成交金额")
    executed_shares: float | None = Field(None, ge=0, description="实际成交份额")
    executed_nav: float | None = Field(None, ge=0, description="实际成交净值")
    executed_fee: float | None = Field(None, ge=0, description="实际成交费用")
    execution_channel: str | None = Field(None, description="执行渠道/平台")
    not_executed_reason: str | None = Field(None, description="未执行原因")
    deviation_reason: str | None = Field(None, description="偏离建议金额/份额的原因")
    user_note: str | None = Field(None, description="用户备注")
    metadata: dict[str, Any] | None = Field(None, description="扩展元数据")


class AdvisorReminderItem(BaseModel):
    """Advisor 历史建议提醒项。"""

    id: int = Field(..., description="提醒 ID")
    advisor_result_id: int = Field(..., description="关联建议记录 ID")
    fund_code: str | None = Field(default=None, description="关联基金代码；为空表示整条建议级提醒")
    category: str = Field(..., description="提醒分类: validity/risk/execution/plan/system")
    reminder_type: str = Field(..., description="提醒类型键")
    severity: str = Field(..., description="严重级别: info/warning/error/success")
    status: str = Field(..., description="提醒状态: active/resolved/dismissed")
    title: str = Field(..., description="提醒标题")
    description: str = Field(..., description="提醒描述")
    payload: dict[str, Any] | None = Field(default=None, description="结构化上下文")
    trigger_date: str | None = Field(default=None, description="触发日期 YYYY-MM-DD")
    resolved_at: str | None = Field(default=None, description="自动解决时间")
    dismissed_at: str | None = Field(default=None, description="手动忽略时间")
    created_at: str | None = Field(default=None, description="创建时间")
    updated_at: str | None = Field(default=None, description="更新时间")


class AdvisorReminderListResponse(BaseModel):
    """Advisor 提醒分页响应。"""

    items: list[AdvisorReminderItem] = Field(default_factory=list, description="提醒列表")
    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")
    pages: int = Field(..., description="总页数")


class AdvisorReminderUpdateRequest(BaseModel):
    """更新 Advisor 提醒状态请求。"""

    status: str = Field(..., description="目标状态: active/resolved/dismissed")


class AdvisorReminderPreferenceRequest(BaseModel):
    """Advisor 提醒订阅偏好。"""

    enabled: bool = Field(default=True, description="是否启用服务端主动提醒摘要")
    min_severity: str = Field(default="warning", description="最低推送级别: info/warning/error/success")
    lookahead_days: int = Field(default=3, ge=0, le=30, description="纳入未来多少天内到期提醒")
    channels: list[str] | None = Field(default=None, description="通知通道: email/wecom/telegram；为空表示使用环境默认")
    muted_categories: list[str] = Field(default_factory=list, description="不纳入主动摘要的提醒分类")
    quiet_hours: dict[str, Any] | None = Field(default=None, description="可选免打扰时段配置")


class AdvisorHoldingImportPosition(BaseModel):
    """持仓导入后的标准化持仓行。"""

    fund_code: str = Field(..., description="基金代码")
    market_value: float = Field(default=0, ge=0, description="当前市值（元）")
    shares: float = Field(default=0, ge=0, description="持有份额")
    cost_basis: float = Field(default=0, ge=0, description="持仓成本（元）")
    buy_date: str | None = Field(default=None, description="买入日期 YYYY-MM-DD")


class AdvisorHoldingImportRowResult(BaseModel):
    """持仓导入逐行处理结果。"""

    row_number: int = Field(..., description="原始文件行号（从 1 开始）")
    status: str = Field(..., description="created/failed")
    fund_code: str | None = Field(default=None, description="基金代码")
    error: str | None = Field(default=None, description="失败原因")


class AdvisorPositionImportGovernanceSummary(BaseModel):
    """持仓导入治理诊断摘要。"""

    position_count: int = Field(..., description="去重后的持仓数量")
    imported_row_count: int = Field(..., description="成功解析的原始持仓行数")
    total_market_value: float = Field(..., description="导入持仓总市值")
    total_cost_basis: float = Field(..., description="导入持仓总成本")
    duplicate_fund_codes: list[str] = Field(default_factory=list, description="重复出现的基金代码；当前按最后一行覆盖")
    zero_value_fund_codes: list[str] = Field(default_factory=list, description="市值/份额/成本均为 0 的基金代码")
    suspicious_cost_fund_codes: list[str] = Field(default_factory=list, description="成本与市值比例明显异常的基金代码")
    warnings: list[str] = Field(default_factory=list, description="导入治理提示")


class AdvisorHoldingImportResponse(BaseModel):
    """持仓导入响应。"""

    status: str = Field(..., description="completed/partial")
    filename: str = Field(..., description="原始文件名")
    total_rows: int = Field(..., description="总数据行数")
    imported_count: int = Field(..., description="成功导入的持仓行数")
    failed_count: int = Field(..., description="失败行数")
    positions: list[AdvisorHoldingImportPosition] = Field(default_factory=list, description="标准化后的持仓列表")
    rows: list[AdvisorHoldingImportRowResult] = Field(default_factory=list, description="逐行处理结果")
    governance_summary: AdvisorPositionImportGovernanceSummary = Field(..., description="导入治理诊断摘要")


class AdvisorPositionsReplaceRequest(BaseModel):
    """替换当前持仓快照请求。"""

    positions: list[AdvisorHoldingImportPosition] = Field(default_factory=list, description="完整持仓列表")


class AdvisorPositionImportHistoryItem(BaseModel):
    """持仓导入历史项。"""

    id: int = Field(..., description="导入记录 ID")
    filename: str = Field(..., description="原始文件名")
    file_format: str = Field(..., description="文件格式")
    status: str = Field(..., description="completed/partial/failed")
    total_rows: int = Field(..., description="总数据行数")
    imported_count: int = Field(..., description="成功导入行数")
    failed_count: int = Field(..., description="失败行数")
    replaced_position_count: int = Field(..., description="导入后当前持仓数")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="逐行结果")
    positions: list[dict[str, Any]] = Field(default_factory=list, description="成功导入持仓快照")
    metadata: dict[str, Any] | None = Field(default=None, description="扩展元数据")
    created_at: str | None = Field(default=None, description="创建时间")


class AdvisorPositionImportHistoryResponse(BaseModel):
    """持仓导入历史分页响应。"""

    items: list[AdvisorPositionImportHistoryItem] = Field(default_factory=list, description="导入历史列表")
    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")
    pages: int = Field(..., description="总页数")


class AdvisorPositionImportRestoreResponse(BaseModel):
    """从导入历史恢复持仓响应。"""

    status: str = Field(..., description="restored")
    total: int = Field(..., description="恢复后的当前持仓数")
    positions: list[AdvisorHoldingImportPosition] = Field(default_factory=list, description="恢复后的当前持仓")
    restored_from: AdvisorPositionImportHistoryItem = Field(..., description="恢复来源导入历史")


class SnapshotVersionResponse(BaseModel):
    """原始响应快照版本元数据。"""

    version_id: str = Field(..., description="不可变快照版本 ID")
    provider: str = Field(..., description="数据提供方")
    fund_code: str = Field(..., description="基金代码")
    endpoint: str = Field(..., description="逻辑端点，如 nav_history/fund_meta")
    ext: str = Field(..., description="原始扩展名")
    snapshot_date: str = Field(..., description="快照日期 YYYY-MM-DD")
    captured_at: str | None = Field(default=None, description="捕获时间 ISO")
    sha256: str = Field(..., description="原始内容 SHA256")
    size_bytes: int = Field(..., description="压缩文件大小（字节）")


class SnapshotVersionListResponse(BaseModel):
    """快照版本检索响应。"""

    items: list[SnapshotVersionResponse] = Field(default_factory=list, description="版本列表")
    total: int = Field(..., description="返回数量")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _iso_datetime(value: datetime | None) -> str | None:
    """将 datetime 序列化为 ISO 字符串。"""
    return value.isoformat() if value else None


def _snapshot_version_to_dict(version: Any) -> dict[str, Any]:
    """序列化 SnapshotVersion 为 API 友好的字典。"""
    return {
        "version_id": str(version.version_id),
        "provider": str(version.provider),
        "fund_code": str(version.fund_code),
        "endpoint": str(version.endpoint),
        "ext": str(version.ext),
        "snapshot_date": version.snapshot_date.isoformat() if hasattr(version.snapshot_date, "isoformat") else str(version.snapshot_date),
        "captured_at": _iso_datetime(getattr(version, "captured_at", None)),
        "sha256": str(version.sha256),
        "size_bytes": int(version.size_bytes),
    }


def _snapshot_media_type(ext: str) -> str:
    """根据扩展名推断下载时的媒体类型。"""
    normalized = str(ext or "").lower()
    if normalized == "json":
        return "application/json; charset=utf-8"
    if normalized == "html":
        return "text/html; charset=utf-8"
    if normalized == "xml":
        return "application/xml; charset=utf-8"
    if normalized == "csv":
        return "text/csv; charset=utf-8"
    if normalized == "js":
        return "application/javascript; charset=utf-8"
    return "text/plain; charset=utf-8"


async def _refresh_advisor_user_learning(
    session: AsyncSession,
    profile_key: str | None = None,
) -> dict[str, Any] | None:
    """执行记录变更后自动刷新用户级学习画像。"""
    try:
        from app.services.advisor_user_learning import AdvisorUserLearningService

        snapshot = await AdvisorUserLearningService.learn_and_persist(
            session,
            profile_key=profile_key,
        )
        return snapshot.to_dict()
    except Exception:
        logger.exception("advisor.user_learning.refresh_failed")
        return None


async def _sync_advisor_result_reminders(
    session: AsyncSession,
    row: Any,
) -> dict[str, int] | None:
    """按最新执行记录与计划状态重新同步单条建议的提醒。"""
    from app.services.advisor_execution_records import (
        build_execution_plan_statuses,
        load_execution_records_for_result,
        summarize_execution_records,
    )
    from app.services.advisor_reminders import sync_advisor_reminders_for_result

    try:
        result_id = int(getattr(row, "id"))
        records = await load_execution_records_for_result(session, result_id)
        execution_summary = summarize_execution_records(getattr(row, "advices", None), records)
        execution_plan_status = build_execution_plan_statuses(getattr(row, "advices", None), records)
        return await sync_advisor_reminders_for_result(
            session,
            row,
            execution_summary=execution_summary,
            execution_plan_status=execution_plan_status,
        )
    except Exception:
        logger.exception("advisor.reminders.sync_failed", extra={"advisor_result_id": getattr(row, "id", None)})
        return None



def _serialize_holding_position(position: Any) -> dict[str, Any]:
    buy_date = getattr(position, "buy_date", None)
    return {
        "fund_code": str(getattr(position, "fund_code", "") or ""),
        "market_value": float(getattr(position, "market_value", 0) or 0),
        "shares": float(getattr(position, "shares", 0) or 0),
        "cost_basis": float(getattr(position, "cost_basis", 0) or 0),
        "buy_date": buy_date.isoformat() if hasattr(buy_date, "isoformat") and buy_date else buy_date,
    }



def _serialize_position_import_record(record: Any) -> dict[str, Any]:
    return {
        "id": int(getattr(record, "id")),
        "filename": str(getattr(record, "filename", "") or ""),
        "file_format": str(getattr(record, "file_format", "") or ""),
        "status": str(getattr(record, "status", "") or ""),
        "total_rows": int(getattr(record, "total_rows", 0) or 0),
        "imported_count": int(getattr(record, "imported_count", 0) or 0),
        "failed_count": int(getattr(record, "failed_count", 0) or 0),
        "replaced_position_count": int(getattr(record, "replaced_position_count", 0) or 0),
        "rows": list(getattr(record, "rows_json", None) or []),
        "positions": list(getattr(record, "positions_json", None) or []),
        "metadata": getattr(record, "metadata_json", None),
        "created_at": _iso_datetime(getattr(record, "created_at", None)),
    }



def _normalize_import_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def _parse_holding_number(value: Any) -> float:
    text_value = _normalize_import_text(value).replace(",", "")
    if not text_value:
        return 0.0
    try:
        return float(text_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"无法识别数值: {value}") from exc


def _parse_holding_buy_date(value: Any) -> str | None:
    text_value = _normalize_import_text(value)
    if not text_value:
        return None
    normalized = text_value.replace("/", "-")
    try:
        return date.fromisoformat(normalized[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(f"买入日期格式无效: {value}") from exc


def _canonicalize_holding_import_row(row: dict[str, Any]) -> dict[str, Any]:
    aliases = {
        "fund_code": {"fund_code", "code", "基金代码", "基金", "代码"},
        "market_value": {"market_value", "amount", "当前市值", "市值", "持仓市值"},
        "shares": {"shares", "份额", "持有份额", "持仓份额"},
        "cost_basis": {"cost_basis", "cost", "持仓成本", "成本"},
        "buy_date": {"buy_date", "买入日期", "建仓日期", "持仓日期"},
    }
    normalized_by_header = {_normalize_import_text(k): v for k, v in row.items()}
    result: dict[str, Any] = {}
    for target, names in aliases.items():
        value = ""
        for header, raw_value in normalized_by_header.items():
            if header in names:
                value = raw_value
                break
        result[target] = value
    return result


def _normalize_positions_payload(
    positions: list[AdvisorHoldingImportPosition],
) -> list[AdvisorHoldingImportPosition]:
    normalized_by_code: dict[str, AdvisorHoldingImportPosition] = {}
    for item in positions:
        fund_code = _normalize_import_text(item.fund_code)
        if not fund_code:
            continue
        normalized_by_code[fund_code] = AdvisorHoldingImportPosition(
            fund_code=fund_code,
            market_value=float(item.market_value or 0),
            shares=float(item.shares or 0),
            cost_basis=float(item.cost_basis or 0),
            buy_date=item.buy_date or None,
        )
    return [normalized_by_code[code] for code in sorted(normalized_by_code)]


def _build_position_import_governance_summary(
    positions: list[AdvisorHoldingImportPosition],
) -> AdvisorPositionImportGovernanceSummary:
    """生成持仓导入治理诊断，提示重复、零值和成本异常。"""
    seen: set[str] = set()
    duplicate_codes: set[str] = set()
    for item in positions:
        code = _normalize_import_text(item.fund_code)
        if not code:
            continue
        if code in seen:
            duplicate_codes.add(code)
        seen.add(code)

    normalized_positions = _normalize_positions_payload(positions)
    zero_value_codes: list[str] = []
    suspicious_cost_codes: list[str] = []
    total_market_value = 0.0
    total_cost_basis = 0.0

    for item in normalized_positions:
        market_value = float(item.market_value or 0)
        shares = float(item.shares or 0)
        cost_basis = float(item.cost_basis or 0)
        total_market_value += market_value
        total_cost_basis += cost_basis
        if market_value <= 0 and shares <= 0 and cost_basis <= 0:
            zero_value_codes.append(item.fund_code)
        if market_value > 0 and cost_basis > 0:
            cost_ratio = cost_basis / market_value
            if cost_ratio < 0.2 or cost_ratio > 5:
                suspicious_cost_codes.append(item.fund_code)

    warnings: list[str] = []
    if duplicate_codes:
        warnings.append("存在重复基金行，系统已按同基金最后一行作为当前持仓快照")
    if zero_value_codes:
        warnings.append("存在市值、份额和成本均为 0 的持仓行，建议确认是否误导入")
    if suspicious_cost_codes:
        warnings.append("存在成本/市值比例明显异常的持仓，建议核对持仓成本或当前市值")
    if not normalized_positions:
        warnings.append("本次导入没有可保存的有效持仓")

    return AdvisorPositionImportGovernanceSummary(
        position_count=len(normalized_positions),
        imported_row_count=len(positions),
        total_market_value=round(total_market_value, 2),
        total_cost_basis=round(total_cost_basis, 2),
        duplicate_fund_codes=sorted(duplicate_codes),
        zero_value_fund_codes=sorted(zero_value_codes),
        suspicious_cost_fund_codes=sorted(suspicious_cost_codes),
        warnings=warnings,
    )



async def _load_persisted_advisor_positions(session: AsyncSession) -> list[Any]:
    from app.data.models.advisor_positions import AdvisorPosition

    try:
        result = await session.execute(
            select(AdvisorPosition).order_by(AdvisorPosition.fund_code.asc(), AdvisorPosition.id.asc())
        )
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("advisor.positions.load_failed")
        return []
    return list(result.scalars().all())



async def _count_advisor_position_import_history(session: AsyncSession) -> int:
    from app.data.models.advisor_position_imports import AdvisorPositionImport

    try:
        result = await session.execute(
            select(AdvisorPositionImport.id)
        )
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("advisor.position_import_history.count_failed")
        return 0
    return len(list(result.scalars().all()))


async def _load_advisor_position_import_history(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
) -> list[Any]:
    from app.data.models.advisor_position_imports import AdvisorPositionImport

    safe_page = max(1, int(page or 1))
    safe_page_size = max(1, int(page_size or 20))
    offset = (safe_page - 1) * safe_page_size
    try:
        result = await session.execute(
            select(AdvisorPositionImport)
            .order_by(AdvisorPositionImport.created_at.desc().nullslast(), AdvisorPositionImport.id.desc())
            .offset(offset)
            .limit(safe_page_size)
        )
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("advisor.position_import_history.load_failed")
        return []
    return list(result.scalars().all())


async def _get_advisor_position_import_record(
    session: AsyncSession,
    import_id: int,
) -> Any | None:
    from app.data.models.advisor_position_imports import AdvisorPositionImport

    try:
        result = await session.execute(
            select(AdvisorPositionImport).where(AdvisorPositionImport.id == import_id)
        )
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("advisor.position_import_history.get_failed", extra={"import_id": import_id})
        return None
    return result.scalar_one_or_none()



async def _replace_persisted_advisor_positions(
    session: AsyncSession,
    positions: list[AdvisorHoldingImportPosition],
    *,
    source: str,
    metadata: dict[str, Any] | None = None,
) -> list[Any] | None:
    from app.data.models.advisor_positions import AdvisorPosition

    normalized_positions = _normalize_positions_payload(positions)
    try:
        await session.execute(delete(AdvisorPosition))
        created: list[AdvisorPosition] = []
        for item in normalized_positions:
            record = AdvisorPosition(
                fund_code=item.fund_code,
                market_value=item.market_value,
                shares=item.shares,
                cost_basis=item.cost_basis,
                buy_date=date.fromisoformat(item.buy_date) if item.buy_date else None,
                source=source,
                metadata_json=metadata,
            )
            session.add(record)
            created.append(record)
        await session.commit()
        for record in created:
            await session.refresh(record)
        return created
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("advisor.positions.replace_failed", extra={"source": source})
        return None



async def _create_advisor_position_import_history(
    session: AsyncSession,
    *,
    filename: str,
    file_format: str,
    status: str,
    total_rows: int,
    imported_count: int,
    failed_count: int,
    replaced_position_count: int,
    rows: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> Any:
    from app.data.models.advisor_position_imports import AdvisorPositionImport

    record = AdvisorPositionImport(
        filename=filename,
        file_format=file_format,
        status=status,
        total_rows=total_rows,
        imported_count=imported_count,
        failed_count=failed_count,
        replaced_position_count=replaced_position_count,
        rows_json=rows,
        positions_json=positions,
        metadata_json=metadata,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record



def _parse_holding_import_file(filename: str, content: bytes) -> list[dict[str, Any]]:
    suffix = (filename.rsplit(".", 1)[-1] if "." in filename else "csv").lower()
    if suffix == "csv":
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("导入文件缺少表头")
        return [_canonicalize_holding_import_row(row) for row in reader]
    if suffix in {"xls", "xlsx"}:
        try:
            import pandas as pd
        except ImportError as exc:
            raise ValueError("Excel 导入需要安装 pandas/openpyxl") from exc
        try:
            frame = pd.read_excel(io.BytesIO(content))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"无法读取 Excel 文件: {exc}") from exc
        return [_canonicalize_holding_import_row(row) for row in frame.to_dict(orient="records")]
    raise ValueError("仅支持 CSV、XLS、XLSX 持仓导入")



def _build_holding_import_response(filename: str, rows: list[dict[str, Any]]) -> AdvisorHoldingImportResponse:
    positions: list[AdvisorHoldingImportPosition] = []
    results: list[AdvisorHoldingImportRowResult] = []

    for idx, row in enumerate(rows, start=2):
        fund_code = _normalize_import_text(row.get("fund_code"))
        if not fund_code and not any(_normalize_import_text(row.get(field)) for field in ("market_value", "shares", "cost_basis", "buy_date")):
            continue
        try:
            if not fund_code:
                raise ValueError("缺少基金代码")
            positions.append(
                AdvisorHoldingImportPosition(
                    fund_code=fund_code,
                    market_value=_parse_holding_number(row.get("market_value")),
                    shares=_parse_holding_number(row.get("shares")),
                    cost_basis=_parse_holding_number(row.get("cost_basis")),
                    buy_date=_parse_holding_buy_date(row.get("buy_date")),
                )
            )
            results.append(AdvisorHoldingImportRowResult(row_number=idx, status="created", fund_code=fund_code))
        except ValueError as exc:
            results.append(AdvisorHoldingImportRowResult(row_number=idx, status="failed", fund_code=fund_code or None, error=str(exc)))

    failed_count = sum(1 for item in results if item.status == "failed")
    governance_summary = _build_position_import_governance_summary(positions)
    return AdvisorHoldingImportResponse(
        status="partial" if failed_count else "completed",
        filename=filename,
        total_rows=len(results),
        imported_count=len(positions),
        failed_count=failed_count,
        positions=positions,
        rows=results,
        governance_summary=governance_summary,
    )



def _build_holding_template_csv_bytes() -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["基金代码", "当前市值", "持有份额", "持仓成本", "买入日期"])
    writer.writerow(["000001", "12000", "9800", "11000", "2026-05-20"])
    writer.writerow(["000003", "5000", "3000", "4500", "2026-05-22"])
    return output.getvalue().encode("utf-8-sig")



def _build_holding_template_xlsx_bytes() -> bytes:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ValueError("生成 Excel 模板需要安装 pandas/openpyxl") from exc

    frame = pd.DataFrame([
        {"基金代码": "000001", "当前市值": 12000, "持有份额": 9800, "持仓成本": 11000, "买入日期": "2026-05-20"},
        {"基金代码": "000003", "当前市值": 5000, "持有份额": 3000, "持仓成本": 4500, "买入日期": "2026-05-22"},
    ])
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="持仓模板")
    return output.getvalue()


async def _get_advisor_result_or_404(result_id: int, session: AsyncSession) -> Any:
    """读取历史建议记录，不存在则抛 404。"""
    from sqlalchemy import select

    from app.data.models.advisor_results import AdvisorResult

    result = await session.execute(
        select(AdvisorResult).where(AdvisorResult.id == result_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="建议记录不存在")
    return row


def _get_nightly_oos_refresh_config() -> dict[str, Any]:
    """读取 nightly OOS 刷新任务配置，缺失时回退到安全默认值。"""
    defaults = {
        "risk_level": "moderate",
        "lookback_days": None,
        "n_folds": 5,
        "rebalance_freq": 5,
        "max_funds": 50,
        "max_age_days": 1,
        "dispatch_every_n": 10,
        "dispatch_countdown_step": 30,
        "schedule": "daily 21:40 Asia/Shanghai",
    }
    try:
        from app.tasks.schedule import BEAT_SCHEDULE

        entry = BEAT_SCHEDULE.get("daily-oos-validation-refresh") or {}
        defaults.update(dict(entry.get("kwargs") or {}))
        if entry.get("schedule") is not None:
            defaults["schedule"] = str(entry.get("schedule"))
    except Exception:
        pass
    return defaults


async def _load_active_advisor_fund_codes(session: AsyncSession, *, limit: int = 200) -> list[str]:
    """从已配置策略中读取 Advisor 活跃基金池。"""
    result = await session.execute(select(text("universe")).select_from(text("strategies")))
    codes: set[str] = set()
    for row in result:
        universe = row[0]
        if isinstance(universe, str):
            try:
                universe = json.loads(universe)
            except Exception:
                universe = {}
        if isinstance(universe, dict):
            candidates = universe.get("fund_codes") or []
        elif isinstance(universe, list):
            candidates = universe
        else:
            candidates = []
        codes.update(str(code) for code in candidates if code)
    return sorted(codes)[:limit]


def _build_user_profile_from_request(request: Any) -> dict[str, Any]:
    """从请求对象提取投资画像，过滤空值。"""
    profile = {
        "risk_level": getattr(request, "risk_level", None),
        "investment_goal": getattr(request, "investment_goal", None),
        "investment_horizon": getattr(request, "investment_horizon", None),
        "liquidity_need": getattr(request, "liquidity_need", None),
        "max_drawdown_tolerance": getattr(request, "max_drawdown_tolerance", None),
        "monthly_invest_amount": getattr(request, "monthly_invest_amount", None),
        "industry_concentration_tolerance": getattr(request, "industry_concentration_tolerance", None),
        "qdii_fx_risk_tolerance": getattr(request, "qdii_fx_risk_tolerance", None),
        "fee_sensitivity": getattr(request, "fee_sensitivity", None),
    }
    return {k: v for k, v in profile.items() if v is not None}


async def _build_risk_comparison(
    *,
    session: AsyncSession,
    fund_codes: list[str],
    total_capital: float,
    current_positions: dict[str, float],
    positions_detail: dict[str, dict[str, Any]],
    user_profile: dict[str, Any],
    strategy_id: int | None = None,
    strategy_name: str | None = None,
    mode: str = "live",
) -> dict[str, Any]:
    comparison: dict[str, Any] = {}
    for risk_level in ("conservative", "moderate", "aggressive"):
        profile = dict(user_profile or {})
        profile["risk_level"] = risk_level
        compare_request = AdvisorExecutionRequest(
            fund_codes=list(fund_codes),
            total_capital=total_capital,
            current_positions=dict(current_positions),
            positions_detail=dict(positions_detail),
            risk_level=risk_level,
            user_profile=profile,
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            mode=mode,
            enable_llm=False,
            enable_reliability_layers=True,
            enable_learned_weights=True,
        )
        advices, bundle = await execute_advisor_request(compare_request, session)
        execution_context = build_result_execution_context(compare_request, bundle, advices)
        comparison[risk_level] = {
            "risk_level": risk_level,
            "fund_count": len(advices),
            "advices": [item.to_dict() for item in advices],
            "summary": _generate_summary(advices),
            "execution_context": execution_context,
        }
    return comparison


def _classify_performance_label(action: str | None, return_20d: Any, hit_20d: Any) -> str:
    if return_20d is None or hit_20d is None:
        return "not_evaluable"
    try:
        value = float(return_20d)
    except (TypeError, ValueError):
        return "not_evaluable"
    normalized_action = str(action or "hold")
    if normalized_action == "buy":
        if value >= 0.03:
            return "effective"
        if value <= -0.03:
            return "ineffective"
        return "neutral"
    if normalized_action == "sell":
        if value <= -0.03:
            return "effective"
        if value >= 0.03:
            return "ineffective"
        return "neutral"
    if normalized_action == "watch":
        if abs(value) >= 0.03:
            return "effective"
        return "neutral"
    return "neutral"


def _serialize_reminder_candidate(candidate: Any) -> dict[str, Any]:
    return {
        "id": 0,
        "advisor_result_id": int(candidate.advisor_result_id),
        "fund_code": candidate.fund_code,
        "category": candidate.category,
        "reminder_type": candidate.reminder_type,
        "severity": candidate.severity,
        "status": "active",
        "title": candidate.title,
        "description": candidate.description,
        "payload": candidate.payload,
        "trigger_date": candidate.trigger_date.isoformat(),
        "resolved_at": None,
        "dismissed_at": None,
        "created_at": None,
        "updated_at": None,
    }


def _serialize_advisor_history_row(
    row: Any,
    *,
    include_detail: bool = False,
    execution_records: list[Any] | None = None,
) -> dict[str, Any]:
    from app.services.advisor_execution_records import (
        build_execution_plan_statuses,
        serialize_execution_record,
        summarize_execution_records,
    )

    execution_context = getattr(row, "execution_context", None)
    nav_data_stale = (
        execution_context.get("nav_data_stale")
        if isinstance(execution_context, dict)
        else None
    )
    payload = {
        "id": int(getattr(row, "id")),
        "advice_date": str(getattr(row, "advice_date")),
        "fund_codes": list(getattr(row, "fund_codes", None) or []),
        "total_capital": float(getattr(row, "total_capital", 0) or 0),
        "risk_level": str(getattr(row, "risk_level", "") or ""),
        "strategy_id": getattr(row, "strategy_id", None),
        "strategy_name": getattr(row, "strategy_name", None),
        "summary": getattr(row, "summary", None) or {},
        "nav_data_stale": nav_data_stale,
        "note": getattr(row, "note", None),
        "created_at": _iso_datetime(getattr(row, "created_at", None)),
        "updated_at": _iso_datetime(getattr(row, "updated_at", None)),
    }
    if not include_detail:
        return payload

    records = execution_records or []
    payload.update({
        "current_positions": getattr(row, "current_positions", None),
        "positions_detail": getattr(row, "positions_detail", None),
        "user_profile": getattr(row, "user_profile", None),
        "advices": list(getattr(row, "advices", None) or []),
        "analysis_mode": getattr(row, "analysis_mode", None),
        "source_result_id": getattr(row, "source_result_id", None),
        "learned_params_version_id": getattr(row, "learned_params_version_id", None),
        "parameter_set_id": getattr(row, "parameter_set_id", None),
        "execution_context": execution_context,
        "risk_comparison": execution_context.get("risk_comparison") if isinstance(execution_context, dict) else None,
        "execution_records": [serialize_execution_record(record) for record in records],
        "execution_summary": summarize_execution_records(getattr(row, "advices", None), records),
        "execution_plan_status": build_execution_plan_statuses(getattr(row, "advices", None), records),
    })
    from app.services.advisor_reminders import build_advisor_reminder_candidates

    execution_summary = payload.get("execution_summary") or {}
    execution_plan_status = payload.get("execution_plan_status") or {}
    payload["reminders"] = [
        _serialize_reminder_candidate(candidate)
        for candidate in build_advisor_reminder_candidates(
            row,
            execution_summary=execution_summary,
            execution_plan_status=execution_plan_status,
        )
    ]
    return payload


async def _refresh_advisor_history_row(row: Any, session: AsyncSession) -> dict[str, Any]:
    """按原历史记录的 as-of 日期重放并保存为一条新的刷新记录。"""
    positions_detail = dict(getattr(row, "positions_detail", None) or {})
    current_positions = dict(getattr(row, "current_positions", None) or {})
    exec_request = AdvisorExecutionRequest(
        fund_codes=list(getattr(row, "fund_codes", None) or []),
        total_capital=float(getattr(row, "total_capital", 0) or 0),
        current_positions=current_positions,
        positions_detail=positions_detail,
        risk_level=str(getattr(row, "risk_level", "moderate") or "moderate"),
        user_profile=dict(getattr(row, "user_profile", None) or {"risk_level": getattr(row, "risk_level", "moderate")}),
        strategy_id=getattr(row, "strategy_id", None),
        strategy_name=getattr(row, "strategy_name", None),
        as_of_date=getattr(row, "advice_date", None),
        mode="history_refresh",
        enable_llm=False,
        enable_reliability_layers=True,
        enable_learned_weights=True,
        source_result_id=int(getattr(row, "id")),
    )
    advices, bundle = await execute_advisor_request(exec_request, session)
    execution_context = build_result_execution_context(exec_request, bundle, advices)
    execution_context.setdefault("replay", {})
    execution_context["replay"].update({
        "requested_result_id": int(getattr(row, "id")),
        "original_advice_date": str(getattr(row, "advice_date")),
        "replayed_as_of_date": str(exec_request.as_of_date) if exec_request.as_of_date else None,
    })

    advice_payload = [advice.to_dict() for advice in advices]
    advice_date = None
    for item in advice_payload:
        if item.get("advice_date"):
            try:
                advice_date = date.fromisoformat(str(item["advice_date"])[:10])
                break
            except ValueError:
                pass
    advice_date = advice_date or date.today()

    new_row = AdvisorResult(
        advice_date=advice_date,
        fund_codes=list(getattr(row, "fund_codes", None) or []),
        total_capital=getattr(row, "total_capital", 0),
        risk_level=str(getattr(row, "risk_level", "moderate") or "moderate"),
        strategy_id=getattr(row, "strategy_id", None),
        strategy_name=getattr(row, "strategy_name", None),
        current_positions=current_positions,
        positions_detail=positions_detail,
        user_profile=dict(getattr(row, "user_profile", None) or {}),
        learned_params_version_id=getattr(getattr(bundle, "learned_weights", None), "version_id", None),
        parameter_set_id=(execution_context.get("parameter_set") or {}).get("param_set_id"),
        source_result_id=int(getattr(row, "id")),
        analysis_mode="history_refresh",
        execution_context=execution_context,
        advices=advice_payload,
        summary=_generate_summary(advices),
        note=getattr(row, "note", None),
        tracked_returns=None,
        tracked_at=None,
    )
    session.add(new_row)
    await session.commit()
    await session.refresh(new_row)
    return {
        "status": "refreshed",
        "id": int(new_row.id),
        "source_id": int(getattr(row, "id")),
        "updated_at": _iso_datetime(getattr(new_row, "updated_at", None) or getattr(new_row, "created_at", None)),
    }


@router.post(
    "/analyze",
    summary="生成交易建议",
    description=(
        "综合技术分析（MA/MACD/RSI/布林带）、动量分析、策略信号和"
        "Bootstrap 预测，为指定基金生成参考性的买卖建议。"
        "按基金类型自动调整分析权重，包含费用估算和模型局限性说明。"
    ),
)
async def analyze_funds(
    request: AdvisorAnalyzeRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """为指定基金生成交易建议。"""
    normalized_positions_detail = {
        code: detail.to_legacy_dict()
        for code, detail in request.positions_detail.items()
    }
    normalized_current_positions = dict(request.current_positions)
    for code, detail in normalized_positions_detail.items():
        if code not in normalized_current_positions and detail.get("market_value") is not None:
            normalized_current_positions[code] = float(detail["market_value"])

    exec_request = AdvisorExecutionRequest(
        fund_codes=list(request.fund_codes),
        total_capital=request.total_capital,
        current_positions=normalized_current_positions,
        positions_detail=normalized_positions_detail,
        risk_level=request.risk_level,
        user_profile=_build_user_profile_from_request(request),
        mode="live",
        enable_llm=False,
        enable_reliability_layers=True,
        enable_learned_weights=True,
    )
    advices, bundle = await execute_advisor_request(exec_request, session)

    if not bundle.nav_data:
        raise HTTPException(
            status_code=404,
            detail="未找到指定基金的净值数据，请确认基金代码正确且已采集数据",
        )

    from app.services.trading_advisor import calculate_fund_trade_timing, get_next_effective_trading_date

    effective_date, cutoff_info = get_next_effective_trading_date()
    default_action = next((a.action for a in advices if a.action not in {"hold", "watch"}), "buy")
    default_fund_type = next((a.fund_type for a in advices if a.action not in {"hold", "watch"}), None)
    trade_timing = calculate_fund_trade_timing(default_action, default_fund_type)

    execution_context = build_result_execution_context(exec_request, bundle, advices)
    risk_comparison = None
    if request.compare_risk_levels:
        risk_comparison = await _build_risk_comparison(
            session=session,
            fund_codes=list(request.fund_codes),
            total_capital=request.total_capital,
            current_positions=normalized_current_positions,
            positions_detail=normalized_positions_detail,
            user_profile=_build_user_profile_from_request(request),
            mode="live",
        )

    return {
        "advice_date": date.today().isoformat(),
        "total_capital": request.total_capital,
        "risk_level": exec_request.risk_level,
        "fund_count": len(advices),
        "advices": [a.to_dict() for a in advices],
        "summary": _generate_summary(advices),
        "user_profile": _build_user_profile_from_request(request),
        "learned_params_version_id": getattr(bundle.learned_weights, "version_id", None),
        "parameter_set_id": (execution_context.get("parameter_set") or {}).get("param_set_id"),
        "execution_context": execution_context,
        "risk_comparison": risk_comparison,
        "trading_time": {
            **trade_timing.to_dict(),
            "effective_date": effective_date,
            "cutoff_info": cutoff_info,
            "note": "基金申赎以 15:00 为界，15:00:00 及之后提交按下一交易日净值确认",
        },
        "disclaimer": (
            "本建议基于历史数据的统计分析，不构成投资建议。"
            "请结合自身风险承受能力独立决策。"
        ),
    }


@router.post(
    "/portfolio",
    summary="组合调仓建议",
    description=(
        "基于用户已创建的策略，分析策略基金池中所有基金，"
        "生成整体组合的调仓建议（买入哪些、卖出哪些、各多少）。"
    ),
)
async def portfolio_advice(
    request: PortfolioAdviceRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """基于策略生成组合调仓建议。"""
    normalized_positions_detail = {
        code: detail.to_legacy_dict()
        for code, detail in request.positions_detail.items()
    }
    normalized_current_positions = dict(request.current_positions)
    for code, detail in normalized_positions_detail.items():
        if code not in normalized_current_positions and detail.get("market_value") is not None:
            normalized_current_positions[code] = float(detail["market_value"])

    exec_request = AdvisorExecutionRequest(
        fund_codes=[],
        total_capital=request.total_capital,
        current_positions=normalized_current_positions,
        positions_detail=normalized_positions_detail,
        risk_level=request.risk_level,
        user_profile=_build_user_profile_from_request(request),
        strategy_id=request.strategy_id,
        mode="portfolio",
        enable_llm=False,
        enable_reliability_layers=True,
        enable_learned_weights=True,
    )
    try:
        advices, bundle = await execute_advisor_request(exec_request, session)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not exec_request.fund_codes:
        raise HTTPException(status_code=400, detail="策略基金池为空")
    if not bundle.nav_data:
        raise HTTPException(
            status_code=404,
            detail="策略基金池中的基金均无净值数据，请先在「基金检索」中采集数据",
        )

    from app.services.trading_advisor import calculate_fund_trade_timing, get_next_effective_trading_date

    effective_date, cutoff_info = get_next_effective_trading_date()
    default_action = next((a.action for a in advices if a.action not in {"hold", "watch"}), "buy")
    default_fund_type = next((a.fund_type for a in advices if a.action not in {"hold", "watch"}), None)
    trade_timing = calculate_fund_trade_timing(default_action, default_fund_type)

    execution_context = build_result_execution_context(exec_request, bundle, advices)
    risk_comparison = None
    if request.compare_risk_levels:
        risk_comparison = await _build_risk_comparison(
            session=session,
            fund_codes=list(exec_request.fund_codes),
            total_capital=request.total_capital,
            current_positions=normalized_current_positions,
            positions_detail=normalized_positions_detail,
            user_profile=_build_user_profile_from_request(request),
            strategy_id=request.strategy_id,
            strategy_name=exec_request.strategy_name,
            mode="portfolio",
        )

    return {
        "advice_date": date.today().isoformat(),
        "strategy_id": request.strategy_id,
        "strategy_name": exec_request.strategy_name,
        "total_capital": request.total_capital,
        "risk_level": exec_request.risk_level,
        "fund_count": len(advices),
        "advices": [a.to_dict() for a in advices],
        "summary": _generate_summary(advices),
        "user_profile": _build_user_profile_from_request(request),
        "learned_params_version_id": getattr(bundle.learned_weights, "version_id", None),
        "parameter_set_id": (execution_context.get("parameter_set") or {}).get("param_set_id"),
        "execution_context": execution_context,
        "risk_comparison": risk_comparison,
        "trading_time": {
            **trade_timing.to_dict(),
            "effective_date": effective_date,
            "cutoff_info": cutoff_info,
            "note": "基金申赎以 15:00 为界，15:00:00 及之后提交按下一交易日净值确认",
        },
        "disclaimer": (
            "本建议基于历史数据的统计分析，不构成投资建议。"
            "请结合自身风险承受能力独立决策。"
        ),
    }


# ---------------------------------------------------------------------------
# 建议结果保存与历史查询
# ---------------------------------------------------------------------------


class SaveAdvisorResultRequest(BaseModel):
    """保存建议结果请求。"""

    advice_date: str = Field(..., description="建议日期 (YYYY-MM-DD)")
    fund_codes: list[str] = Field(..., description="基金代码列表")
    total_capital: float = Field(..., description="总可用资金")
    risk_level: str = Field(..., description="风险偏好")
    strategy_id: int | None = Field(None, description="策略 ID（组合模式）")
    strategy_name: str | None = Field(None, description="策略名称")
    current_positions: dict[str, float] | None = Field(None, description="当前持仓")
    positions_detail: dict[str, PositionDetailPayload] | None = Field(None, description="持仓详情")
    user_profile: dict[str, Any] | None = Field(None, description="投资画像快照")
    advices: list[dict[str, Any]] = Field(..., description="建议列表")
    summary: dict[str, Any] = Field(..., description="摘要统计")
    note: str | None = Field(None, description="用户备注")
    learned_params_version_id: int | None = Field(None, description="学习参数版本 ID")
    parameter_set_id: str | None = Field(None, description="默认参数集 ID")
    execution_context: dict[str, Any] | None = Field(None, description="执行审计上下文")


@router.post(
    "/save",
    summary="保存建议结果",
    description="将生成的交易建议保存到数据库。每次保存都会生成一条新记录，保留完整历史。",
)
async def save_advisor_result(
    request: SaveAdvisorResultRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """保存交易建议结果。每次保存都追加一条新记录。"""
    from app.data.models.advisor_results import AdvisorResult

    sorted_codes = sorted(request.fund_codes)

    normalized_positions_detail = {
        code: detail.to_legacy_dict()
        for code, detail in (request.positions_detail or {}).items()
    }

    result = AdvisorResult(
        advice_date=date.fromisoformat(request.advice_date),
        fund_codes=sorted_codes,
        total_capital=request.total_capital,
        risk_level=request.risk_level,
        strategy_id=request.strategy_id,
        strategy_name=request.strategy_name,
        current_positions=request.current_positions,
        positions_detail=normalized_positions_detail,
        user_profile=request.user_profile,
        learned_params_version_id=request.learned_params_version_id,
        parameter_set_id=request.parameter_set_id,
        analysis_mode="manual_save",
        execution_context=request.execution_context or {
            "analysis_mode": "manual_save",
            "fund_count": len(sorted_codes),
            "fund_codes": sorted_codes,
            "user_profile_keys": sorted((request.user_profile or {}).keys()),
            "parameter_set": {"param_set_id": request.parameter_set_id},
            "learned_params": {"version_id": request.learned_params_version_id},
        },
        advices=request.advices,
        summary=request.summary,
        note=request.note,
    )
    session.add(result)
    await session.commit()
    await session.refresh(result)
    return {
        "status": "created",
        "id": result.id,
        "message": "建议已保存为新记录",
    }


@router.get(
    "/history",
    summary="查询历史建议",
    description="查询已保存的历史交易建议记录，支持分页。",
)
async def list_advisor_history(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """查询历史建议列表。"""
    from sqlalchemy import func, select

    from app.data.models.advisor_results import AdvisorResult

    # 总数
    count_query = select(func.count(AdvisorResult.id))
    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

    # 分页查询
    query = (
        select(AdvisorResult)
        .order_by(
            AdvisorResult.updated_at.desc().nullslast(),
            AdvisorResult.created_at.desc().nullslast(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await session.execute(query)
    rows = result.scalars().all()

    items = [_serialize_advisor_history_row(r) for r in rows]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
    }


@router.get(
    "/history/{result_id}",
    summary="获取历史建议详情",
    description="获取单条已保存的建议完整内容，包括所有基金的详细建议。",
)
async def get_advisor_history_detail(
    result_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """获取单条历史建议详情。"""
    from app.services.advisor_execution_records import build_execution_plan_statuses, load_execution_records_for_result, summarize_execution_records
    from app.services.advisor_reminders import load_advisor_reminders_for_result, serialize_advisor_reminder, sync_advisor_reminders_for_result

    row = await _get_advisor_result_or_404(result_id, session)
    execution_records = await load_execution_records_for_result(session, result_id)
    payload = _serialize_advisor_history_row(row, include_detail=True, execution_records=execution_records)
    persisted_reminders = await load_advisor_reminders_for_result(session, result_id)
    if not persisted_reminders:
        execution_summary = summarize_execution_records(getattr(row, "advices", None), execution_records)
        execution_plan_status = build_execution_plan_statuses(getattr(row, "advices", None), execution_records)
        await sync_advisor_reminders_for_result(
            session,
            row,
            execution_summary=execution_summary,
            execution_plan_status=execution_plan_status,
        )
        persisted_reminders = await load_advisor_reminders_for_result(session, result_id)
    payload["reminders"] = [serialize_advisor_reminder(item) for item in persisted_reminders]
    return payload


@router.get(
    "/reminders",
    summary="查询 Advisor 提醒",
    description="分页查询 Advisor 历史建议提醒，支持按状态、分类、严重级别和建议记录筛选。",
)
async def list_advisor_reminders(
    status: str | None = Query(None, description="提醒状态: active/resolved/dismissed"),
    category: str | None = Query(None, description="提醒分类: validity/risk/execution/plan/system"),
    severity: str | None = Query(None, description="严重级别: info/warning/error/success"),
    advisor_result_id: int | None = Query(None, description="关联建议记录 ID"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.services.advisor_reminders import load_advisor_reminders, normalize_reminder_status, serialize_advisor_reminder

    if status is not None:
        try:
            status = normalize_reminder_status(status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    rows, total = await load_advisor_reminders(
        session,
        status=status,
        category=category,
        severity=severity,
        advisor_result_id=advisor_result_id,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [serialize_advisor_reminder(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
    }


@router.post(
    "/reminders/refresh",
    summary="刷新 Advisor 提醒",
    description="根据历史建议、执行记录与计划任务状态重新计算并同步 Advisor 提醒。",
)
async def refresh_advisor_reminders(
    advisor_result_id: int | None = Query(None, description="只刷新指定建议记录；为空则刷新最近历史建议"),
    lookback_days: int = Query(120, ge=1, le=3650, description="批量刷新时回看最近多少天的建议"),
    limit: int = Query(200, ge=1, le=1000, description="批量刷新时最多处理多少条建议"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.data.models.advisor_results import AdvisorResult
    from app.services.advisor_execution_records import build_execution_plan_statuses, load_execution_records_for_result, summarize_execution_records
    from app.services.advisor_reminders import sync_advisor_reminders_for_result

    rows: list[Any] = []
    if advisor_result_id is not None:
        rows = [await _get_advisor_result_or_404(advisor_result_id, session)]
    else:
        min_date = date.today() - timedelta(days=lookback_days)
        result = await session.execute(
            select(AdvisorResult)
            .where(AdvisorResult.advice_date >= min_date)
            .order_by(AdvisorResult.updated_at.desc().nullslast(), AdvisorResult.id.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())

    refreshed = []
    total_created = 0
    total_reactivated = 0
    total_updated = 0
    total_resolved = 0
    for row in rows:
        records = await load_execution_records_for_result(session, int(getattr(row, "id")))
        execution_summary = summarize_execution_records(getattr(row, "advices", None), records)
        execution_plan_status = build_execution_plan_statuses(getattr(row, "advices", None), records)
        stats = await sync_advisor_reminders_for_result(
            session,
            row,
            execution_summary=execution_summary,
            execution_plan_status=execution_plan_status,
        )
        total_created += stats["created"]
        total_reactivated += stats["reactivated"]
        total_updated += stats["updated"]
        total_resolved += stats["resolved"]
        refreshed.append({
            "advisor_result_id": int(getattr(row, "id")),
            "advice_date": str(getattr(row, "advice_date")),
            **stats,
        })

    return {
        "status": "success",
        "processed": len(rows),
        "created": total_created,
        "reactivated": total_reactivated,
        "updated": total_updated,
        "resolved": total_resolved,
        "items": refreshed,
    }


@router.get(
    "/reminders/preferences",
    summary="查询 Advisor 提醒订阅偏好",
    description="查询服务端提醒摘要的订阅偏好；当前以 profile_key/default 作为用户级作用域。",
)
async def get_advisor_reminder_preferences(
    profile_key: str | None = Query(None, description="用户/画像作用域键；为空使用 default"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.services.advisor_reminders import load_advisor_reminder_preference, serialize_advisor_reminder_preference

    preference = await load_advisor_reminder_preference(session, profile_key)
    return {"status": "success", "preference": serialize_advisor_reminder_preference(preference, profile_key=profile_key)}


@router.put(
    "/reminders/preferences",
    summary="保存 Advisor 提醒订阅偏好",
    description="保存服务端主动提醒摘要的最低级别、通道、静默分类和免打扰配置。",
)
async def put_advisor_reminder_preferences(
    request: AdvisorReminderPreferenceRequest,
    profile_key: str | None = Query(None, description="用户/画像作用域键；为空使用 default"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.services.advisor_reminders import serialize_advisor_reminder_preference, upsert_advisor_reminder_preference

    try:
        preference = await upsert_advisor_reminder_preference(
            session,
            profile_key=profile_key,
            enabled=request.enabled,
            min_severity=request.min_severity,
            lookahead_days=request.lookahead_days,
            channels=request.channels,
            muted_categories=request.muted_categories,
            quiet_hours=request.quiet_hours,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "updated", "preference": serialize_advisor_reminder_preference(preference)}


@router.post(
    "/reminders/digest",
    summary="生成或发送 Advisor 提醒摘要",
    description="从服务端提醒中心生成跨端一致的提醒摘要；可 dry_run 预览，也可按已配置通知通道主动发送。",
)
async def create_advisor_reminder_digest(
    days: int | None = Query(None, ge=0, le=30, description="纳入未来多少天内到期的提醒；为空使用订阅偏好"),
    min_severity: str | None = Query(None, description="最低主动通知级别；为空使用订阅偏好"),
    dry_run: bool = Query(True, description="是否仅生成摘要不发送通知"),
    channels: str | None = Query(None, description="可选通知通道逗号分隔: email,wecom,telegram；为空使用订阅偏好/环境默认"),
    profile_key: str | None = Query(None, description="用户/画像作用域键；为空使用 default"),
    use_preferences: bool = Query(True, description="是否读取服务端订阅偏好"),
    limit: int = Query(50, ge=1, le=200, description="摘要最多纳入多少条提醒"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.services.advisor_reminders import REMINDER_SEVERITIES, send_advisor_reminder_digest

    normalized_min_severity = str(min_severity).strip().lower() if min_severity else None
    if normalized_min_severity is not None and normalized_min_severity not in REMINDER_SEVERITIES:
        allowed = ", ".join(sorted(REMINDER_SEVERITIES))
        raise HTTPException(status_code=400, detail=f"min_severity 必须是以下之一: {allowed}")
    channel_list = [item.strip() for item in str(channels or "").split(",") if item.strip()] or None
    return await send_advisor_reminder_digest(
        session,
        days=days,
        min_severity=normalized_min_severity,
        channels=channel_list,
        profile_key=profile_key,
        use_preferences=use_preferences,
        dry_run=dry_run,
        limit=limit,
    )


@router.patch(
    "/reminders/{reminder_id}",
    summary="更新 Advisor 提醒状态",
    description="手动忽略、恢复或标记一条 Advisor 提醒为已解决。",
)
async def update_advisor_reminder(
    reminder_id: int,
    request: AdvisorReminderUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.services.advisor_reminders import (
        REMINDER_STATUS_ACTIVE,
        REMINDER_STATUS_DISMISSED,
        REMINDER_STATUS_RESOLVED,
        load_advisor_reminder_by_id,
        normalize_reminder_status,
        serialize_advisor_reminder,
    )

    reminder = await load_advisor_reminder_by_id(session, reminder_id)
    if reminder is None:
        raise HTTPException(status_code=404, detail="提醒不存在")
    try:
        normalized_status = normalize_reminder_status(request.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    reminder.status = normalized_status
    now = datetime.now(timezone.utc)
    if normalized_status == REMINDER_STATUS_DISMISSED:
        reminder.dismissed_at = now
        reminder.resolved_at = None
    elif normalized_status == REMINDER_STATUS_RESOLVED:
        reminder.resolved_at = now
        reminder.dismissed_at = None
    elif normalized_status == REMINDER_STATUS_ACTIVE:
        reminder.dismissed_at = None
        reminder.resolved_at = None

    await session.commit()
    await session.refresh(reminder)
    return {"status": "updated", "item": serialize_advisor_reminder(reminder)}


@router.get(
    "/history/{result_id}/executions",
    summary="查询用户实际执行记录",
    description="查询某条历史建议下用户记录的已执行/部分执行/未执行等真实执行情况。",
)
async def list_advisor_execution_records(
    result_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """查询历史建议的用户实际执行记录。"""
    from app.services.advisor_execution_records import (
        load_execution_records_for_result,
        serialize_execution_record,
        summarize_execution_records,
    )

    row = await _get_advisor_result_or_404(result_id, session)
    records = await load_execution_records_for_result(session, result_id)
    return {
        "advisor_result_id": result_id,
        "items": [serialize_execution_record(record) for record in records],
        "summary": summarize_execution_records(row.advices, records),
    }


@router.post(
    "/history/{result_id}/executions",
    summary="记录用户实际执行",
    description="为某条历史建议记录一笔用户实际成交、部分成交、计划执行或未执行原因。",
)
async def create_advisor_execution_record(
    result_id: int,
    request: AdvisorExecutionRecordRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """创建用户实际执行记录。"""
    from app.data.models.advisor_execution_records import AdvisorExecutionRecord
    from app.services.advisor_execution_records import (
        build_execution_snapshot_from_advice,
        find_advice_snapshot,
        normalize_advice_action,
        normalize_execution_status,
        normalize_trade_intent,
        parse_execution_date,
        serialize_execution_record,
    )

    row = await _get_advisor_result_or_404(result_id, session)
    advice_snapshot = find_advice_snapshot(row.advices, request.fund_code)
    if advice_snapshot is None:
        raise HTTPException(status_code=404, detail="该历史建议中未找到对应基金")

    try:
        execution_status = normalize_execution_status(request.execution_status)
        executed_date = parse_execution_date(request.executed_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        from app.services.advisor_execution_records import validate_execution_payload

        validate_execution_payload(
            execution_status=execution_status,
            executed_date=executed_date,
            not_executed_reason=request.not_executed_reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    snapshot = build_execution_snapshot_from_advice(advice_snapshot)
    advice_action = normalize_advice_action(request.advice_action or snapshot["advice_action"])
    trade_intent = normalize_trade_intent(request.trade_intent or snapshot["trade_intent"], advice_action)

    record = AdvisorExecutionRecord(
        advisor_result_id=row.id,
        advice_date=row.advice_date,
        fund_code=request.fund_code,
        advice_action=advice_action,
        trade_intent=trade_intent,
        suggested_amount=snapshot["suggested_amount"],
        suggested_shares=snapshot["suggested_shares"],
        suggested_pct=snapshot["suggested_pct"],
        confidence=snapshot["confidence"],
        execution_status=execution_status,
        executed_date=executed_date,
        executed_amount=request.executed_amount,
        executed_shares=request.executed_shares,
        executed_nav=request.executed_nav,
        executed_fee=request.executed_fee,
        execution_channel=request.execution_channel,
        not_executed_reason=request.not_executed_reason,
        deviation_reason=request.deviation_reason,
        user_note=request.user_note,
        source=request.source,
        metadata_json=request.metadata,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    await _sync_advisor_result_reminders(session, row)
    await _refresh_advisor_user_learning(session)
    await session.commit()
    return {"status": "created", "record": serialize_execution_record(record)}


@router.post(
    "/history/{result_id}/executions/import",
    summary="批量导入用户实际执行记录",
    description="从 CSV/Excel 文件批量导入成交、部分成交、计划执行或未执行记录；有效行会落库，错误行返回明细。",
)
async def import_advisor_execution_records(
    result_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """批量导入历史建议的用户实际执行记录。"""
    from app.services.advisor_execution_records import (
        build_execution_record_from_import_row,
        load_execution_records_for_result,
        parse_execution_import_file,
        serialize_execution_record,
        summarize_execution_records,
    )

    row = await _get_advisor_result_or_404(result_id, session)
    filename = file.filename or "execution_records.csv"
    try:
        content = await file.read()
        import_rows = parse_execution_import_file(filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created_records = []
    row_results = []
    for index, import_row in enumerate(import_rows, start=2):
        try:
            record = build_execution_record_from_import_row(
                advisor_result=row,
                row=import_row,
                source_filename=filename,
                row_number=index,
            )
            session.add(record)
            await session.flush()
            created_records.append(record)
            row_results.append({
                "row_number": index,
                "status": "created",
                "fund_code": record.fund_code,
                "execution_status": record.execution_status,
            })
        except ValueError as exc:
            row_results.append({
                "row_number": index,
                "status": "failed",
                "fund_code": import_row.get("fund_code"),
                "error": str(exc),
            })

    if created_records:
        await session.commit()
        for record in created_records:
            await session.refresh(record)
        await _sync_advisor_result_reminders(session, row)
        await _refresh_advisor_user_learning(session)
        await session.commit()
    else:
        await session.rollback()

    records = await load_execution_records_for_result(session, result_id)
    return {
        "status": "completed" if not any(item["status"] == "failed" for item in row_results) else "partial",
        "advisor_result_id": result_id,
        "filename": filename,
        "total_rows": len(row_results),
        "created_count": len(created_records),
        "failed_count": sum(1 for item in row_results if item["status"] == "failed"),
        "rows": row_results,
        "records": [serialize_execution_record(record) for record in created_records],
        "summary": summarize_execution_records(row.advices, records),
    }


@router.patch(
    "/executions/{execution_id}",
    summary="更新用户实际执行记录",
    description="更新一笔已保存的用户实际执行记录。",
)
async def update_advisor_execution_record(
    execution_id: int,
    request: AdvisorExecutionRecordUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """更新用户实际执行记录。"""
    from app.services.advisor_execution_records import (
        load_execution_record_by_id,
        normalize_execution_status,
        parse_execution_date,
        serialize_execution_record,
    )

    record = await load_execution_record_by_id(session, execution_id)
    if record is None:
        raise HTTPException(status_code=404, detail="执行记录不存在")

    try:
        if request.execution_status is not None:
            record.execution_status = normalize_execution_status(request.execution_status)
        if request.executed_date is not None:
            record.executed_date = parse_execution_date(request.executed_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for field_name in [
        "executed_amount",
        "executed_shares",
        "executed_nav",
        "executed_fee",
        "execution_channel",
        "not_executed_reason",
        "deviation_reason",
        "user_note",
    ]:
        value = getattr(request, field_name)
        if value is not None:
            setattr(record, field_name, value)
    if request.metadata is not None:
        record.metadata_json = request.metadata

    try:
        from app.services.advisor_execution_records import validate_execution_payload

        validate_execution_payload(
            execution_status=record.execution_status,
            executed_date=record.executed_date,
            not_executed_reason=record.not_executed_reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(record)
    parent_row = await _get_advisor_result_or_404(int(record.advisor_result_id), session)
    await _sync_advisor_result_reminders(session, parent_row)
    await _refresh_advisor_user_learning(session)
    await session.commit()
    return {"status": "updated", "record": serialize_execution_record(record)}


@router.delete(
    "/executions/{execution_id}",
    summary="删除用户实际执行记录",
    description="删除一笔已保存的用户实际执行记录。",
)
async def delete_advisor_execution_record(
    execution_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """删除用户实际执行记录。"""
    from app.services.advisor_execution_records import load_execution_record_by_id

    record = await load_execution_record_by_id(session, execution_id)
    if record is None:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    parent_row = await _get_advisor_result_or_404(int(record.advisor_result_id), session)
    await session.delete(record)
    await session.commit()
    await _sync_advisor_result_reminders(session, parent_row)
    await _refresh_advisor_user_learning(session)
    await session.commit()
    return {"status": "success", "message": "已删除"}


@router.post(
    "/history/{result_id}/refresh",
    summary="更新历史建议",
    description="按该条历史记录保存时的参数，基于最新数据重新生成建议并覆盖原记录。",
)
async def refresh_advisor_history(
    result_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """按历史记录原参数重新生成建议并覆盖原记录。"""
    row = await _get_advisor_result_or_404(result_id, session)
    return await _refresh_advisor_history_row(row, session)


@router.delete(
    "/history/{result_id}",
    summary="删除历史建议",
    description="删除一条已保存的建议记录。",
)
async def delete_advisor_history(
    result_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """删除历史建议记录。"""
    row = await _get_advisor_result_or_404(result_id, session)
    await session.delete(row)
    await session.commit()

    return {"status": "success", "message": "已删除"}


@router.get(
    "/user-learning/profile",
    summary="查看 Advisor 用户级学习画像",
    description="基于用户实际执行记录学习交易节奏、分批偏好和解释偏好；只影响执行体验，不改变底层风控门禁。",
)
async def get_advisor_user_learning_profile(
    profile_key: str | None = Query(None, description="用户/画像键；未接入登录时默认 default"),
    refresh: bool = Query(False, description="是否立即重算并保存用户级学习画像"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.services.advisor_user_learning import AdvisorUserLearningService

    if refresh:
        snapshot = await AdvisorUserLearningService.learn_and_persist(
            session,
            profile_key=profile_key,
        )
        await session.commit()
    else:
        snapshot = await AdvisorUserLearningService.load_or_learn(
            session,
            profile_key=profile_key,
        )
        await session.commit()
    if snapshot is None:
        return {"status": "not_available", "profile": None}
    return {"status": "success", "profile": snapshot.to_dict()}


@router.get(
    "/snapshots/versions",
    summary="检索原始响应快照版本",
    description="按 provider/fund_code/endpoint/ext/snapshot_date/as_of 检索 Advisor 相关原始响应快照版本元数据。",
)
async def list_advisor_snapshot_versions(
    provider: str | None = Query(None, description="数据提供方，如 eastmoney"),
    fund_code: str | None = Query(None, description="基金代码"),
    endpoint: str | None = Query(None, description="逻辑端点，如 nav_history/fund_meta"),
    ext: str | None = Query(None, description="原始扩展名，如 json/html"),
    snapshot_date: str | None = Query(None, description="快照日期 YYYY-MM-DD"),
    as_of: str | None = Query(None, description="只返回该时点前可见的版本；支持 YYYY-MM-DD 或 ISO 时间"),
    limit: int = Query(100, ge=1, le=500, description="最大返回数量"),
) -> SnapshotVersionListResponse:
    """检索原始响应快照版本元数据。"""
    archive = SnapshotArchive()
    parsed_snapshot_date = _parse_optional_date(snapshot_date)
    parsed_as_of: date | datetime | None = None
    if as_of:
        try:
            parsed_as_of = _parse_optional_date(as_of) if len(as_of) <= 10 else datetime.fromisoformat(as_of)
        except ValueError:
            parsed_as_of = _parse_optional_date(as_of)
    versions = archive.list_versions(
        provider=provider,
        fund_code=fund_code,
        endpoint=endpoint,
        ext=ext,
        snapshot_date=parsed_snapshot_date,
        as_of=parsed_as_of,
    )
    items = [_snapshot_version_to_dict(version) for version in versions[-limit:]]
    items.reverse()
    return SnapshotVersionListResponse(items=items, total=len(items))


@router.get(
    "/snapshots/versions/{version_id}",
    summary="下载原始响应快照",
    description="按不可变 version_id 下载一份原始响应快照内容。",
)
async def download_advisor_snapshot_version(version_id: str) -> Response:
    """下载单个原始响应快照。"""
    archive = SnapshotArchive()
    versions = archive.list_versions()
    matched = next((version for version in versions if version.version_id == version_id), None)
    if matched is None:
        raise HTTPException(status_code=404, detail="快照版本不存在")
    try:
        raw_bytes = archive.load_raw_version(version_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="快照原始文件不存在") from exc
    filename = f"{matched.provider}_{matched.fund_code}_{matched.endpoint}_{matched.snapshot_date.isoformat()}_{matched.version_id}.{matched.ext}"
    return Response(
        content=raw_bytes,
        media_type=_snapshot_media_type(matched.ext),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/signals",
    summary="查询历史信号",
    description="查询策略生成的历史交易信号记录，支持按基金、策略、方向和日期筛选。",
)
async def query_signals(
    fund_code: str | None = Query(None, description="基金代码筛选"),
    strategy_id: int | None = Query(None, description="策略 ID 筛选"),
    direction: str | None = Query(None, description="方向筛选: subscribe/redeem/hold"),
    start_date: str | None = Query(None, description="起始日期 (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="结束日期 (YYYY-MM-DD)"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """查询历史交易信号。"""
    from app.data.models.signals import Signal
    from sqlalchemy import func, select

    # 构建查询
    query = select(Signal).order_by(Signal.signal_date.desc(), Signal.created_at.desc())
    count_query = select(func.count(Signal.id))

    # 应用筛选条件
    if fund_code:
        query = query.where(Signal.fund_code == fund_code)
        count_query = count_query.where(Signal.fund_code == fund_code)
    if strategy_id:
        query = query.where(Signal.strategy_id == strategy_id)
        count_query = count_query.where(Signal.strategy_id == strategy_id)
    if direction:
        from app.services.trading_advisor import normalize_trade_direction
        normalized_direction = normalize_trade_direction(direction)
        query = query.where(Signal.direction == normalized_direction)
        count_query = count_query.where(Signal.direction == normalized_direction)
    if start_date:
        query = query.where(Signal.signal_date >= start_date)
        count_query = count_query.where(Signal.signal_date >= start_date)
    if end_date:
        query = query.where(Signal.signal_date <= end_date)
        count_query = count_query.where(Signal.signal_date <= end_date)

    # 分页
    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await session.execute(query)
    signals = result.scalars().all()

    items = []
    for s in signals:
        items.append({
            "id": s.id,
            "strategy_id": s.strategy_id,
            "strategy_name": s.strategy_name,
            "fund_code": s.fund_code,
            "signal_date": str(s.signal_date),
            "direction": s.direction,
            "strength": float(s.strength) if s.strength else None,
            "target_weight": float(s.target_weight) if s.target_weight else None,
            "amount": float(s.amount) if s.amount else None,
            "shares": float(s.shares) if s.shares else None,
            "reason": s.reason,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
    }


@router.get(
    "/parameter-sets",
    summary="查询 Advisor 参数集",
    description="查询默认配置参数集及其门禁、审核、发布状态。",
)
async def list_advisor_parameter_sets(
    risk_level: str | None = Query(None, description="风险档筛选"),
    kind: str | None = Query(None, description="参数集类型筛选"),
    release_status: str | None = Query(None, description="发布状态筛选"),
    review_status: str | None = Query(None, description="审核状态筛选"),
    gate_status: str | None = Query(None, description="门禁状态筛选"),
    limit: int = Query(100, ge=1, le=500, description="返回数量"),
) -> dict[str, Any]:
    from app.services.advisor_parameter_governance import AdvisorParameterRegistry

    items = AdvisorParameterRegistry.list_parameter_sets(
        risk_level=risk_level,
        kind=kind,
        release_status=release_status,
        review_status=review_status,
        gate_status=gate_status,
        limit=limit,
    )
    return {
        "items": [item.to_dict(include_payload=False) for item in items],
        "total": len(items),
    }


@router.get(
    "/parameter-sets/active",
    summary="查看当前 active Advisor 参数集",
    description="返回指定风险档当前 active 默认配置参数集。",
)
async def get_active_advisor_parameter_set(
    risk_level: str = Query("moderate", description="风险档"),
) -> dict[str, Any]:
    from app.services.advisor_parameter_governance import AdvisorParameterRegistry

    item = AdvisorParameterRegistry.load_active_parameter_set(risk_level=risk_level)
    if item is None:
        return {"status": "not_found", "parameter_set": None}
    return {"status": "active", "parameter_set": item.to_dict(include_payload=True)}


@router.post(
    "/parameter-sets/register-default",
    summary="注册当前内置默认 Advisor 参数集",
    description="把当前风险档内置默认配置注册为可治理参数集，并执行 OOS/PBO 发布门禁。",
)
async def register_default_advisor_parameter_set(
    request: RegisterDefaultParameterSetRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.services.advisor_parameter_governance import AdvisorParameterRegistry

    fund_codes = request.fund_codes if request.fund_codes is not None else await _load_active_advisor_fund_codes(session)
    record = AdvisorParameterRegistry.register_default_parameter_set(
        risk_level=request.risk_level,
        config=build_advisor_config(request.risk_level),
        name=request.name,
        description=request.description,
        created_reason=request.created_reason,
        fund_codes=fund_codes,
        evaluate_gate=request.evaluate_gate,
        review_status=request.review_status,
        release_status=request.release_status,
    )
    if record is None:
        raise HTTPException(status_code=503, detail="advisor_parameter_sets 表不可用，请先运行数据库迁移")
    return {"status": "registered", "parameter_set": record.to_dict(include_payload=True)}


@router.post(
    "/parameter-sets/{param_set_id}/review",
    summary="审核 Advisor 参数集",
    description="人工标记参数集审核通过、拒绝或待审核。",
)
async def review_advisor_parameter_set(
    param_set_id: str,
    request: ParameterSetReviewRequest,
) -> dict[str, Any]:
    from app.services.advisor_parameter_governance import AdvisorParameterRegistry

    try:
        record = AdvisorParameterRegistry.review_parameter_set(
            param_set_id=param_set_id,
            review_status=request.review_status,
            reviewed_by=request.reviewed_by,
            review_notes=request.review_notes,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "reviewed", "parameter_set": record.to_dict(include_payload=True)}


@router.post(
    "/parameter-sets/{param_set_id}/activate",
    summary="激活 Advisor 参数集",
    description="将已通过 OOS/PBO 门禁且人工审核通过的参数集切为 active。",
)
async def activate_advisor_parameter_set(
    param_set_id: str,
    request: ParameterSetActivateRequest,
) -> dict[str, Any]:
    from app.services.advisor_parameter_governance import AdvisorParameterRegistry

    try:
        record = AdvisorParameterRegistry.activate_parameter_set(
            param_set_id=param_set_id,
            reason=request.reason,
            effective_from=_parse_optional_date(request.effective_from),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "activated", "parameter_set": record.to_dict(include_payload=True)}


@router.post(
    "/parameter-sets/rollback",
    summary="回滚 Advisor 参数集",
    description="回滚到指定或最近一个已通过门禁/审核的历史默认参数集。",
)
async def rollback_advisor_parameter_set(
    request: ParameterSetRollbackRequest,
) -> dict[str, Any]:
    from app.services.advisor_parameter_governance import AdvisorParameterRegistry

    try:
        record = AdvisorParameterRegistry.rollback_parameter_set(
            risk_level=request.risk_level,
            target_param_set_id=request.target_param_set_id,
            reason=request.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "rolled_back", "parameter_set": record.to_dict(include_payload=True)}


@router.get(
    "/config",
    summary="获取建议引擎配置",
    description="返回交易建议引擎的默认配置参数和风险等级说明。",
)
async def get_advisor_config() -> dict[str, Any]:
    """获取建议引擎默认配置。"""
    try:
        from app.services.runtime_health import check_queue_health

        runtime_health = {"queue": check_queue_health().to_dict()}
    except Exception as exc:
        runtime_health = {
            "queue": {
                "status": "unknown",
                "redis_available": False,
                "broker_url_configured": False,
                "queues": {},
                "warnings": ["运行时队列健康检查不可用"],
                "error": str(exc),
            }
        }
    return {
        "version": "5.0",
        "runtime_health": runtime_health,
        "risk_profiles": {
            "conservative": {
                "label": "保守型",
                "description": "较高买入门槛，较低仓位上限，适合风险厌恶型投资者",
                **RISK_PROFILES["conservative"],
            },
            "moderate": {
                "label": "稳健型",
                "description": "平衡的买卖阈值和仓位控制，适合大多数投资者",
                **RISK_PROFILES["moderate"],
            },
            "aggressive": {
                "label": "进取型",
                "description": "较低买入门槛，较高仓位上限，适合风险偏好型投资者",
                **RISK_PROFILES["aggressive"],
            },
        },
        "scoring_dimensions": {
            "technical": {
                "description": "技术分析（MA/MACD/RSI/布林带）",
                "note": "v5: 所有基金类型启用（非ETF降权但不归零），提供趋势参考",
            },
            "momentum": {
                "description": "动量/均值回复分析（收益率趋势和波动率状态）",
                "note": (
                    "v5: 自适应动量折扣温和化（最低0.5），"
                    "波动率折扣温和化（高波动时0.75而非0.6）"
                ),
            },
            "strategy": {
                "description": "策略信号（已配置策略的输出）",
                "note": "v5: 信号半衰期延长到14天，最低保留20%强度",
            },
            "prediction": {
                "description": "Bootstrap 预测（基于历史数据的条件概率估计）",
                "note": "v5: 恢复参与决策（权重0.10~0.15），提供概率视角补充",
            },
            "cross_sectional": {
                "description": "截面因子选基（同类基金间相对排序）",
                "note": "需要同类基金池≥10只，不可用时权重自动重分配",
            },
        },
        "v5_enhancements": {
            "adaptive_weights": "信号源不可用时自动将权重按比例分配给可用信号源",
            "dynamic_threshold": "每缺少一个信号源，买卖阈值降低0.07（最低0.08）",
            "signal_consensus": "≥3个信号方向一致时获得1.3×加成",
            "regime_moderation": "市场regime调整温和化，不再过度压制信号",
        },
        "position_sizing": {
            "method": "风险预算模型 (Risk Budget)",
            "description": (
                "基于目标组合波动率和单只基金波动率计算建议仓位，"
                "考虑基金间相关性（默认0.6）和最大回撤惩罚。"
            ),
        },
        "market_regime": {
            "description": "市场 regime 自适应检测（多基金投票法）",
            "regimes": ["bull", "bear", "crisis", "volatile", "normal"],
            "note": (
                "v5: 温和化调整 — crisis 时信号权重50%（原30%），"
                "volatile 时75%（原60%），bull 时加成至110%"
            ),
        },
        "signal_cooldown": {
            "enabled": True,
            "cooldown_days": 5,
            "description": "同方向信号冷却期，衰减系数0.7（v5温和化）",
        },
        "correlation_control": {
            "enabled": True,
            "threshold": 0.8,
            "method": "Spearman 秩相关（对厚尾分布更稳健）",
            "description": "高相关基金（>0.8）只保留评分最高的买入建议，避免同质化持仓",
        },
        "fee_estimation": {
            "enabled": True,
            "description": "自动估算申购/赎回费用，优先使用数据库实际费率",
        },
        "fund_type_awareness": {
            "description": "按基金类型自动调整分析权重",
            "types": ["stock", "index", "bond", "mixed", "money", "qdii", "fof"],
        },
        "engine_health": {
            "description": "引擎健康度监控（Spearman Rank IC + 命中率趋势）",
            "ic_healthy_threshold": 0.05,
            "ic_degraded_threshold": 0.02,
            "note": "IC 持续下降时自动告警，建议暂停使用",
        },
        "anti_overfitting": {
            "reliability_adjustment": "根据引擎健康度对综合评分、置信度和建议金额做自动折扣",
            "oos_reliability_layer": "若存在最近一次 Walk-Forward 样本外验证结果，会叠加第二层可靠性折扣",
            "oos_auto_refresh": "系统会在每日 21:40 自动派发 Walk-Forward 刷新任务，维护最新样本外缓存",
            "oos_risk_level_reuse": "优先使用当前风险偏好的样本外缓存，缺失时回退到 moderate 或该基金最近一次可用缓存",
            "learned_parameter_shrinkage": "反馈学习结果不会直接全量套用，而是向默认参数收缩并限制单次偏移幅度",
            "parameter_release_gate": "反馈学习参数必须通过 OOS/PBO 发布门禁才能成为默认参数；未通过时仅允许 shadow/实验和审计回放",
            "shadow_mode": "OOS 覆盖率、样本外信号数、OOS IC、IC 衰减或 PBO 任一关键条件不达标时，学习参数保存但不进入默认建议链路",
            "weight_change_limit": "学习权重相对默认值单次上调/下调默认不超过 30%",
            "threshold_change_limit": "学习阈值调整会先收缩后再应用，避免为历史样本过度放宽/收紧",
            "oos_signal_minimum": "样本外信号数少于 20 时只做保守参考，不给予强信任",
        },
        "limitations": [
            "基于历史数据，假设未来市场环境与样本期相似",
            "未经过充分的样本外验证",
            "无法预测 regime 切换和黑天鹅事件",
            "动量因子在A股2017年后有效性下降（已自适应折扣）",
            "不构成投资建议",
        ],
    }


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


async def _load_last_advices(
    fund_codes: list[str],
    session: AsyncSession,
) -> dict[str, dict[str, str]]:
    """加载各基金上次的建议记录，用于信号冷却机制。

    从最近保存的建议结果中提取每只基金的上次建议方向和日期。

    Returns:
        {fund_code: {action: str, date: str}}
    """
    from app.data.models.advisor_results import AdvisorResult
    from sqlalchemy import select

    last_advices: dict[str, dict[str, str]] = {}

    try:
        # 查最近若干条记录，优先用更新时间排序，尽量拿到每只基金最新的建议
        result = await session.execute(
            select(AdvisorResult)
            .order_by(
                AdvisorResult.updated_at.desc().nullslast(),
                AdvisorResult.created_at.desc().nullslast(),
            )
            .limit(20)
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
        # 如果查询失败（表不存在等），不影响主流程
        pass

    return last_advices


@router.get(
    "/feedback-status",
    summary="查看反馈学习状态",
    description="返回引擎自适应学习的当前状态，包括各因子IC、权重调整和阈值调整。",
)
async def get_feedback_status() -> dict[str, Any]:
    """查看反馈学习状态。"""
    from app.services.advisor_feedback import AdvisorFeedbackLearner

    learned = AdvisorFeedbackLearner.load_learned()
    if learned is None:
        return {
            "status": "not_learned",
            "message": "尚未运行反馈学习（每周日 04:00 自动运行，或手动触发）",
            "learned": None,
        }

    return {
        "status": "active" if learned.confidence >= 0.3 else "low_confidence",
        "message": (
            f"学习结果有效（置信度 {learned.confidence:.0%}，{learned.sample_count} 样本）"
            if learned.confidence >= 0.3
            else f"学习置信度不足（{learned.confidence:.0%}），使用默认参数"
        ),
        "learned": {
            "version_id": learned.version_id,
            "engine_version": learned.engine_version,
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
        },
    }


@router.post(
    "/feedback-learn",
    summary="手动触发反馈学习",
    description="立即运行反馈学习，基于历史建议效果调整引擎参数。",
)
async def trigger_feedback_learning() -> dict[str, Any]:
    """手动触发反馈学习。"""
    from app.services.advisor_feedback import AdvisorFeedbackLearner, FeedbackConfig

    learner = AdvisorFeedbackLearner(FeedbackConfig(lookback_days=180))
    learned = learner.learn_from_history_sync()

    return {
        "status": "completed",
        "version_id": learned.version_id,
        "engine_version": learned.engine_version,
        "learn_date": learned.learn_date,
        "sample_count": learned.sample_count,
        "confidence": learned.confidence,
        "adjustments_log": learned.adjustments_log,
        "weight_multipliers": {
            "technical": learned.multiplier_technical,
            "momentum": learned.multiplier_momentum,
            "strategy": learned.multiplier_strategy,
            "prediction": learned.multiplier_prediction,
            "cross_sectional": learned.multiplier_cross_sectional,
        },
        "threshold_adjustment": learned.threshold_adjustment,
    }


@router.get(
    "/positions",
    summary="读取当前持仓快照",
    description="返回 Advisor 页面最近一次保存到服务端的当前持仓列表。",
)
async def list_advisor_positions(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    records = await _load_persisted_advisor_positions(session)
    return {
        "status": "success",
        "total": len(records),
        "positions": [_serialize_holding_position(record) for record in records],
    }


@router.get(
    "/positions/template",
    summary="下载持仓导入模板",
    description="下载 Advisor 当前持仓导入模板，支持 CSV 或 XLSX。",
)
async def download_advisor_positions_template(
    format: str = Query("csv", pattern="^(csv|xlsx)$", description="模板格式：csv/xlsx"),
) -> Response:
    if format == "xlsx":
        content = _build_holding_template_xlsx_bytes()
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = "advisor_positions_template.xlsx"
    else:
        content = _build_holding_template_csv_bytes()
        media_type = "text/csv; charset=utf-8"
        filename = "advisor_positions_template.csv"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put(
    "/positions",
    summary="保存当前持仓快照",
    description="用前端当前持仓完整替换服务端保存的 Advisor 持仓快照。",
)
async def replace_advisor_positions(
    request: AdvisorPositionsReplaceRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    records = await _replace_persisted_advisor_positions(
        session,
        request.positions,
        source="manual",
        metadata={"saved_via": "positions_api"},
    )
    if records is None:
        return {
            "status": "unavailable",
            "total": len(request.positions),
            "positions": [position.model_dump() for position in _normalize_positions_payload(request.positions)],
        }
    return {
        "status": "saved",
        "total": len(records),
        "positions": [_serialize_holding_position(record) for record in records],
    }


@router.get(
    "/positions/import-history",
    response_model=AdvisorPositionImportHistoryResponse,
    summary="查询持仓导入历史",
    description="分页返回持仓 CSV/Excel 导入历史、成功/失败摘要和可恢复的持仓快照。",
)
async def list_advisor_position_import_history(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=100, description="每页数量"),
    limit: int | None = Query(None, ge=1, le=100, description="兼容旧版：返回数量；传入后等价于 page=1&page_size=limit"),
    session: AsyncSession = Depends(get_session),
) -> AdvisorPositionImportHistoryResponse:
    resolved_page = 1 if limit is not None else page
    resolved_page_size = limit if limit is not None else page_size
    total = await _count_advisor_position_import_history(session)
    records = await _load_advisor_position_import_history(
        session,
        page=resolved_page,
        page_size=resolved_page_size,
    )
    pages = math.ceil(total / resolved_page_size) if total > 0 else 0
    return AdvisorPositionImportHistoryResponse(
        items=[AdvisorPositionImportHistoryItem.model_validate(_serialize_position_import_record(record)) for record in records],
        total=total,
        page=resolved_page,
        page_size=resolved_page_size,
        pages=pages,
    )


@router.post(
    "/positions/import-history/{import_id}/restore",
    response_model=AdvisorPositionImportRestoreResponse,
    summary="从导入历史恢复持仓快照",
    description="按某次导入历史中保存的成功持仓快照，完整替换当前 Advisor 持仓。",
)
async def restore_advisor_positions_from_import_history(
    import_id: int,
    session: AsyncSession = Depends(get_session),
) -> AdvisorPositionImportRestoreResponse:
    record = await _get_advisor_position_import_record(session, import_id)
    if record is None:
        raise HTTPException(status_code=404, detail="导入历史不存在")

    raw_positions = list(getattr(record, "positions_json", None) or [])
    if not raw_positions:
        raise HTTPException(status_code=400, detail="该导入历史没有可恢复的持仓快照")

    positions = [AdvisorHoldingImportPosition.model_validate(item) for item in raw_positions]
    restored_records = await _replace_persisted_advisor_positions(
        session,
        positions,
        source="import_restore",
        metadata={
            "restored_from_import_id": int(getattr(record, "id")),
            "restored_from_filename": str(getattr(record, "filename", "") or ""),
            "restored_from_created_at": _iso_datetime(getattr(record, "created_at", None)),
            "restored_via": "positions_import_history_restore_api",
            "governance_summary": (getattr(record, "metadata_json", None) or {}).get("governance_summary"),
        },
    )
    restored_positions = (
        [AdvisorHoldingImportPosition.model_validate(_serialize_holding_position(item)) for item in restored_records]
        if restored_records is not None
        else _normalize_positions_payload(positions)
    )
    return AdvisorPositionImportRestoreResponse(
        status="restored" if restored_records is not None else "unavailable",
        total=len(restored_positions),
        positions=restored_positions,
        restored_from=AdvisorPositionImportHistoryItem.model_validate(_serialize_position_import_record(record)),
    )


@router.post(
    "/positions/import",
    response_model=AdvisorHoldingImportResponse,
    summary="导入当前持仓",
    description="上传 CSV 持仓文件，返回标准化持仓列表与逐行错误，并将成功行保存为当前服务端持仓快照。",
)
async def import_advisor_positions(
    file: UploadFile = File(..., description="持仓 CSV 文件"),
    session: AsyncSession = Depends(get_session),
) -> AdvisorHoldingImportResponse:
    filename = file.filename or "positions.csv"
    file_format = (filename.rsplit(".", 1)[-1] if "." in filename else "csv").lower()
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="导入文件为空")
    try:
        rows = _parse_holding_import_file(filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = _build_holding_import_response(filename, rows)
    if response.total_rows == 0:
        raise HTTPException(status_code=400, detail="未识别到有效持仓数据")

    replaced_count = 0
    if response.positions:
        replaced_records = await _replace_persisted_advisor_positions(
            session,
            response.positions,
            source="import",
            metadata={
                "import_filename": filename,
                "failed_count": response.failed_count,
                "total_rows": response.total_rows,
                "governance_summary": response.governance_summary.model_dump(),
            },
        )
        replaced_count = len(replaced_records) if replaced_records is not None else len(response.positions)

    await _create_advisor_position_import_history(
        session,
        filename=filename,
        file_format=file_format,
        status="failed" if response.imported_count == 0 else response.status,
        total_rows=response.total_rows,
        imported_count=response.imported_count,
        failed_count=response.failed_count,
        replaced_position_count=replaced_count,
        rows=[row.model_dump() for row in response.rows],
        positions=[position.model_dump() for position in response.positions],
        metadata={
            "content_type": file.content_type,
            "import_source": "advisor_positions_import_api",
            "governance_summary": response.governance_summary.model_dump(),
        },
    )
    return response


def _generate_summary(advices: list) -> dict[str, Any]:
    """生成建议摘要。"""
    buy_count = sum(1 for a in advices if a.action == "buy")
    sell_count = sum(1 for a in advices if a.action == "sell")
    hold_count = sum(1 for a in advices if a.action == "hold")
    watch_count = sum(1 for a in advices if a.action == "watch")
    total_buy_amount = sum(a.suggested_amount for a in advices if a.action == "buy")
    total_sell_amount = sum(a.suggested_amount for a in advices if a.action == "sell")

    high_confidence = [a for a in advices if a.confidence > 0.6]

    return {
        "buy_count": buy_count,
        "sell_count": sell_count,
        "hold_count": hold_count,
        "watch_count": watch_count,
        "total_buy_amount": round(total_buy_amount, 2),
        "total_sell_amount": round(total_sell_amount, 2),
        "high_confidence_signals": len(high_confidence),
        "top_buy": (
            advices[0].fund_code
            if advices and advices[0].action == "buy"
            else None
        ),
        "top_sell": next(
            (a.fund_code for a in advices if a.action == "sell"), None
        ),
    }


# ---------------------------------------------------------------------------
# 建议引擎自我检验端点
# ---------------------------------------------------------------------------


class AdvisorBacktestRequest(BaseModel):
    """建议引擎回测验证请求。"""

    fund_code: str = Field(..., description="基金代码")
    lookback_days: int | None = Field(
        default=None, ge=300,
        description=(
            "加载历史数据天数。None 或 0 表示使用全部可用数据（推荐），"
            "系统会自动获取基金上市以来的所有净值，确保信号样本量充足。"
        ),
    )
    rebalance_freq: int = Field(
        default=5, ge=1, le=20,
        description="模拟调仓频率（天）",
    )
    risk_level: str = Field(default="moderate", description="风险偏好")


@router.post(
    "/backtest",
    summary="建议引擎历史验证",
    description=(
        "对交易建议引擎进行历史回测验证：在历史每个调仓日运行引擎，"
        "统计建议后的实际收益和命中率。用于评估引擎信号的有效性。"
        "注意：这是样本内验证，不能证明未来有效。"
    ),
)
async def advisor_backtest(
    request: AdvisorBacktestRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """运行建议引擎的历史回测验证。"""
    from app.services.advisor_backtest import load_and_run_advisor_backtest

    config = build_advisor_config(request.risk_level)

    result = await load_and_run_advisor_backtest(
        fund_code=request.fund_code,
        session=session,
        lookback_days=request.lookback_days if request.lookback_days else None,
        rebalance_freq=request.rebalance_freq,
        config=config,
    )

    return result.to_dict()


class WalkForwardRequest(BaseModel):
    """Walk-Forward 样本外验证请求。"""

    fund_code: str = Field(..., description="基金代码")
    lookback_days: int | None = Field(
        default=None, ge=400,
        description=(
            "加载历史数据天数。None 或 0 表示使用全部可用数据（推荐），"
            "系统会自动获取基金上市以来的所有净值。"
        ),
    )
    n_folds: int = Field(
        default=5, ge=3, le=10,
        description="折叠数（默认5）",
    )
    rebalance_freq: int = Field(
        default=5, ge=1, le=20,
        description="调仓频率（天）",
    )
    risk_level: str = Field(default="moderate", description="风险偏好")
    async_mode: bool = Field(
        default=False,
        description="是否异步执行（通过 Celery 后台任务）。数据量大时建议开启。",
    )


@router.post(
    "/walk-forward",
    summary="Walk-Forward 样本外验证",
    description=(
        "对交易建议引擎进行 Walk-Forward 样本外验证：将数据严格分为训练期和测试期，"
        "测试期的指标为真正的样本外表现。用于检测引擎是否存在过拟合。"
        "IC 衰减率 < 0.5 表示严重过拟合。"
        "数据量大时可设置 async_mode=true 通过后台任务执行。"
    ),
)
async def walk_forward_validation(
    request: WalkForwardRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """运行 Walk-Forward 样本外验证。"""
    from app.services.advisor_backtest import load_and_run_walk_forward

    config = build_advisor_config(request.risk_level)

    # lookback_days=None 或 0 表示使用全部可用数据
    lookback_days = request.lookback_days if request.lookback_days else None

    if request.async_mode:
        # 异步模式：提交 Celery 任务
        from app.tasks.advisor import run_walk_forward_task
        task = run_walk_forward_task.delay(
            fund_code=request.fund_code,
            lookback_days=lookback_days,
            n_folds=request.n_folds,
            rebalance_freq=request.rebalance_freq,
            risk_level=request.risk_level,
        )
        return {
            "status": "submitted",
            "task_id": task.id,
            "message": "Walk-Forward 验证已提交后台执行，请稍后查询结果",
        }

    # 同步模式：直接执行
    result = await load_and_run_walk_forward(
        fund_code=request.fund_code,
        session=session,
        lookback_days=lookback_days,
        n_folds=request.n_folds,
        rebalance_freq=request.rebalance_freq,
        config=config,
        risk_level=request.risk_level,
    )

    return result.to_dict()


# ---------------------------------------------------------------------------
# 建议执行跟踪 + 引擎健康度
# ---------------------------------------------------------------------------


@router.get(
    "/history/{result_id}/performance",
    summary="获取建议执行效果",
    description=(
        "获取某条历史建议的实际执行效果：建议后 5/10/20/60 日的实际收益、"
        "命中率等。需要等待跟踪任务运行后才有数据。"
    ),
)
async def get_advice_performance(
    result_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """获取建议执行效果。"""
    from sqlalchemy import select

    from app.data.models.advisor_results import AdvisorResult
    from app.services.advisor_execution_records import (
        attach_execution_attribution,
        load_execution_records_for_result,
        summarize_execution_records,
    )

    result = await session.execute(
        select(AdvisorResult).where(AdvisorResult.id == result_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="建议记录不存在")

    execution_records = await load_execution_records_for_result(session, result_id)
    execution_summary = summarize_execution_records(row.advices, execution_records)

    if not row.tracked_returns:
        return {
            "id": row.id,
            "advice_date": str(row.advice_date),
            "status": "pending",
            "message": "尚未完成跟踪，请等待每日 23:00 跟踪任务运行",
            "tracked_returns": None,
            "summary": None,
            "execution_summary": execution_summary,
        }

    # 计算汇总统计
    tracked = row.tracked_returns
    buy_hits_20d = []
    sell_hits_20d = []
    buy_returns_20d = []
    sell_returns_20d = []

    for code, data in tracked.items():
        action = data.get("action")
        hit_20d = data.get("hit_20d")
        ret_20d = data.get("return_20d")

        if hit_20d is not None:
            if action == "buy":
                buy_hits_20d.append(hit_20d)
            elif action == "sell":
                sell_hits_20d.append(hit_20d)

        if ret_20d is not None:
            if action == "buy":
                buy_returns_20d.append(ret_20d)
            elif action == "sell":
                sell_returns_20d.append(ret_20d)

    summary = {
        "buy_hit_rate_20d": (
            round(sum(1 for h in buy_hits_20d if h) / len(buy_hits_20d), 4)
            if buy_hits_20d else None
        ),
        "sell_hit_rate_20d": (
            round(sum(1 for h in sell_hits_20d if h) / len(sell_hits_20d), 4)
            if sell_hits_20d else None
        ),
        "buy_avg_return_20d": (
            round(sum(buy_returns_20d) / len(buy_returns_20d), 6)
            if buy_returns_20d else None
        ),
        "sell_avg_return_20d": (
            round(sum(sell_returns_20d) / len(sell_returns_20d), 6)
            if sell_returns_20d else None
        ),
        "total_tracked": len(tracked),
        "buy_count": len(buy_hits_20d),
        "sell_count": len(sell_hits_20d),
    }

    tracked_with_execution = attach_execution_attribution(
        tracked,
        row.advices,
        execution_records,
    )
    if tracked_with_execution:
        for _, item in tracked_with_execution.items():
            item["evaluation_label"] = _classify_performance_label(
                item.get("action"),
                item.get("return_20d"),
                item.get("hit_20d"),
            )

    label_counts = {
        "effective": sum(1 for item in (tracked_with_execution or {}).values() if item.get("evaluation_label") == "effective"),
        "neutral": sum(1 for item in (tracked_with_execution or {}).values() if item.get("evaluation_label") == "neutral"),
        "ineffective": sum(1 for item in (tracked_with_execution or {}).values() if item.get("evaluation_label") == "ineffective"),
        "not_evaluable": sum(1 for item in (tracked_with_execution or {}).values() if item.get("evaluation_label") == "not_evaluable"),
    }
    summary["evaluation_labels"] = label_counts

    return {
        "id": row.id,
        "advice_date": str(row.advice_date),
        "status": "tracked",
        "tracked_at": row.tracked_at.isoformat() if row.tracked_at else None,
        "tracked_returns": tracked_with_execution,
        "summary": summary,
        "execution_summary": execution_summary,
    }


@router.get(
    "/health",
    summary="引擎健康度",
    description=(
        "返回交易建议引擎的健康度指标：滚动 IC、命中率趋势、IC 衰减检测。"
        "用于判断引擎信号是否仍然有效。"
    ),
)
async def get_engine_health(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """获取引擎健康度指标。"""
    from app.services.advisor_tracking import compute_engine_health_async

    metrics = await compute_engine_health_async(session)
    payload = metrics.to_dict()
    try:
        from app.services.runtime_health import check_queue_health

        payload["runtime_health"] = {"queue": check_queue_health().to_dict()}
    except Exception as exc:
        payload["runtime_health"] = {
            "queue": {
                "status": "unknown",
                "redis_available": False,
                "broker_url_configured": False,
                "queues": {},
                "warnings": ["运行时队列健康检查不可用"],
                "error": str(exc),
            }
        }
    return payload


@router.post(
    "/oos-refresh",
    summary="手动触发样本外缓存刷新",
    description="按 nightly 默认参数提交一轮 OOS Walk-Forward 缓存刷新后台任务。",
)
async def trigger_oos_cache_refresh() -> dict[str, Any]:
    """手动触发 nightly OOS 缓存刷新。"""
    from app.tasks.advisor import refresh_oos_validation_cache

    nightly_config = _get_nightly_oos_refresh_config()
    task = refresh_oos_validation_cache.delay(
        risk_level=nightly_config["risk_level"],
        lookback_days=nightly_config["lookback_days"],
        n_folds=nightly_config["n_folds"],
        rebalance_freq=nightly_config["rebalance_freq"],
        max_funds=nightly_config["max_funds"],
        max_age_days=nightly_config["max_age_days"],
        dispatch_every_n=nightly_config["dispatch_every_n"],
        dispatch_countdown_step=nightly_config["dispatch_countdown_step"],
    )
    return {
        "status": "submitted",
        "task_id": task.id,
        "message": "已提交一轮 OOS 缓存后台刷新任务",
        "config": nightly_config,
    }


@router.get(
    "/oos-status",
    summary="查看样本外缓存状态",
    description=(
        "返回交易建议当前活跃基金池的 Walk-Forward 样本外缓存覆盖率、"
        "各风险档命中情况以及 nightly 自动刷新配置。"
    ),
)
async def get_oos_cache_status(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """获取样本外验证缓存状态。"""
    from app.services.advisor_oos import DEFAULT_RISK_LEVEL, OOSValidationStore

    nightly_config = _get_nightly_oos_refresh_config()
    fund_codes = await _load_active_advisor_fund_codes(session)
    all_snapshots = OOSValidationStore.load_all()
    risk_levels = ["conservative", DEFAULT_RISK_LEVEL, "aggressive"]

    coverage: dict[str, Any] = {}
    latest_updates: list[str] = []
    total_funds = len(fund_codes)

    for risk_level in risk_levels:
        exact_count = 0
        resolved_count = 0
        fallback_to_moderate = 0
        fallback_to_latest = 0
        stale_count = 0

        for fund_code in fund_codes:
            exact_snapshot = all_snapshots.get(fund_code, {}).get(risk_level)
            if exact_snapshot is not None:
                exact_count += 1

            resolved_snapshot = OOSValidationStore.load(fund_code, risk_level=risk_level)
            if resolved_snapshot is None:
                continue

            resolved_count += 1
            latest_updates.append(str(resolved_snapshot.updated_at or ""))
            selection_source = getattr(resolved_snapshot, "selection_source", None)
            if selection_source == "moderate_fallback":
                fallback_to_moderate += 1
            elif selection_source == "latest_fallback":
                fallback_to_latest += 1

            if OOSValidationStore.is_stale(resolved_snapshot, max_age_days=1):
                stale_count += 1

        coverage[risk_level] = {
            "exact_count": exact_count,
            "resolved_count": resolved_count,
            "missing_count": max(0, total_funds - resolved_count),
            "fallback_to_moderate": fallback_to_moderate,
            "fallback_to_latest": fallback_to_latest,
            "stale_count": stale_count,
            "exact_coverage_pct": round(exact_count / total_funds, 4) if total_funds else None,
            "resolved_coverage_pct": round(resolved_count / total_funds, 4) if total_funds else None,
        }

    latest_update = max((value for value in latest_updates if value), default=None)

    return {
        "date": date.today().isoformat(),
        "total_active_funds": total_funds,
        "latest_snapshot_update": latest_update,
        "nightly_refresh": {
            "schedule": nightly_config["schedule"],
            "risk_level": nightly_config["risk_level"],
            "lookback_days": nightly_config["lookback_days"],
            "n_folds": nightly_config["n_folds"],
            "rebalance_freq": nightly_config["rebalance_freq"],
            "max_funds": nightly_config["max_funds"],
            "max_age_days": nightly_config["max_age_days"],
            "dispatch_every_n": nightly_config["dispatch_every_n"],
            "dispatch_countdown_step": nightly_config["dispatch_countdown_step"],
        },
        "coverage": coverage,
        "fund_codes_sample": fund_codes[:10],
    }


# ---------------------------------------------------------------------------
# v4: 截面因子选基端点
# ---------------------------------------------------------------------------


class CrossSectionalRequest(BaseModel):
    """截面因子选基请求。"""

    fund_type: str | None = Field(
        None,
        description="基金类型过滤: stock/bond/mixed/index/qdii/fof（None=全部）",
    )
    min_history_days: int = Field(
        252,
        ge=120,
        le=1000,
        description="最少历史天数要求",
    )
    top_n: int = Field(
        10,
        ge=1,
        le=50,
        description="返回 Top N 基金",
    )


@router.post(
    "/cross-sectional",
    summary="截面因子选基",
    description=(
        "基于截面因子模型对同类基金进行排序，选出相对最优的基金。"
        "不预测绝对涨跌，只预测相对优劣。"
        "因子包括：Alpha持续性、Sharpe持续性、规模、费率、回撤恢复、一致性。"
    ),
)
async def cross_sectional_scoring(
    request: CrossSectionalRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """运行截面因子选基评分。"""
    from app.services.cross_sectional_scorer import (
        CrossSectionalConfig,
        load_fund_data_for_scoring,
        run_cross_sectional_scoring,
    )

    config = CrossSectionalConfig(
        min_history_days=request.min_history_days,
    )

    # 加载基金数据
    fund_data = await load_fund_data_for_scoring(
        session,
        fund_type=request.fund_type,
        min_history_days=request.min_history_days,
    )

    if not fund_data:
        return {
            "status": "no_data",
            "message": "未找到符合条件的基金数据",
            "fund_type": request.fund_type,
        }

    # 运行截面评分
    result = run_cross_sectional_scoring(fund_data, config)

    # 限制返回数量
    response = result.to_dict()
    response["top_funds"] = response["top_funds"][:request.top_n]

    return response


@router.post(
    "/cross-sectional/ic",
    summary="截面因子 IC 验证",
    description=(
        "计算各截面因子的 IC（信息系数），验证因子的预测力。"
        "截面 IC = 同一时点多只基金的因子排序与未来收益排序的相关性。"
        "IC > 0.03 表示因子有效，IC > 0.05 表示因子较强。"
    ),
)
async def cross_sectional_ic_validation(
    request: CrossSectionalRequest,
    forward_days: int = Query(20, ge=5, le=60, description="前瞻天数"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """计算截面因子 IC。"""
    from app.services.cross_sectional_scorer import (
        CrossSectionalConfig,
        compute_cross_sectional_ic,
        load_fund_data_for_scoring,
    )

    config = CrossSectionalConfig(
        min_history_days=request.min_history_days,
    )

    # 加载基金数据（需要更长历史用于 IC 计算）
    fund_data = await load_fund_data_for_scoring(
        session,
        fund_type=request.fund_type,
        min_history_days=request.min_history_days + forward_days + 60,
    )

    if not fund_data:
        return {
            "status": "no_data",
            "message": "未找到符合条件的基金数据",
        }

    # 计算截面 IC
    ics = compute_cross_sectional_ic(fund_data, forward_days, config)

    # 解读
    interpretation = []
    for factor, ic_val in ics.items():
        if factor in ("n_funds", "error"):
            continue
        if ic_val is None:
            interpretation.append(f"{factor}: 无法计算")
        elif ic_val > 0.05:
            interpretation.append(f"{factor}: IC={ic_val:.4f} ✓ 因子有效")
        elif ic_val > 0.02:
            interpretation.append(f"{factor}: IC={ic_val:.4f} ~ 因子微弱")
        elif ic_val > 0:
            interpretation.append(f"{factor}: IC={ic_val:.4f} △ 因子极弱")
        else:
            interpretation.append(f"{factor}: IC={ic_val:.4f} ✗ 因子无效")

    return {
        "fund_type": request.fund_type,
        "forward_days": forward_days,
        "n_funds": ics.get("n_funds"),
        "factor_ics": {k: v for k, v in ics.items() if k != "n_funds"},
        "interpretation": interpretation,
        "methodology": (
            "截面 IC = Spearman(因子值排序, 未来收益排序)。"
            "在同一时点对多只基金计算，IC > 0.03 表示因子有预测力。"
            "与时序 IC 不同，截面 IC 不需要预测绝对涨跌，只需预测相对排序。"
        ),
    }
