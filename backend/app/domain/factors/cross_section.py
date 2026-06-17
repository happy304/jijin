"""Cross-sectional factor preprocessing utilities.

A factor's raw value isn't directly usable in research. Standard pipeline:

    1. **Winsorize** — clip outliers by percentile or N-MAD (median absolute deviation)
    2. **Standardize** — z-score or rank-normalize across the cross-section
    3. **Industry/style neutralize** — regress out industry dummies and size

These three steps remove cross-sectional outliers, put factors on a common
scale, and strip away exposure to nuisance factors (industry rotation, size)
so the remaining signal is what's tested via IC and quintile backtests.

All functions operate on **wide-format DataFrames** indexed by date with
columns being asset codes, OR on **long-format DataFrames** with columns
[date, asset, factor_value, ...]. Where ambiguous, the wide-format API
is provided alongside a long-format equivalent.

References:
    - Grinold & Kahn (1999), "Active Portfolio Management"
    - Sloan (1996), "Do stock prices fully reflect information in accruals
      and cash flows about future earnings?"
    - Hou, Xue & Zhang (2015), "Digesting Anomalies"
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Winsorization
# ---------------------------------------------------------------------------


def winsorize_quantile(
    series: pd.Series,
    lower_quantile: float = 0.01,
    upper_quantile: float = 0.99,
) -> pd.Series:
    """Clip outliers to the given quantile thresholds.

    Common thresholds: 1%/99% (loose), 2.5%/97.5% (moderate), 5%/95% (tight).

    Parameters:
        series: Input series. NaN values are preserved as NaN.
        lower_quantile: Lower bound percentile (default 0.01 = 1st percentile).
        upper_quantile: Upper bound percentile (default 0.99 = 99th percentile).

    Returns:
        Series with values outside [q_low, q_high] clipped to the bounds.
        NaN positions are preserved.
    """
    if series is None or series.empty:
        return series
    if not (0.0 <= lower_quantile < upper_quantile <= 1.0):
        raise ValueError(
            f"Invalid quantiles: lower={lower_quantile}, upper={upper_quantile}"
        )

    valid = series.dropna()
    if len(valid) < 2:
        return series

    low = valid.quantile(lower_quantile)
    high = valid.quantile(upper_quantile)
    return series.clip(lower=low, upper=high)


def winsorize_mad(
    series: pd.Series,
    n_mad: float = 5.0,
) -> pd.Series:
    """Clip outliers using the N-MAD rule (Median Absolute Deviation).

    Robust to outliers themselves (unlike mean ± k·std). The bounds are:
        median ± n_mad × 1.4826 × MAD

    The 1.4826 factor scales MAD to be a consistent estimator of σ under
    the assumption of normality.

    Parameters:
        series: Input series.
        n_mad: How many MADs to keep. Common: 3 (strict), 5 (moderate), 10 (loose).

    Returns:
        Clipped series with NaN preserved.
    """
    if series is None or series.empty:
        return series

    valid = series.dropna()
    if len(valid) < 2:
        return series

    median = valid.median()
    mad = (valid - median).abs().median()
    if mad == 0:
        # All values equal → no clipping needed
        return series

    spread = 1.4826 * mad * n_mad
    return series.clip(lower=median - spread, upper=median + spread)


# ---------------------------------------------------------------------------
# Standardization
# ---------------------------------------------------------------------------


def zscore(series: pd.Series, ddof: int = 1) -> pd.Series:
    """Cross-sectional z-score: (x - mean) / std.

    Standardizes to zero mean, unit variance.

    Parameters:
        series: Input series.
        ddof: Delta degrees of freedom (1 = sample std, default).

    Returns:
        Z-scored series. Returns all-NaN if std is zero or data is insufficient.
    """
    if series is None or series.empty:
        return series

    valid = series.dropna()
    if len(valid) < 2:
        return pd.Series(np.nan, index=series.index)

    mu = valid.mean()
    sigma = valid.std(ddof=ddof)
    if sigma == 0 or np.isnan(sigma):
        return pd.Series(np.nan, index=series.index)

    return (series - mu) / sigma


def rank_normalize(series: pd.Series) -> pd.Series:
    """Cross-sectional rank standardization to [-0.5, 0.5].

    Robust to outliers and non-normality. Each value is replaced by
    (rank - 1) / (N - 1) - 0.5 where rank is the ascending rank.

    Parameters:
        series: Input series.

    Returns:
        Rank-normalized series in [-0.5, 0.5]. NaN preserved.
    """
    if series is None or series.empty:
        return series

    valid = series.dropna()
    n = len(valid)
    if n < 2:
        return pd.Series(np.nan, index=series.index)

    ranks = valid.rank(method="average")
    normalized = (ranks - 1) / (n - 1) - 0.5
    return normalized.reindex(series.index)


def standardize(
    series: pd.Series,
    method: str = "zscore",
) -> pd.Series:
    """Standardize a series by the chosen method.

    Parameters:
        series: Input series.
        method: 'zscore' or 'rank'.

    Returns:
        Standardized series.
    """
    if method == "zscore":
        return zscore(series)
    if method == "rank":
        return rank_normalize(series)
    raise ValueError(f"Unknown standardization method: {method}")


# ---------------------------------------------------------------------------
# Neutralization (regress out industry + size)
# ---------------------------------------------------------------------------


def neutralize(
    factor: pd.Series,
    industry: pd.Series | None = None,
    log_size: pd.Series | None = None,
) -> pd.Series:
    """Industry & size neutralization via OLS regression.

    Regresses the factor on industry dummies and log market cap, returns
    the residuals. The residuals are the part of the factor uncorrelated
    with industry membership and size — the "pure" alpha signal.

    Parameters:
        factor: Factor values (index = asset codes).
        industry: Industry classification per asset (str labels). One-hot
            encoded as dummies inside the regression. Optional.
        log_size: Log market cap (or log AUM for funds) per asset. Optional.

    Returns:
        Residual series with NaN for assets missing any input.

    Notes:
        - Drop one industry dummy to avoid the dummy variable trap (we use
          the lexicographically smallest industry as the reference).
        - If both ``industry`` and ``log_size`` are None, returns the
          factor unchanged.
        - Requires at least 5 observations more than features for stable
          regression; below this returns NaN-filled series.
    """
    if factor is None or factor.empty:
        return factor

    if industry is None and log_size is None:
        return factor.copy()

    # Build design matrix
    df = pd.DataFrame({"factor": factor})
    if industry is not None:
        df["industry"] = industry
    if log_size is not None:
        df["log_size"] = log_size

    # Drop assets with any missing value
    clean = df.dropna()
    if len(clean) < 5:
        return pd.Series(np.nan, index=factor.index)

    y = clean["factor"].values.astype(np.float64)
    feature_cols: list[np.ndarray] = []

    # Industry dummies (drop first level to avoid collinearity)
    if industry is not None:
        ind_dummies = pd.get_dummies(clean["industry"], drop_first=True, dtype=float)
        if not ind_dummies.empty:
            feature_cols.append(ind_dummies.values)

    if log_size is not None:
        feature_cols.append(clean["log_size"].values.reshape(-1, 1).astype(np.float64))

    # Stack features and prepend intercept
    if not feature_cols:
        # No features after processing — return original
        return factor.copy()

    X = np.hstack(feature_cols)
    intercept = np.ones((X.shape[0], 1))
    X_full = np.hstack([intercept, X])

    n, k = X_full.shape
    if n <= k:
        # Not enough degrees of freedom
        return pd.Series(np.nan, index=factor.index)

    # OLS: β = (X'X)^{-1} X'y
    try:
        coef, *_ = np.linalg.lstsq(X_full, y, rcond=None)
    except np.linalg.LinAlgError:
        return pd.Series(np.nan, index=factor.index)

    y_hat = X_full @ coef
    residuals = y - y_hat

    out = pd.Series(np.nan, index=factor.index, dtype=float)
    out.loc[clean.index] = residuals
    return out


# ---------------------------------------------------------------------------
# Composite preprocessing pipeline
# ---------------------------------------------------------------------------


def preprocess_factor(
    factor: pd.Series,
    *,
    winsorize_method: str | None = "quantile",
    winsorize_kwargs: dict | None = None,
    standardize_method: str | None = "zscore",
    industry: pd.Series | None = None,
    log_size: pd.Series | None = None,
) -> pd.Series:
    """Standard cross-section preprocessing pipeline.

    Applies in order:
        1. Winsorize (quantile or MAD, optional)
        2. Neutralize (industry + size, optional)
        3. Standardize (zscore or rank, optional)

    Parameters:
        factor: Raw cross-sectional factor values.
        winsorize_method: 'quantile' / 'mad' / None.
        winsorize_kwargs: Kwargs to the winsorize function.
        standardize_method: 'zscore' / 'rank' / None.
        industry: Industry classification (for neutralization).
        log_size: Log size (for neutralization).

    Returns:
        Processed factor series.
    """
    result = factor.copy()

    if winsorize_method is not None:
        kw = winsorize_kwargs or {}
        if winsorize_method == "quantile":
            result = winsorize_quantile(result, **kw)
        elif winsorize_method == "mad":
            result = winsorize_mad(result, **kw)
        else:
            raise ValueError(f"Unknown winsorize method: {winsorize_method}")

    if industry is not None or log_size is not None:
        result = neutralize(result, industry=industry, log_size=log_size)

    if standardize_method is not None:
        result = standardize(result, method=standardize_method)

    return result


__all__ = [
    "neutralize",
    "preprocess_factor",
    "rank_normalize",
    "standardize",
    "winsorize_mad",
    "winsorize_quantile",
    "zscore",
]
