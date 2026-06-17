"""Data masking utilities for AI data pipeline.

When AI_DATA_MASKING is enabled, holdings data sent to LLM providers
is aggregated by industry instead of exposing individual stock codes
and names. This satisfies requirement 11.25: support configurable
desensitization before sending portfolio data to cloud LLMs.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass
class MaskedHolding:
    """Industry-level aggregated holding after masking."""

    industry: str
    total_weight: float
    stock_count: int


def mask_holdings_by_industry(
    holdings: list[dict[str, Any]],
) -> list[MaskedHolding]:
    """Aggregate individual stock holdings into industry-level summaries.

    Each input dict is expected to have at least:
      - "industry": str (industry classification)
      - "weight": float | Decimal (weight as percentage of NAV)

    Stocks without an industry field are grouped under "未分类".

    Returns a list of MaskedHolding sorted by total_weight descending.
    """
    industry_map: dict[str, dict[str, Any]] = {}

    for holding in holdings:
        industry = holding.get("industry") or "未分类"
        weight = holding.get("weight", 0)
        if isinstance(weight, Decimal):
            weight = float(weight)

        if industry not in industry_map:
            industry_map[industry] = {"total_weight": 0.0, "stock_count": 0}

        industry_map[industry]["total_weight"] += weight
        industry_map[industry]["stock_count"] += 1

    result = [
        MaskedHolding(
            industry=industry,
            total_weight=round(data["total_weight"], 4),
            stock_count=data["stock_count"],
        )
        for industry, data in industry_map.items()
    ]

    # Sort by weight descending for readability
    result.sort(key=lambda x: x.total_weight, reverse=True)
    return result


def format_masked_holdings_for_llm(masked: list[MaskedHolding]) -> str:
    """Format masked holdings into a text representation for LLM prompts.

    Returns a human-readable summary suitable for inclusion in prompts.
    """
    if not masked:
        return "无持仓数据"

    lines = ["行业配置分布:"]
    for item in masked:
        lines.append(
            f"  - {item.industry}: {item.total_weight:.2f}% "
            f"({item.stock_count} 只)"
        )
    return "\n".join(lines)
