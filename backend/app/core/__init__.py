"""Core building blocks: configuration, logging, shared constants.

These modules are framework-agnostic and must not import from `app.api`,
`app.services` or `app.domain` to keep the dependency direction clean.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings

__all__ = ["Settings", "get_settings"]
