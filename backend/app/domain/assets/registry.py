"""资产类型注册表。

提供资产类型的注册、查询功能。内置资产类型在模块加载时自动注册，
开发者可通过 register_asset() 注册自定义资产类型。

Usage::

    from app.domain.assets.registry import register_asset, get_asset

    # 注册自定义资产类型
    register_asset(MyCustomAsset())

    # 通过 asset_type 获取
    asset = get_asset("fund")
    fee = asset.calc_fee(Decimal("10000"), "subscribe")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from app.domain.assets.base import Asset

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 全局注册表
# ---------------------------------------------------------------------------

_ASSET_REGISTRY: dict[str, Asset] = {}


def register_asset(asset: Asset) -> None:
    """注册一个资产类型实例。

    Args:
        asset: Asset 子类实例

    Raises:
        TypeError: 如果 asset 不是 Asset 的实例
        ValueError: 如果 asset_type 为空
    """
    if not isinstance(asset, Asset):
        raise TypeError(f"必须是 Asset 的实例，收到: {type(asset).__name__}")

    if not asset.asset_type:
        raise ValueError(
            f"资产类 {type(asset).__name__} 必须定义非空的 asset_type 属性"
        )

    if asset.asset_type in _ASSET_REGISTRY:
        logger.warning(
            "资产类型 '%s' 已注册，将被 %s 覆盖",
            asset.asset_type,
            type(asset).__name__,
        )

    _ASSET_REGISTRY[asset.asset_type] = asset
    logger.debug("已注册资产类型: %s (%s)", asset.asset_type, type(asset).__name__)


def get_asset(asset_type: str) -> Asset:
    """根据 asset_type 获取资产实例。

    Args:
        asset_type: 资产类型标识

    Returns:
        对应的 Asset 实例

    Raises:
        KeyError: 如果 asset_type 未注册
    """
    try:
        return _ASSET_REGISTRY[asset_type]
    except KeyError:
        available = ", ".join(sorted(_ASSET_REGISTRY.keys())) or "(none)"
        raise KeyError(
            f"资产类型 '{asset_type}' 未注册。可用类型: {available}"
        ) from None


def list_assets() -> dict[str, Asset]:
    """列出所有已注册的资产类型。

    Returns:
        {asset_type: Asset} 字典（副本）
    """
    return dict(_ASSET_REGISTRY)


def _clear_registry() -> None:
    """清空注册表。仅用于测试。"""
    _ASSET_REGISTRY.clear()


# ---------------------------------------------------------------------------
# 自动注册内置资产类型
# ---------------------------------------------------------------------------

def _register_builtins() -> None:
    """注册内置资产类型。"""
    from app.domain.assets.types import (
        BondAsset,
        ETFAsset,
        FundAsset,
        MoneyFundAsset,
        StockAsset,
    )

    register_asset(FundAsset())
    register_asset(MoneyFundAsset())
    register_asset(ETFAsset())
    register_asset(StockAsset())
    register_asset(BondAsset())


# 模块加载时自动注册
_register_builtins()
