"""FactorEngine service — batch factor computation returning wide DataFrames.

Provides a high-performance, vectorized engine that computes multiple factors
across multiple funds in a single call. Supports:
- Configurable rolling windows
- Frequency resampling (daily → weekly/monthly) before computation
- Vectorized execution targeting < 1 second for 100 funds × 10 years

Satisfies requirements 3.9 (rolling window + frequency) and 3.11 (unified
DataFrame output structure).
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

import numpy as np
import pandas as pd

from app.domain.factors.registry import get_factor

logger = logging.getLogger(__name__)

# Type alias for frequency parameter
Frequency = Literal["daily", "weekly", "monthly"]


def _resample_nav(nav: pd.Series, freq: Frequency) -> pd.Series:
    """Resample a daily NAV series to the target frequency.

    Uses last available value in each period (standard for NAV data).

    Parameters:
        nav: Date-indexed daily NAV series.
        freq: Target frequency.

    Returns:
        Resampled NAV series. Returns original if freq is "daily".
    """
    if freq == "daily":
        return nav

    rule = "W-FRI" if freq == "weekly" else "ME"
    resampled = nav.resample(rule).last().dropna()
    return resampled


class FactorEngine:
    """Batch factor computation engine.

    Computes specified factors for a collection of funds, returning results
    as a wide-format DataFrame (index=fund_code, columns=factor_name).

    The engine is stateless and designed for repeated invocations with
    different parameters.

    Example::

        engine = FactorEngine(
            nav_data={"000001": nav_series_1, "000002": nav_series_2},
            factor_names=["annualized_return", "volatility", "sharpe"],
            window=252,
            freq="daily",
        )
        result = engine.compute()
        # result is a DataFrame with shape (2, 3)
        # index: ["000001", "000002"]
        # columns: ["annualized_return", "volatility", "sharpe"]
    """

    def __init__(
        self,
        nav_data: dict[str, pd.Series],
        factor_names: list[str],
        window: Optional[int] = None,
        freq: Frequency = "daily",
        benchmark_nav: Optional[pd.Series] = None,
    ) -> None:
        """Initialize the FactorEngine.

        Parameters:
            nav_data: Mapping of fund_code → NAV pd.Series (date-indexed).
            factor_names: List of registered factor names to compute.
            window: Optional rolling window size. If provided, the NAV series
                is truncated to the last ``window`` data points before
                computation. For factors that accept a ``window`` parameter,
                this value is NOT passed as a rolling window — it controls
                the lookback period of the input data.
            freq: Computation frequency. If "weekly" or "monthly", NAV data
                is resampled before factor computation.
            benchmark_nav: Optional benchmark NAV series, passed to factors
                that accept a ``benchmark_nav`` parameter.

        Raises:
            KeyError: If any factor_name is not registered.
        """
        self.nav_data = nav_data
        self.factor_names = factor_names
        self.window = window
        self.freq = freq
        self.benchmark_nav = benchmark_nav

        # Validate factor names eagerly
        self._factor_defs = {}
        for name in factor_names:
            self._factor_defs[name] = get_factor(name)

    def compute(self) -> pd.DataFrame:
        """Execute batch factor computation.

        Returns:
            A wide-format DataFrame where:
            - Index: fund codes (str)
            - Columns: factor names (str)
            - Values: computed factor values (float, may contain NaN)

        Notes:
            - Factors that require a benchmark but none is provided return NaN.
            - Funds with insufficient data for the window return NaN.
            - Computation is vectorized per-fund (no Python loops over dates).
        """
        fund_codes = list(self.nav_data.keys())
        results: dict[str, dict[str, float]] = {}

        # Pre-process benchmark if needed
        benchmark_resampled: Optional[pd.Series] = None
        if self.benchmark_nav is not None:
            benchmark_resampled = _resample_nav(self.benchmark_nav, self.freq)

        for code in fund_codes:
            nav = self.nav_data[code]
            if nav is None or nav.empty:
                results[code] = {name: np.nan for name in self.factor_names}
                continue

            # Resample to target frequency
            nav_resampled = _resample_nav(nav, self.freq)

            # Apply window truncation
            if self.window is not None and len(nav_resampled) > self.window:
                nav_resampled = nav_resampled.iloc[-self.window:]

            # Compute each factor
            fund_results: dict[str, float] = {}
            for name in self.factor_names:
                factor_def = self._factor_defs[name]
                fund_results[name] = self._compute_single_factor(
                    factor_def, nav_resampled, benchmark_resampled
                )
            results[code] = fund_results

        # Build wide DataFrame
        df = pd.DataFrame.from_dict(results, orient="index")
        df.index.name = "fund_code"
        df.columns.name = "factor"
        return df

    def _compute_single_factor(
        self,
        factor_def,
        nav: pd.Series,
        benchmark_nav: Optional[pd.Series],
    ) -> float:
        """Compute a single factor for a single fund.

        Handles parameter injection (benchmark_nav, risk_free_rate, freq)
        based on the factor function's signature.

        Returns:
            Scalar factor value, or np.nan on error.
        """
        import inspect

        fn = factor_def.fn
        sig = inspect.signature(fn)
        params = sig.parameters

        kwargs: dict = {"nav": nav}

        # Inject benchmark_nav if the factor accepts it
        if "benchmark_nav" in params:
            kwargs["benchmark_nav"] = benchmark_nav

        # Inject freq parameter (trading periods per year) based on
        # our resampling frequency
        if "freq" in params:
            kwargs["freq"] = self._get_annualization_factor()

        try:
            result = fn(**kwargs)
            # If the factor returns a Series (rolling mode), take the last value
            if isinstance(result, pd.Series):
                if result.empty or result.isna().all():
                    return np.nan
                return float(result.dropna().iloc[-1])
            if result is None or (isinstance(result, float) and np.isnan(result)):
                return np.nan
            return float(result)
        except Exception as e:
            logger.warning(
                "Factor '%s' computation failed: %s",
                factor_def.name,
                str(e),
            )
            return np.nan

    def _get_annualization_factor(self) -> int:
        """Return the annualization factor based on the computation frequency.

        Returns:
            252 for daily, 52 for weekly, 12 for monthly.
        """
        if self.freq == "weekly":
            return 52
        elif self.freq == "monthly":
            return 12
        return 252
