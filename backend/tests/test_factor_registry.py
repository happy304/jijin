"""Unit tests for the factor registration mechanism.

Covers:
- @factor decorator registration
- FactorDef metadata correctness
- list_factors() with and without category filter
- get_factor() success and failure cases
- Duplicate name rejection
"""

from __future__ import annotations

import pytest

from app.domain.factors.registry import (
    FactorDef,
    _clear_registry,
    _restore_registry,
    _snapshot_registry,
    factor,
    get_factor,
    list_factors,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure each test starts with an empty factor registry, then restore."""
    snapshot = _snapshot_registry()
    _clear_registry()
    yield
    _restore_registry(snapshot)


# ---------------------------------------------------------------------------
# @factor decorator
# ---------------------------------------------------------------------------


class TestFactorDecorator:
    """Tests for the @factor registration decorator."""

    def test_registers_factor_with_minimal_args(self):
        @factor("my_factor", category="return")
        def my_factor(nav):
            """Compute my factor."""
            return 0.0

        result = get_factor("my_factor")
        assert result.name == "my_factor"
        assert result.category == "return"
        assert result.window is None
        assert result.return_type == "scalar"
        assert result.fn is my_factor

    def test_registers_factor_with_all_args(self):
        @factor("rolling_vol", category="risk", window=20, return_type="series")
        def rolling_vol(nav):
            """Rolling volatility over a window."""
            return nav

        result = get_factor("rolling_vol")
        assert result.name == "rolling_vol"
        assert result.category == "risk"
        assert result.window == 20
        assert result.return_type == "series"
        assert result.fn is rolling_vol

    def test_decorator_returns_original_function(self):
        @factor("test_fn", category="return")
        def test_fn(x):
            return x * 2

        # The decorated function should still be callable as normal
        assert test_fn(5) == 10

    def test_extracts_first_line_of_docstring(self):
        @factor("documented", category="risk")
        def documented(nav):
            """First line of docs.

            More details here that should not appear.
            """
            return 0.0

        result = get_factor("documented")
        assert result.description == "First line of docs."

    def test_empty_docstring_gives_empty_description(self):
        @factor("no_docs", category="return")
        def no_docs(nav):
            return 0.0

        result = get_factor("no_docs")
        assert result.description == ""

    def test_duplicate_name_raises_value_error(self):
        @factor("dup", category="return")
        def first(nav):
            return 0.0

        with pytest.raises(ValueError, match="already registered"):

            @factor("dup", category="risk")
            def second(nav):
                return 0.0


# ---------------------------------------------------------------------------
# FactorDef dataclass
# ---------------------------------------------------------------------------


class TestFactorDef:
    """Tests for the FactorDef data class."""

    def test_is_frozen(self):
        fdef = FactorDef(name="x", category="return", fn=lambda: None)
        with pytest.raises(AttributeError):
            fdef.name = "y"  # type: ignore[misc]

    def test_default_values(self):
        fdef = FactorDef(name="x", category="return")
        assert fdef.window is None
        assert fdef.return_type == "scalar"
        assert fdef.description == ""


# ---------------------------------------------------------------------------
# list_factors()
# ---------------------------------------------------------------------------


class TestListFactors:
    """Tests for the list_factors query interface."""

    def test_empty_registry_returns_empty_list(self):
        assert list_factors() == []

    def test_returns_all_registered_factors(self):
        @factor("alpha", category="return")
        def alpha(nav):
            return 0.0

        @factor("beta", category="benchmark")
        def beta(nav, bench):
            return 0.0

        result = list_factors()
        assert len(result) == 2
        names = [f.name for f in result]
        assert "alpha" in names
        assert "beta" in names

    def test_filters_by_category(self):
        @factor("ret1", category="return")
        def ret1(nav):
            return 0.0

        @factor("risk1", category="risk")
        def risk1(nav):
            return 0.0

        @factor("ret2", category="return")
        def ret2(nav):
            return 0.0

        result = list_factors(category="return")
        assert len(result) == 2
        assert all(f.category == "return" for f in result)

    def test_filter_nonexistent_category_returns_empty(self):
        @factor("x", category="return")
        def x(nav):
            return 0.0

        assert list_factors(category="nonexistent") == []

    def test_results_sorted_by_name(self):
        @factor("zeta", category="return")
        def zeta(nav):
            return 0.0

        @factor("alpha", category="return")
        def alpha(nav):
            return 0.0

        @factor("mu", category="return")
        def mu(nav):
            return 0.0

        result = list_factors()
        names = [f.name for f in result]
        assert names == ["alpha", "mu", "zeta"]


# ---------------------------------------------------------------------------
# get_factor()
# ---------------------------------------------------------------------------


class TestGetFactor:
    """Tests for the get_factor query interface."""

    def test_returns_correct_factor(self):
        @factor("target", category="risk", window=30)
        def target(nav):
            """Target factor."""
            return 0.0

        result = get_factor("target")
        assert result.name == "target"
        assert result.category == "risk"
        assert result.window == 30

    def test_raises_key_error_for_unknown_name(self):
        with pytest.raises(KeyError, match="not registered"):
            get_factor("nonexistent")

    def test_error_message_lists_available_factors(self):
        @factor("available_one", category="return")
        def available_one(nav):
            return 0.0

        with pytest.raises(KeyError, match="available_one"):
            get_factor("missing")

    def test_callable_via_factor_def(self):
        """The registered fn should be directly callable."""

        @factor("callable_test", category="return")
        def callable_test(x: float) -> float:
            return x * 2

        fdef = get_factor("callable_test")
        assert fdef.fn(3.0) == 6.0
