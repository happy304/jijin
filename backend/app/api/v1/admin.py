"""Administrative maintenance API endpoints.

These endpoints trigger operational tasks such as historical NAV
recalculation. They intentionally dispatch Celery jobs instead of doing heavy
work in the request thread.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from app.core.config import Settings, get_settings
from pydantic import BaseModel, Field, model_validator

router = APIRouter(prefix="/admin", tags=["admin"])


async def require_admin_access(
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """Lightweight guard for local administrative maintenance endpoints."""
    if not settings.admin_api_enabled:
        raise HTTPException(status_code=403, detail="Admin API is disabled")
    if settings.admin_api_token and x_admin_token != settings.admin_api_token:
        raise HTTPException(status_code=403, detail="Invalid admin token")


class RecalculateAdjNavRequest(BaseModel):
    """Request body for historical adjusted NAV recalculation."""

    fund_code: str | None = Field(
        default=None,
        description="Single fund code to recalculate. Mutually exclusive with fund_codes.",
    )
    fund_codes: list[str] | None = Field(
        default=None,
        description="Specific fund codes to recalculate. Omit both fund_code and fund_codes to process all active funds.",
    )
    invalidate_cache: bool = Field(
        default=True,
        description="Whether to invalidate NAV cache after recalculation.",
    )
    mark_stale_results: bool = Field(
        default=True,
        description="Whether to mark dependent Advisor/backtest/simulation results as stale when NAV changes.",
    )

    @model_validator(mode="after")
    def _normalize_codes(self) -> "RecalculateAdjNavRequest":
        if self.fund_codes is not None:
            self.fund_codes = [str(code).strip() for code in self.fund_codes if str(code).strip()]
        if self.fund_code is not None:
            self.fund_code = self.fund_code.strip()
        return self


class RecalculateAdjNavResponse(BaseModel):
    """Response returned after dispatching the recalculation task."""

    status: str = Field(default="submitted", description="Dispatch status")
    task_id: str | None = Field(default=None, description="Celery task id")
    task_name: str = Field(..., description="Celery task name")
    message: str = Field(..., description="Human-readable dispatch message")
    params: dict[str, Any] = Field(..., description="Submitted task parameters")


@router.post(
    "/nav/recalculate-adj-nav",
    response_model=RecalculateAdjNavResponse,
    status_code=202,
    summary="触发历史复权净值重算",
    description="派发后台任务重算 adj_nav 和 daily_return，并可标记依赖旧 NAV 的结果为过期。",
)
async def trigger_recalculate_adj_nav(
    body: RecalculateAdjNavRequest,
    _: None = Depends(require_admin_access),
) -> RecalculateAdjNavResponse:
    """Dispatch historical adjusted NAV recalculation as a Celery task."""
    if body.fund_code and body.fund_codes:
        raise HTTPException(status_code=422, detail="fund_code and fund_codes are mutually exclusive")
    if body.fund_code is not None and not body.fund_code:
        raise HTTPException(status_code=422, detail="fund_code must not be blank when provided")
    if body.fund_codes is not None and not body.fund_codes:
        raise HTTPException(status_code=422, detail="fund_codes must not be empty when provided")

    try:
        from app.tasks.ingest import recalculate_adj_nav_history

        async_result = recalculate_adj_nav_history.delay(
            fund_code=body.fund_code,
            fund_codes=body.fund_codes,
            invalidate_cache=body.invalidate_cache,
            mark_stale_results=body.mark_stale_results,
        )
    except Exception as exc:  # noqa: BLE001 - convert dispatcher failures to HTTP errors
        raise HTTPException(status_code=503, detail=f"无法派发复权净值重算任务: {exc}") from exc

    params = {
        "fund_code": body.fund_code,
        "fund_codes": body.fund_codes,
        "invalidate_cache": body.invalidate_cache,
        "mark_stale_results": body.mark_stale_results,
    }
    return RecalculateAdjNavResponse(
        task_id=getattr(async_result, "id", None),
        task_name="app.tasks.ingest.recalculate_adj_nav_history",
        message="历史复权净值重算任务已提交",
        params=params,
    )
