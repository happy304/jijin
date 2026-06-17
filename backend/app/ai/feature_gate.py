"""AI feature gate — dependency that blocks AI endpoints when disabled.

When the global configuration `AI_ENABLED` is set to False, all AI API
endpoints return HTTP 501 (Not Implemented). Core platform functionality
(data, factors, backtests, strategies, risk) remains fully operational.

This satisfies requirements 11.24 and 11.26.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.core.config import Settings, get_settings


def require_ai_enabled(
    settings: Settings = Depends(get_settings),
) -> None:
    """FastAPI dependency that raises 501 when AI is disabled.

    Add this as a dependency to any AI router or endpoint to enforce
    the global AI kill switch.

    Raises:
        HTTPException: 501 Not Implemented when AI_ENABLED is False.
    """
    if not settings.ai_enabled:
        raise HTTPException(
            status_code=501,
            detail="AI 功能已关闭。平台核心功能（数据、因子、回测、策略、风控）仍可正常使用。",
        )
