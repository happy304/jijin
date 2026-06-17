"""系统设置 API 端点。

提供 AI 配置的读取和更新功能。
修改配置后会写入 .env 文件，需要重启后端服务生效。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.tasks.schedule import BEAT_SCHEDULE, get_beat_schedule

log = get_logger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

# .env 文件路径
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DOTENV_PATH = _REPO_ROOT / ".env"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AIConfig(BaseModel):
    """AI 配置。"""

    ai_enabled: bool = Field(False, description="是否启用 AI 功能")
    ai_default_provider: str = Field("openai_compat", description="默认 AI 提供商")
    openai_api_key: str = Field("", description="OpenAI 兼容 API Key")
    openai_base_url: str = Field("https://api.openai.com/v1", description="OpenAI 兼容 Base URL")
    openai_model: str = Field("gpt-4o-mini", description="OpenAI 兼容模型名称")
    anthropic_api_key: str = Field("", description="Anthropic API Key")
    anthropic_model: str = Field("claude-3-5-sonnet-latest", description="Anthropic 模型名称")
    llm_daily_budget_usd: float = Field(10.0, description="每日预算（USD）")
    llm_monthly_budget_usd: float = Field(200.0, description="每月预算（USD）")


class AIConfigUpdate(BaseModel):
    """AI 配置更新请求。"""

    ai_enabled: bool | None = None
    ai_default_provider: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    llm_daily_budget_usd: float | None = None
    llm_monthly_budget_usd: float | None = None


class AIConfigResponse(BaseModel):
    """AI 配置响应（隐藏 key 中间部分）。"""

    ai_enabled: bool
    ai_default_provider: str
    openai_api_key_masked: str
    openai_base_url: str
    openai_model: str
    anthropic_api_key_masked: str
    anthropic_model: str
    llm_daily_budget_usd: float
    llm_monthly_budget_usd: float


class ScheduleTaskProfile(BaseModel):
    """调度任务暴露信息。"""

    name: str
    task: str
    queue: str | None = None
    enabled: bool


class FeatureProfileResponse(BaseModel):
    """个人化功能开关配置。"""

    personal_mode: bool
    feature_ai: bool
    feature_advisor_governance: bool
    feature_full_monitoring: bool
    schedule_mode: str
    schedule_enabled_tasks: list[ScheduleTaskProfile]
    schedule_disabled_tasks: list[ScheduleTaskProfile]


class FeatureProfileUpdate(BaseModel):
    """个人化功能开关更新请求。"""

    personal_mode: bool | None = None
    feature_ai: bool | None = None
    feature_advisor_governance: bool | None = None
    feature_full_monitoring: bool | None = None
    schedule_mode: str | None = Field(None, pattern="^(light|research|full)$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask_key(key: str) -> str:
    """将 API key 中间部分替换为 ***。"""
    if not key or len(key) < 8:
        return "***" if key else ""
    return key[:4] + "***" + key[-4:]


def _read_env() -> dict[str, str]:
    """读取 .env 文件为 key=value 字典。"""
    result: dict[str, str] = {}
    if not _DOTENV_PATH.exists():
        return result
    for line in _DOTENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def _update_env(updates: dict[str, str]) -> None:
    """更新 .env 文件中的指定键值对，保留注释和格式。"""
    if not _DOTENV_PATH.exists():
        raise HTTPException(status_code=500, detail=".env 文件不存在")

    content = _DOTENV_PATH.read_text(encoding="utf-8")
    lines = content.splitlines()
    updated_keys: set[str] = set()

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                lines[i] = f"{key}={updates[key]}"
                updated_keys.add(key)

    # 如果有新的 key 不在文件中，追加到末尾
    for key, value in updates.items():
        if key not in updated_keys:
            lines.append(f"{key}={value}")

    _DOTENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _schedule_task_profile(name: str, entry: dict[str, Any], enabled: bool) -> ScheduleTaskProfile:
    """将 Celery Beat 配置转换为前端可展示的任务摘要。"""
    options = entry.get("options")
    queue = options.get("queue") if isinstance(options, dict) else None
    return ScheduleTaskProfile(
        name=name,
        task=str(entry.get("task", "")),
        queue=str(queue) if queue else None,
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/features",
    response_model=FeatureProfileResponse,
    summary="获取个人化功能开关",
    description="返回个人模式、AI、高级治理、完整监控和调度模式等功能开关。",
)
async def get_feature_profile() -> FeatureProfileResponse:
    """读取当前个人化功能开关。"""
    settings = get_settings()
    enabled_schedule = get_beat_schedule(settings.schedule_mode)
    enabled_names = set(enabled_schedule)
    return FeatureProfileResponse(
        personal_mode=settings.personal_mode,
        feature_ai=settings.feature_ai,
        feature_advisor_governance=settings.feature_advisor_governance,
        feature_full_monitoring=settings.feature_full_monitoring,
        schedule_mode=settings.schedule_mode,
        schedule_enabled_tasks=[
            _schedule_task_profile(name, entry, enabled=True)
            for name, entry in sorted(enabled_schedule.items())
        ],
        schedule_disabled_tasks=[
            _schedule_task_profile(name, entry, enabled=False)
            for name, entry in sorted(BEAT_SCHEDULE.items())
            if name not in enabled_names
        ],
    )


@router.post(
    "/features",
    response_model=FeatureProfileResponse,
    summary="更新个人化功能开关",
    description="写入 .env 并刷新后端设置缓存；部分调度变更需要重启 Celery Beat 才完全生效。",
)
@router.put(
    "/features",
    response_model=FeatureProfileResponse,
    summary="更新个人化功能开关",
    description="写入 .env 并刷新后端设置缓存；部分调度变更需要重启 Celery Beat 才完全生效。",
)
async def update_feature_profile(body: FeatureProfileUpdate) -> FeatureProfileResponse:
    """更新个人化功能开关。"""
    updates: dict[str, str] = {}
    if body.personal_mode is not None:
        updates["PERSONAL_MODE"] = str(body.personal_mode).lower()
    if body.feature_ai is not None:
        updates["FEATURE_AI"] = str(body.feature_ai).lower()
    if body.feature_advisor_governance is not None:
        updates["FEATURE_ADVISOR_GOVERNANCE"] = str(body.feature_advisor_governance).lower()
    if body.feature_full_monitoring is not None:
        updates["FEATURE_FULL_MONITORING"] = str(body.feature_full_monitoring).lower()
    if body.schedule_mode is not None:
        updates["SCHEDULE_MODE"] = body.schedule_mode

    if updates:
        _update_env(updates)
        get_settings.cache_clear()
        log.info("settings.feature_profile.updated", keys=list(updates.keys()))

    return await get_feature_profile()


@router.get(
    "/ai",
    response_model=AIConfigResponse,
    summary="获取 AI 配置",
    description="返回当前 AI 配置（API Key 已脱敏）。",
)
async def get_ai_config() -> AIConfigResponse:
    """读取当前 AI 配置。"""
    env = _read_env()
    settings = get_settings()

    return AIConfigResponse(
        ai_enabled=env.get("AI_ENABLED", "false").lower() == "true",
        ai_default_provider=env.get("AI_DEFAULT_PROVIDER", settings.ai_default_provider),
        openai_api_key_masked=_mask_key(env.get("OPENAI_API_KEY", "")),
        openai_base_url=env.get("OPENAI_BASE_URL", settings.openai_base_url),
        openai_model=env.get("OPENAI_MODEL", settings.openai_model),
        anthropic_api_key_masked=_mask_key(env.get("ANTHROPIC_API_KEY", "")),
        anthropic_model=env.get("ANTHROPIC_MODEL", settings.anthropic_model),
        llm_daily_budget_usd=float(env.get("LLM_DAILY_BUDGET_USD", "10")),
        llm_monthly_budget_usd=float(env.get("LLM_MONTHLY_BUDGET_USD", "200")),
    )


@router.put(
    "/ai",
    response_model=AIConfigResponse,
    summary="更新 AI 配置",
    description="更新 AI 配置并写入 .env 文件。需要重启后端服务生效。",
)
async def update_ai_config(body: AIConfigUpdate) -> AIConfigResponse:
    """更新 AI 配置。"""
    updates: dict[str, str] = {}

    if body.ai_enabled is not None:
        updates["AI_ENABLED"] = str(body.ai_enabled).lower()
    if body.ai_default_provider is not None:
        updates["AI_DEFAULT_PROVIDER"] = body.ai_default_provider
    if body.openai_api_key is not None:
        updates["OPENAI_API_KEY"] = body.openai_api_key
    if body.openai_base_url is not None:
        updates["OPENAI_BASE_URL"] = body.openai_base_url
    if body.openai_model is not None:
        updates["OPENAI_MODEL"] = body.openai_model
    if body.anthropic_api_key is not None:
        updates["ANTHROPIC_API_KEY"] = body.anthropic_api_key
    if body.anthropic_model is not None:
        updates["ANTHROPIC_MODEL"] = body.anthropic_model
    if body.llm_daily_budget_usd is not None:
        updates["LLM_DAILY_BUDGET_USD"] = str(body.llm_daily_budget_usd)
    if body.llm_monthly_budget_usd is not None:
        updates["LLM_MONTHLY_BUDGET_USD"] = str(body.llm_monthly_budget_usd)

    if updates:
        _update_env(updates)
        log.info("settings.ai_config.updated", keys=list(updates.keys()))

    return await get_ai_config()
