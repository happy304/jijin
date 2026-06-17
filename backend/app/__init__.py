"""Fund Quant Platform backend application package.

Module layout is defined in `.kiro/specs/fund-quant-platform/design.md` and
progressively filled in through the phased tasks in `tasks.md`.

Phase 0 intentionally ships only placeholder subpackages so that:
- `pip install -e ".[dev,test]"` succeeds,
- the `fundquant` CLI entry point resolves,
- linters / type checkers have a consistent package tree to inspect.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
