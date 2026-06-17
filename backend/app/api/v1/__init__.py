"""Version 1 of the HTTP API.

This package aggregates the meta, funds, factors, strategies, backtests,
alerts, AI, and discovery endpoints, including the WebSocket progress
endpoint for backtests.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.advisor import router as advisor_router
from app.api.v1.ai import router as ai_router
from app.api.v1.alerts import router as alerts_router
from app.api.v1.backtests import router as backtests_router
from app.api.v1.discovery import router as discovery_router
from app.api.v1.factors import router as factors_router
from app.api.v1.funds import router as funds_router
from app.api.v1.holdings import router as holdings_router
from app.api.v1.meta import router as meta_router
from app.api.v1.settings import router as settings_router
from app.api.v1.simulations import router as simulations_router
from app.api.v1.strategies import router as strategies_router
from app.api.v1.ws import backtest_ws_router

router = APIRouter()
router.include_router(meta_router)
router.include_router(funds_router)
router.include_router(factors_router)
router.include_router(strategies_router)
router.include_router(backtests_router)
router.include_router(backtest_ws_router)
router.include_router(alerts_router)
router.include_router(ai_router)
router.include_router(discovery_router)
router.include_router(holdings_router)
router.include_router(settings_router)
router.include_router(simulations_router)
router.include_router(advisor_router)
router.include_router(admin_router)

__all__ = ["router"]
