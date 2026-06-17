"""Data provider abstractions and implementations.

This package contains:
- ``base``: ``FundDataProvider`` Protocol and exception hierarchy
- ``snapshot``: Raw response archival (gzip-compressed local storage)
- ``eastmoney``: 天天基金数据 Provider（主源，priority=1）
- ``akshare``: AkShare 数据 Provider（备源，priority=2）
- ``cninfo``: 巨潮资讯数据 Provider（兜底，priority=3）
- ``composite``: CompositeProvider 多源编排（按优先级链式调用 + 熔断降级）
"""

from app.data.providers.base import (
    AllProvidersFailedError,
    FundDataProvider,
    HealthStatus,
    ProviderError,
    ProviderNotFoundError,
    ProviderTimeoutError,
)
from app.data.providers.snapshot import SnapshotArchive, SnapshotVersion

# Lazy imports for providers to avoid import errors when dependencies are missing
def __getattr__(name: str):
    if name == "EastmoneyProvider":
        from app.data.providers.eastmoney import EastmoneyProvider
        return EastmoneyProvider
    if name == "AkshareProvider":
        from app.data.providers.akshare import AkshareProvider
        return AkshareProvider
    if name == "CnInfoProvider":
        from app.data.providers.cninfo import CnInfoProvider
        return CnInfoProvider
    if name == "CompositeProvider":
        from app.data.providers.composite import CompositeProvider
        return CompositeProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "AllProvidersFailedError",
    "AkshareProvider",
    "CnInfoProvider",
    "CompositeProvider",
    "EastmoneyProvider",
    "FundDataProvider",
    "HealthStatus",
    "ProviderError",
    "ProviderNotFoundError",
    "ProviderTimeoutError",
    "SnapshotArchive",
    "SnapshotVersion",
]
