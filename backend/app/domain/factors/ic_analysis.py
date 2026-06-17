"""Information Coefficient (IC) and quintile portfolio analysis.

The IC test is the workhorse of cross-sectional factor research. Given:
- A panel of factor exposures (date × asset → factor_value)
- A panel of forward returns (date × asset → forward_return)

We compute, for each date:
- **IC** = Pearson correlation between factor_t and forward_return_t (Spearman = Rank IC)
- **IC Decay** = IC computed at multiple forward horizons (1, 5, 10, 20 days)

Aggregate metrics across the time series:
- **IC mean** ≥ 0.03 considered economically meaningful
- **IC IR** = IC_mean / IC_std × √frequency, ≥ 0.5 considered robust
- **IC t-stat** with Newey-West correction for autocorrelation
- **IC win rate** = fraction of periods where IC has the expected sign

Quintile / decile backtests provide a complementary view:
- Sort assets each rebalance into N groups by factor
- Track equal-weight portfolio return for each group
- Long-short = top group - bottom group; should be monotonic across groups
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

import numpy as np
import pandas as pd
from scipy import stats


# ---------------------------------------------------------------------------
# IC computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ICStats:
    """Aggregate IC statistics across the time series.

    Attributes:
        ic_mean: Mean IC across all dates.
        ic_std: Standard deviation of IC across dates.
        ic_ir: IC mean / IC std (Information Ratio for the IC series).
        ic_ir_annualized: IR × √frequency (e.g. √252 for daily).
        ic_t_stat: T-statistic for H0: IC_mean = 0 (Newey-West adjusted).
        ic_p_value: Two-sided p-value.
        ic_positive_rate: Fraction of dates with positive IC.
        ic_significant_rate: Fraction of dates with |IC| > 0.02 in the
            same direction as the mean.
        n_periods: Number of date points contributing.
        method: 'pearson' or 'spearman'.
    """

    ic_mean: float
    ic_std: float
    ic_ir: float
    ic_ir_annualized: float
    ic_t_stat: float
    ic_p_value: float
    ic_positive_rate: float
    ic_significant_rate: float
    n_periods: int
    method: str = "pearson"

    def to_dict(self) -> dict:
        return {
            "ic_mean": _safe(self.ic_mean),
            "ic_std": _safe(self.ic_std),
            "ic_ir": _safe(self.ic_ir),
            "ic_ir_annualized": _safe(self.ic_ir_annualized),
            "ic_t_stat": _safe(self.ic_t_stat),
            "ic_p_value": _safe(self.ic_p_value),
            "ic_positive_rate": _safe(self.ic_positive_rate),
            "ic_significant_rate": _safe(self.ic_significant_rate),
            "n_periods": self.n_periods,
            "method": self.method,
        }


def _safe(v: float) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if np.isnan(f) or np.isinf(f):
        return None
    return f


def _newey_west_se(x: np.ndarray, lags: int = 5) -> float:
    """Newey-West HAC standard error for the mean of a series.

    Robust to autocorrelation (which IC series typically have because
    factors are persistent across rebalances).

    SE_NW²(x̄) = (1/T²) × Σ_{l=0}^{lags} w_l × Σ_t cov(x_t, x_{t-l})

    where w_l = 1 - l/(lags+1) is the Bartlett kernel.
    """
    n = len(x)
    if n < 2:
        return float("nan")
    centered = x - np.mean(x)
    # Lag-0 contribution = sample variance (with 1/T)
    gamma_0 = np.sum(centered**2) / n
    var_estimate = gamma_0
    max_lag = min(lags, n - 1)
    for lag in range(1, max_lag + 1):
        # Bartlett kernel weight
        w = 1.0 - lag / (max_lag + 1)
        gamma_l = np.sum(centered[lag:] * centered[:-lag]) / n
        var_estimate += 2.0 * w * gamma_l
    if var_estimate < 0:
        # Numerical: when T small and autocov dominates, can go negative.
        # Fall back to the iid estimator.
        var_estimate = gamma_0
    se_mean = np.sqrt(var_estimate / n) if var_estimate > 0 else 0.0
    return float(se_mean)


def compute_ic_series(
    factor_panel: pd.DataFrame,
    forward_returns: pd.DataFrame,
    method: Literal["pearson", "spearman"] = "spearman",
    min_assets_per_period: int = 5,
) -> pd.Series:
    """Compute the cross-sectional IC series across all dates.

    For each date t: IC_t = corr(factor_panel.loc[t], forward_returns.loc[t])

    Parameters:
        factor_panel: Wide DataFrame, index=date, columns=asset_codes,
            values=factor exposures.
        forward_returns: Wide DataFrame, same shape, values=forward returns.
            Pre-aligned with factor_panel.
        method: 'pearson' for IC, 'spearman' for Rank IC.
        min_assets_per_period: Skip dates with fewer valid asset pairs.

    Returns:
        IC time series indexed by date. Dates with insufficient data are
        dropped from the result.
    """
    if factor_panel is None or forward_returns is None:
        return pd.Series([], dtype=float)
    if factor_panel.empty or forward_returns.empty:
        return pd.Series([], dtype=float)

    # Align on dates and assets
    common_dates = factor_panel.index.intersection(forward_returns.index)
    common_assets = factor_panel.columns.intersection(forward_returns.columns)
    if len(common_dates) == 0 or len(common_assets) == 0:
        return pd.Series([], dtype=float)

    f = factor_panel.loc[common_dates, common_assets]
    r = forward_returns.loc[common_dates, common_assets]

    ic_values: list[float] = []
    ic_dates: list = []

    for date in common_dates:
        f_row = f.loc[date]
        r_row = r.loc[date]

        # Drop NaN pairs
        mask = f_row.notna() & r_row.notna()
        if mask.sum() < min_assets_per_period:
            continue

        f_clean = f_row[mask].astype(float)
        r_clean = r_row[mask].astype(float)

        # Skip if no variation
        if f_clean.std(ddof=1) == 0 or r_clean.std(ddof=1) == 0:
            continue

        if method == "spearman":
            corr, _ = stats.spearmanr(f_clean.values, r_clean.values)
        else:
            corr, _ = stats.pearsonr(f_clean.values, r_clean.values)

        if not np.isfinite(corr):
            continue

        ic_values.append(float(corr))
        ic_dates.append(date)

    return pd.Series(ic_values, index=pd.Index(ic_dates, name="date"), name=f"ic_{method}")


def compute_ic_stats(
    ic_series: pd.Series,
    annualization: int = 252,
    nw_lags: int = 5,
    method: Literal["pearson", "spearman"] = "spearman",
) -> ICStats | None:
    """Aggregate IC time series into summary statistics.

    Includes Newey-West HAC t-statistic to correct for IC autocorrelation.

    Parameters:
        ic_series: IC values per date.
        annualization: Trading periods per year for IR annualization.
        nw_lags: Newey-West lag order. Default 5 trading days.
        method: For metadata only.

    Returns:
        ICStats or None if insufficient data.
    """
    if ic_series is None or len(ic_series) < 2:
        return None

    arr = ic_series.dropna().values.astype(np.float64)
    n = len(arr)
    if n < 2:
        return None

    ic_mean = float(np.mean(arr))
    ic_std = float(np.std(arr, ddof=1))

    if ic_std == 0 or np.isnan(ic_std):
        return ICStats(
            ic_mean=ic_mean,
            ic_std=0.0,
            ic_ir=0.0,
            ic_ir_annualized=0.0,
            ic_t_stat=0.0,
            ic_p_value=1.0,
            ic_positive_rate=float(np.mean(arr > 0)),
            ic_significant_rate=0.0,
            n_periods=n,
            method=method,
        )

    ic_ir = ic_mean / ic_std

    # Newey-West HAC t-statistic
    nw_se = _newey_west_se(arr, lags=nw_lags)
    if np.isnan(nw_se) or nw_se == 0:
        ic_t_stat = ic_mean / (ic_std / np.sqrt(n))  # iid fallback
    else:
        ic_t_stat = ic_mean / nw_se

    # Two-sided p-value using normal approximation
    ic_p_value = float(2.0 * (1.0 - stats.norm.cdf(abs(ic_t_stat))))

    ic_positive_rate = float(np.mean(arr > 0))

    # Significant rate: |IC| > 0.02 in the same sign as the mean
    sign = np.sign(ic_mean)
    if sign == 0:
        sig_rate = 0.0
    else:
        sig_rate = float(np.mean((np.sign(arr) == sign) & (np.abs(arr) > 0.02)))

    return ICStats(
        ic_mean=ic_mean,
        ic_std=ic_std,
        ic_ir=float(ic_ir),
        ic_ir_annualized=float(ic_ir * np.sqrt(annualization)),
        ic_t_stat=float(ic_t_stat),
        ic_p_value=ic_p_value,
        ic_positive_rate=ic_positive_rate,
        ic_significant_rate=sig_rate,
        n_periods=n,
        method=method,
    )


def compute_ic_decay(
    factor_panel: pd.DataFrame,
    returns_panel: pd.DataFrame,
    horizons: Iterable[int] = (1, 5, 10, 20, 60),
    method: Literal["pearson", "spearman"] = "spearman",
) -> dict[int, ICStats]:
    """Compute IC at multiple forward horizons to measure factor decay.

    For each horizon h, builds the h-period forward return as the
    cumulative compound return ``(1+r_t)(1+r_{t+1})...(1+r_{t+h-1}) - 1``,
    then computes IC against the factor.

    A factor with strong information should show high IC at short horizons
    that gradually decays. A factor where IC is flat or rises at longer
    horizons may not be a true predictor.

    Parameters:
        factor_panel: Wide DataFrame of factor values.
        returns_panel: Wide DataFrame of single-period returns
            (NOT pre-aggregated forward returns).
        horizons: Forward horizons to test, in periods.
        method: Correlation method.

    Returns:
        Dict mapping horizon → ICStats.
    """
    results: dict[int, ICStats] = {}

    for h in horizons:
        if h < 1:
            continue
        # Compute h-period forward compound return
        fwd = (1.0 + returns_panel).rolling(window=h).apply(np.prod, raw=True) - 1.0
        # Shift so that fwd at date t corresponds to return from t+1 to t+h
        fwd_aligned = fwd.shift(-h)

        ic_series = compute_ic_series(factor_panel, fwd_aligned, method=method)
        stats_h = compute_ic_stats(ic_series, method=method)
        if stats_h is not None:
            results[int(h)] = stats_h

    return results


# ---------------------------------------------------------------------------
# Quintile / decile backtest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuintileResult:
    """Quintile portfolio backtest result.

    Attributes:
        n_groups: Number of groups (5 = quintile, 10 = decile).
        group_returns: DataFrame index=date, columns=group_id (1..N), values=
            equal-weight average return of assets in each group at each date.
        group_cumulative: Cumulative return curves per group.
        long_short_returns: Top group - bottom group return series.
        long_short_cumulative: Cumulative LS curve.
        annualized_returns: Per-group annualized return.
        sharpes: Per-group annualized Sharpe (rf=0).
        long_short_sharpe: LS portfolio Sharpe.
        monotonicity: 1 if returns increase monotonically from group 1 to N,
            -1 if monotonically decreasing, 0 otherwise. Checks the rough
            economic plausibility of the factor.
    """

    n_groups: int
    group_returns: pd.DataFrame
    group_cumulative: pd.DataFrame
    long_short_returns: pd.Series
    long_short_cumulative: pd.Series
    annualized_returns: dict[int, float] = field(default_factory=dict)
    sharpes: dict[int, float] = field(default_factory=dict)
    long_short_sharpe: float = 0.0
    monotonicity: int = 0

    def to_dict(self) -> dict:
        return {
            "n_groups": self.n_groups,
            "annualized_returns": {
                str(k): _safe(v) for k, v in self.annualized_returns.items()
            },
            "sharpes": {str(k): _safe(v) for k, v in self.sharpes.items()},
            "long_short_sharpe": _safe(self.long_short_sharpe),
            "monotonicity": self.monotonicity,
            "long_short_total_return": _safe(
                float(self.long_short_cumulative.iloc[-1] - 1.0)
                if len(self.long_short_cumulative) > 0
                else 0.0
            ),
        }


def quintile_backtest(
    factor_panel: pd.DataFrame,
    returns_panel: pd.DataFrame,
    n_groups: int = 5,
    annualization: int = 252,
) -> QuintileResult | None:
    """Long-short quintile (or N-tile) backtest.

    For each rebalance date:
        1. Rank assets by factor value
        2. Sort into n_groups equal-size buckets
        3. Use group membership for the **next** period's return
           (forward, no look-ahead)
        4. Compute equal-weight average return per group

    Parameters:
        factor_panel: Wide DataFrame of factor values (date × asset).
        returns_panel: Wide DataFrame of next-period returns aligned with
            factor_panel (date × asset). The return at row t is the return
            from t to t+1.
        n_groups: Number of groups (5 quintile, 10 decile).
        annualization: Periods per year (252 daily, 12 monthly).

    Returns:
        QuintileResult or None if insufficient data.

    Notes:
        Expects ``returns_panel`` to be **already-shifted forward returns**.
        If you have raw returns, pass ``returns_panel.shift(-1)`` so the
        return at date t is the period (t, t+1].
    """
    if factor_panel is None or returns_panel is None:
        return None
    if factor_panel.empty or returns_panel.empty or n_groups < 2:
        return None

    # Align indexes
    common_dates = factor_panel.index.intersection(returns_panel.index)
    common_assets = factor_panel.columns.intersection(returns_panel.columns)
    if len(common_dates) < 2 or len(common_assets) < n_groups:
        return None

    f = factor_panel.loc[common_dates, common_assets]
    r = returns_panel.loc[common_dates, common_assets]

    group_returns_records: list[dict] = []

    for date in common_dates:
        f_row = f.loc[date]
        r_row = r.loc[date]

        mask = f_row.notna() & r_row.notna()
        if mask.sum() < n_groups:
            continue

        f_clean = f_row[mask]
        r_clean = r_row[mask]

        # qcut into n_groups; duplicates='drop' to handle ties
        try:
            groups = pd.qcut(
                f_clean,
                q=n_groups,
                labels=list(range(1, n_groups + 1)),
                duplicates="drop",
            )
        except (ValueError, IndexError):
            continue

        record: dict = {"date": date}
        for g in range(1, n_groups + 1):
            mask_g = groups == g
            if mask_g.sum() == 0:
                record[g] = np.nan
            else:
                record[g] = float(r_clean[mask_g].mean())
        group_returns_records.append(record)

    if not group_returns_records:
        return None

    df_returns = pd.DataFrame(group_returns_records).set_index("date")
    df_returns.columns = pd.Index([int(c) for c in df_returns.columns], name="group")

    # Cumulative equity (1 = initial)
    df_cumulative = (1.0 + df_returns.fillna(0.0)).cumprod()

    # Long-short: top group - bottom group
    if 1 in df_returns.columns and n_groups in df_returns.columns:
        ls_returns = df_returns[n_groups] - df_returns[1]
    else:
        ls_returns = pd.Series([], dtype=float)
    ls_cumulative = (1.0 + ls_returns.fillna(0.0)).cumprod()

    # Per-group statistics
    annualized: dict[int, float] = {}
    sharpes: dict[int, float] = {}
    for g in df_returns.columns:
        col = df_returns[g].dropna()
        if len(col) < 2:
            annualized[int(g)] = float("nan")
            sharpes[int(g)] = float("nan")
            continue
        mean = float(col.mean())
        std = float(col.std(ddof=1))
        annualized[int(g)] = float(mean * annualization)
        sharpes[int(g)] = float(mean / std * np.sqrt(annualization)) if std > 0 else 0.0

    if len(ls_returns.dropna()) >= 2:
        ls_mean = float(ls_returns.dropna().mean())
        ls_std = float(ls_returns.dropna().std(ddof=1))
        ls_sharpe = float(ls_mean / ls_std * np.sqrt(annualization)) if ls_std > 0 else 0.0
    else:
        ls_sharpe = 0.0

    # Monotonicity: are annualized_returns sorted in either direction?
    sorted_returns = [annualized[g] for g in sorted(annualized) if not np.isnan(annualized[g])]
    monotonicity = 0
    if len(sorted_returns) >= 3:
        ascending = all(sorted_returns[i] <= sorted_returns[i + 1] for i in range(len(sorted_returns) - 1))
        descending = all(sorted_returns[i] >= sorted_returns[i + 1] for i in range(len(sorted_returns) - 1))
        if ascending and not descending:
            monotonicity = 1
        elif descending and not ascending:
            monotonicity = -1

    return QuintileResult(
        n_groups=n_groups,
        group_returns=df_returns,
        group_cumulative=df_cumulative,
        long_short_returns=ls_returns,
        long_short_cumulative=ls_cumulative,
        annualized_returns=annualized,
        sharpes=sharpes,
        long_short_sharpe=ls_sharpe,
        monotonicity=monotonicity,
    )


# ---------------------------------------------------------------------------
# Convenience: one-shot factor evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FactorEvaluation:
    """Combined IC and quintile evaluation of a factor."""

    ic_pearson: ICStats | None = None
    ic_spearman: ICStats | None = None
    ic_decay: dict[int, ICStats] = field(default_factory=dict)
    quintile: QuintileResult | None = None

    def to_dict(self) -> dict:
        return {
            "ic_pearson": self.ic_pearson.to_dict() if self.ic_pearson else None,
            "ic_spearman": self.ic_spearman.to_dict() if self.ic_spearman else None,
            "ic_decay": {
                str(h): s.to_dict() for h, s in self.ic_decay.items()
            },
            "quintile": self.quintile.to_dict() if self.quintile else None,
        }


def evaluate_factor(
    factor_panel: pd.DataFrame,
    returns_panel: pd.DataFrame,
    *,
    decay_horizons: Iterable[int] = (1, 5, 10, 20),
    n_groups: int = 5,
) -> FactorEvaluation:
    """One-shot factor evaluation: IC + Rank IC + decay + quintile backtest.

    Parameters:
        factor_panel: Wide DataFrame of factor values.
        returns_panel: Wide DataFrame of single-period returns aligned to
            factor dates. NOT shifted; this function shifts internally.
        decay_horizons: Forward horizons for IC decay analysis.
        n_groups: Number of groups for quintile backtest.

    Returns:
        FactorEvaluation with all metrics populated.
    """
    # 1-period forward return (shift -1 so date t holds return for (t, t+1])
    fwd_returns_1 = returns_panel.shift(-1)

    pearson_series = compute_ic_series(factor_panel, fwd_returns_1, method="pearson")
    spearman_series = compute_ic_series(factor_panel, fwd_returns_1, method="spearman")

    ic_pearson = compute_ic_stats(pearson_series, method="pearson")
    ic_spearman = compute_ic_stats(spearman_series, method="spearman")

    decay = compute_ic_decay(factor_panel, returns_panel, horizons=decay_horizons)

    quintile = quintile_backtest(
        factor_panel=factor_panel,
        returns_panel=fwd_returns_1,
        n_groups=n_groups,
    )

    return FactorEvaluation(
        ic_pearson=ic_pearson,
        ic_spearman=ic_spearman,
        ic_decay=decay,
        quintile=quintile,
    )


__all__ = [
    "FactorEvaluation",
    "ICStats",
    "QuintileResult",
    "compute_ic_decay",
    "compute_ic_series",
    "compute_ic_stats",
    "evaluate_factor",
    "quintile_backtest",
]
