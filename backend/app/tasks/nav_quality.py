"""Helpers for auditing NAV source usage in async tasks.

The backtest and simulation tasks prefer ``adj_nav`` but may fall back to
``unit_nav`` when adjusted NAV is missing. These helpers keep that fallback
visible in persisted metrics so users can judge whether results are based on a
consistent total-return NAV口径.
"""

from __future__ import annotations

from datetime import date
from typing import Any


def new_nav_source_stats() -> dict[str, Any]:
    """Return an empty mutable NAV source usage counter."""
    return {
        "total_points": 0,
        "adj_nav_points": 0,
        "unit_nav_fallback_points": 0,
        "first_fallback_date": None,
        "last_fallback_date": None,
    }


def record_nav_source_usage(
    stats: dict[str, Any],
    trade_date: date,
    *,
    used_adj_nav: bool,
) -> None:
    """Update NAV source usage stats for one valid NAV observation."""
    stats["total_points"] += 1
    if used_adj_nav:
        stats["adj_nav_points"] += 1
        return

    stats["unit_nav_fallback_points"] += 1
    first = stats.get("first_fallback_date")
    last = stats.get("last_fallback_date")
    if first is None or trade_date < first:
        stats["first_fallback_date"] = trade_date
    if last is None or trade_date > last:
        stats["last_fallback_date"] = trade_date


def build_nav_quality_warning(
    stats_by_fund: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Build a JSON-serialisable warning when any fund used unit_nav fallback.

    Returns ``None`` when every valid NAV point used ``adj_nav``.
    """
    funds: dict[str, dict[str, Any]] = {}
    for fund_code, stats in stats_by_fund.items():
        fallback_points = int(stats.get("unit_nav_fallback_points") or 0)
        if fallback_points <= 0:
            continue

        total_points = int(stats.get("total_points") or 0)
        first_fallback = stats.get("first_fallback_date")
        last_fallback = stats.get("last_fallback_date")
        funds[fund_code] = {
            "total_points": total_points,
            "adj_nav_points": int(stats.get("adj_nav_points") or 0),
            "unit_nav_fallback_points": fallback_points,
            "unit_nav_fallback_ratio": round(
                fallback_points / total_points if total_points > 0 else 0.0,
                6,
            ),
            "first_fallback_date": first_fallback.isoformat() if first_fallback else None,
            "last_fallback_date": last_fallback.isoformat() if last_fallback else None,
        }

    if not funds:
        return None

    return {
        "has_unit_nav_fallback": True,
        "message": (
            "部分净值缺少 adj_nav，已回退 unit_nav；收益、回撤和风险指标可能不是完整总回报口径。"
            "建议先重算历史复权净值后再重新运行。"
        ),
        "funds": funds,
    }
