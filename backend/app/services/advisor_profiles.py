from __future__ import annotations

from app.services.trading_advisor import AdvisorConfig

RISK_PROFILES: dict[str, dict[str, float]] = {
    "conservative": {
        "buy_threshold": 0.25,
        "sell_threshold": -0.22,
        "max_single_position": 0.20,
        "max_daily_trade_pct": 0.10,
        "target_portfolio_vol": 0.06,
    },
    "moderate": {
        "buy_threshold": 0.18,
        "sell_threshold": -0.18,
        "max_single_position": 0.30,
        "max_daily_trade_pct": 0.20,
        "target_portfolio_vol": 0.10,
    },
    "aggressive": {
        "buy_threshold": 0.12,
        "sell_threshold": -0.12,
        "max_single_position": 0.50,
        "max_daily_trade_pct": 0.30,
        "target_portfolio_vol": 0.15,
    },
}


def normalize_risk_level(value: str | None) -> str:
    """归一化风险等级。"""
    risk_level = str(value or "moderate").strip().lower()
    if risk_level not in RISK_PROFILES:
        return "moderate"
    return risk_level



def build_advisor_config(risk_level: str = "moderate") -> AdvisorConfig:
    """按风险等级构建统一的建议引擎配置。"""
    profile = RISK_PROFILES[normalize_risk_level(risk_level)]
    return AdvisorConfig(
        buy_threshold=profile["buy_threshold"],
        sell_threshold=profile["sell_threshold"],
        max_single_position=profile["max_single_position"],
        max_daily_trade_pct=profile["max_daily_trade_pct"],
        target_portfolio_vol=profile["target_portfolio_vol"],
    )


__all__ = ["RISK_PROFILES", "normalize_risk_level", "build_advisor_config"]
