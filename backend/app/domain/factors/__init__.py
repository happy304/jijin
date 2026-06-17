"""Factor library — registration, computation, and query interfaces."""

from app.domain.factors.registry import (
    FactorDef,
    factor,
    get_factor,
    list_factors,
)

# Import factor modules to trigger registration via @factor decorator
import app.domain.factors.returns  # noqa: F401
import app.domain.factors.risk  # noqa: F401
import app.domain.factors.risk_adjusted  # noqa: F401
import app.domain.factors.benchmark  # noqa: F401
import app.domain.factors.holding  # noqa: F401
import app.domain.factors.manager  # noqa: F401
import app.domain.factors.trend  # noqa: F401

__all__ = [
    "FactorDef",
    "factor",
    "get_factor",
    "list_factors",
]
