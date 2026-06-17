"""Factor registration mechanism.

Provides a decorator-based registry for quantitative factors, allowing
developers to register new factors with ``@factor("name", category="...")``
and query them via ``list_factors()`` / ``get_factor(name)``.

Satisfies requirements 10.4 (decorator registration) and 3.11 (unified
factor output structure).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass(frozen=True, slots=True)
class FactorDef:
    """Metadata record for a registered factor.

    Attributes:
        name: Unique identifier for the factor (e.g. "annualized_return").
        category: Classification bucket (e.g. "return", "risk", "benchmark").
        window: Required lookback window in periods (None if not applicable).
        fn: The underlying computation function.
        return_type: Description of the return type ("scalar" or "series").
        description: Optional human-readable description extracted from docstring.
    """

    name: str
    category: str
    window: Optional[int] = None
    fn: Callable[..., Any] = field(repr=False, default=lambda: None)
    return_type: str = "scalar"
    description: str = ""


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------

_FACTOR_REGISTRY: dict[str, FactorDef] = {}


def factor(
    name: str,
    category: str,
    window: Optional[int] = None,
    return_type: str = "scalar",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that registers a function as a named factor.

    Parameters:
        name: Unique factor name. Raises ``ValueError`` if already registered.
        category: Factor category (e.g. "return", "risk", "risk_adjusted",
            "benchmark", "holding", "manager").
        window: Minimum lookback window in periods required by this factor.
            ``None`` means no specific window requirement.
        return_type: One of "scalar" (single value for the whole series) or
            "series" (returns a time series).

    Returns:
        A decorator that registers the wrapped function and returns it unchanged.

    Example::

        @factor("annualized_return", category="return")
        def annualized_return(nav: pd.Series, freq: int = 252) -> float:
            ...
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in _FACTOR_REGISTRY:
            raise ValueError(
                f"Factor '{name}' is already registered. "
                "Each factor name must be unique."
            )
        description = (fn.__doc__ or "").strip().split("\n")[0]
        _FACTOR_REGISTRY[name] = FactorDef(
            name=name,
            category=category,
            window=window,
            fn=fn,
            return_type=return_type,
            description=description,
        )
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Query interface
# ---------------------------------------------------------------------------


def list_factors(category: Optional[str] = None) -> list[FactorDef]:
    """Return all registered factors, optionally filtered by category.

    Parameters:
        category: If provided, only factors matching this category are returned.

    Returns:
        A list of ``FactorDef`` instances sorted by name.
    """
    factors = list(_FACTOR_REGISTRY.values())
    if category is not None:
        factors = [f for f in factors if f.category == category]
    return sorted(factors, key=lambda f: f.name)


def get_factor(name: str) -> FactorDef:
    """Retrieve a single factor definition by name.

    Parameters:
        name: The registered factor name.

    Returns:
        The corresponding ``FactorDef``.

    Raises:
        KeyError: If no factor with the given name is registered.
    """
    try:
        return _FACTOR_REGISTRY[name]
    except KeyError:
        available = ", ".join(sorted(_FACTOR_REGISTRY.keys())) or "(none)"
        raise KeyError(
            f"Factor '{name}' is not registered. Available factors: {available}"
        ) from None


# ---------------------------------------------------------------------------
# Internal helpers (for testing / reset)
# ---------------------------------------------------------------------------


def _clear_registry() -> None:
    """Remove all registered factors. Intended for test isolation only."""
    _FACTOR_REGISTRY.clear()


def _snapshot_registry() -> dict[str, "FactorDef"]:
    """Return a shallow copy of the current registry state."""
    return dict(_FACTOR_REGISTRY)


def _restore_registry(snapshot: dict[str, "FactorDef"]) -> None:
    """Replace the registry contents with a previously saved snapshot."""
    _FACTOR_REGISTRY.clear()
    _FACTOR_REGISTRY.update(snapshot)
