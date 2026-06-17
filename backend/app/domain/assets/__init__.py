"""资产类型模块。

提供资产类型的统一抽象和注册机制，支持扩展新资产类型（股票、债券、ETF 等）。

内置资产类型：
- FundAsset: 开放式基金（T+1）
- MoneyFundAsset: 货币基金（T+0）
- ETFAsset: 交易所 ETF（T+1）
- StockAsset: 股票（T+1）
- BondAsset: 债券（T+0）
"""

from app.domain.assets.base import Asset
from app.domain.assets.registry import get_asset, list_assets, register_asset
from app.domain.assets.types import (
    BondAsset,
    ETFAsset,
    FundAsset,
    MoneyFundAsset,
    StockAsset,
)

__all__ = [
    "Asset",
    "BondAsset",
    "ETFAsset",
    "FundAsset",
    "MoneyFundAsset",
    "StockAsset",
    "get_asset",
    "list_assets",
    "register_asset",
]
