"""WebSocket endpoints package."""

from __future__ import annotations

from app.api.v1.ws.backtest_progress import router as backtest_ws_router

__all__ = ["backtest_ws_router"]
