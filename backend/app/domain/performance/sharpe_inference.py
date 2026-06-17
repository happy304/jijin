"""Sharpe ratio statistical inference: PSR and DSR.

A naked Sharpe ratio is a point estimate. When you've tried many strategies
or parameter combinations, the highest-Sharpe is upward-biased by selection
bias. This module implements:

- **Probabilistic Sharpe Ratio (PSR)**: probability that the true Sharpe
  exceeds a benchmark threshold, accounting for sample size, skewness,
  and kurtosis of the return distribution.

- **Deflated Sharpe Ratio (DSR)**: PSR with a benchmark threshold that is
  itself adjusted for the number of independent trials performed (e.g.
  number of parameter combinations searched). Corrects for selection bias
  due to multiple testing.

References:
    - Bailey, D. & Lopez de Prado, M. (2012). "The Sharpe Ratio Efficient
      Frontier." Journal of Risk, 15(2), 13-50.
    - Bailey, D. & Lopez de Prado, M. (2014). "The Deflated Sharpe Ratio:
      Correcting for Selection Bias, Backtest Overfitting and Non-Normality."
      Journal of Portfolio Management, 40(5), 94-107.

The PSR test statistic is:

    PSR(SR_0) = Φ( (SR_obs - SR_0) × √(T-1) /
                   √(1 - γ_3 × SR_obs + ((κ - 1)/4) × SR_obs²) )

where:
    SR_obs = observed (non-annualized) Sharpe ratio
    SR_0   = benchmark Sharpe (for plain PSR usually 0)
    T      = number of return observations
    γ_3    = skewness of returns
    κ      = regular kurtosis of returns
    Φ      = standard normal CDF

Internally scipy returns excess kurtosis (κ - 3), so the implementation uses
((excess_kurtosis + 2) / 4) in the denominator.

For DSR, SR_0 is replaced by the expected maximum Sharpe under N independent
trials of zero-true-Sharpe strategies:

    SR_0_DSR = √V × ( (1 - γ) × Φ⁻¹(1 - 1/N) + γ × Φ⁻¹(1 - 1/(N·e)) )

where V is the variance of the trial Sharpes and γ ≈ 0.5772 (Euler-Mascheroni).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats

# Euler-Mascheroni constant
EULER_MASCHERONI = 0.5772156649015328606


@dataclass(frozen=True)
class SharpeInferenceResult:
    """Statistical inference for a Sharpe ratio.

    Attributes:
        sharpe_observed: Observed (non-annualized) Sharpe.
        sharpe_annualized: Annualized Sharpe (using freq).
        n_observations: Sample size T.
        skewness: Sample skewness of returns.
        excess_kurtosis: Sample excess kurtosis (kurtosis - 3).
        psr: Probabilistic Sharpe Ratio against threshold sharpe_threshold.
        sharpe_threshold: Threshold used for PSR (default 0).
        dsr: Deflated Sharpe Ratio (adjusted for n_trials).
        n_trials: Number of independent trials assumed for DSR.
        psr_significant: Whether PSR > 0.95 (i.e. ≥95% confidence true SR > threshold).
        dsr_significant: Whether DSR > 0.95 (significant after multiple-testing).
        ci_lower: Asymptotic 95% lower bound of true Sharpe.
        ci_upper: Asymptotic 95% upper bound of true Sharpe.
    """

    sharpe_observed: float
    sharpe_annualized: float
    n_observations: int
    skewness: float
    excess_kurtosis: float
    psr: float
    sharpe_threshold: float
    dsr: float
    n_trials: int
    psr_significant: bool
    dsr_significant: bool
    ci_lower: float
    ci_upper: float

    def to_dict(self) -> dict:
        """Serialize to JSON-friendly dict."""
        return {
            "sharpe_observed": _safe(self.sharpe_observed),
            "sharpe_annualized": _safe(self.sharpe_annualized),
            "n_observations": self.n_observations,
            "skewness": _safe(self.skewness),
            "excess_kurtosis": _safe(self.excess_kurtosis),
            "psr": _safe(self.psr),
            "sharpe_threshold": _safe(self.sharpe_threshold),
            "dsr": _safe(self.dsr),
            "n_trials": self.n_trials,
            "psr_significant": self.psr_significant,
            "dsr_significant": self.dsr_significant,
            "ci_lower": _safe(self.ci_lower),
            "ci_upper": _safe(self.ci_upper),
        }


def _safe(v: float) -> float | None:
    """Convert NaN/Inf to None for JSON serialization."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _compute_sharpe_std_error(
    sharpe_obs: float,
    n: int,
    skew: float,
    excess_kurt: float,
) -> float:
    """Asymptotic standard error of the Sharpe ratio estimator.

    Lo (2002) / Mertens (2002) closed-form:
        Var(SR) ≈ (1 - γ_3·SR + ((κ-1)/4)·SR²) / (T - 1)

    where κ is regular kurtosis. This function receives excess kurtosis
    (κ - 3), so ((κ - 1) / 4) = ((excess_kurt + 2) / 4).

    Returns the standard deviation (sqrt of variance).
    Returns NaN if T <= 1 or variance is non-positive.
    """
    if n <= 1:
        return float("nan")
    var = 1.0 - skew * sharpe_obs + ((excess_kurt + 2.0) / 4.0) * (sharpe_obs**2)
    if var <= 0:
        return float("nan")
    return math.sqrt(var / (n - 1))


def probabilistic_sharpe_ratio(
    returns: np.ndarray | list[float],
    sharpe_threshold: float = 0.0,
) -> float:
    """Compute the Probabilistic Sharpe Ratio.

    PSR(SR_0) = probability that the true Sharpe exceeds SR_0, given the
    observed sample Sharpe and accounting for skewness/kurtosis bias.

    PSR = Φ( (SR_obs - SR_0) × √(T-1) /
             √(1 - γ_3·SR_obs + ((κ-1)/4)·SR_obs²) )

    where κ is regular kurtosis. Internally scipy returns excess kurtosis,
    so the denominator uses ((excess_kurtosis + 2) / 4).

    Parameters:
        returns: Return series (any frequency; SR will be in matching unit).
        sharpe_threshold: Threshold Sharpe to test against (per-period).
            For "is true Sharpe > 0?", pass 0.0 (the default).

    Returns:
        PSR ∈ [0, 1]. Values > 0.95 indicate ≥95% confidence the true
        Sharpe exceeds the threshold. NaN if data is insufficient.
    """
    arr = np.asarray(returns, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n < 2:
        return float("nan")

    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))
    if std == 0:
        return float("nan")

    sharpe_obs = mean / std
    skew = float(stats.skew(arr, bias=False)) if n >= 3 else 0.0
    # scipy.stats.kurtosis with fisher=True returns excess kurtosis (=γ_4 - 3)
    excess_kurt = float(stats.kurtosis(arr, fisher=True, bias=False)) if n >= 4 else 0.0

    se = _compute_sharpe_std_error(sharpe_obs, n, skew, excess_kurt)
    if math.isnan(se) or se == 0:
        return float("nan")

    z = (sharpe_obs - sharpe_threshold) / se
    return float(stats.norm.cdf(z))


def expected_max_sharpe(n_trials: int, variance_of_trials: float = 1.0) -> float:
    """Expected maximum Sharpe under N independent trials of zero-true-Sharpe.

    Bailey & Lopez de Prado (2014) approximation based on the Gumbel
    extreme-value distribution:

        E[max SR_n] ≈ √V × ( (1-γ) × Φ⁻¹(1 - 1/N) + γ × Φ⁻¹(1 - 1/(N·e)) )

    where γ ≈ 0.5772 is the Euler-Mascheroni constant, V is the variance
    of the trial Sharpes, and Φ⁻¹ is the standard normal quantile.

    Parameters:
        n_trials: Number of independent strategy / parameter trials.
        variance_of_trials: Sample variance of the trial Sharpes (default 1).

    Returns:
        Expected maximum Sharpe from selection across N trials.
        Returns 0 for n_trials ≤ 1.
    """
    if n_trials <= 1:
        return 0.0

    # Use 1 - 1/N (avoid Φ⁻¹(1) = ∞ when N very small)
    p1 = 1.0 - 1.0 / n_trials
    p2 = 1.0 - 1.0 / (n_trials * math.e)

    # Cap probabilities to avoid infinite quantiles
    p1 = min(p1, 1.0 - 1e-10)
    p2 = min(p2, 1.0 - 1e-10)

    q1 = float(stats.norm.ppf(p1))
    q2 = float(stats.norm.ppf(p2))

    sd = math.sqrt(max(variance_of_trials, 0.0))
    return sd * ((1 - EULER_MASCHERONI) * q1 + EULER_MASCHERONI * q2)


def deflated_sharpe_ratio(
    returns: np.ndarray | list[float],
    n_trials: int,
    variance_of_trials: float | None = None,
) -> float:
    """Compute the Deflated Sharpe Ratio.

    DSR is PSR with the threshold replaced by E[max SR | N trials, true SR=0].
    This corrects for selection bias from multiple-testing.

    Parameters:
        returns: Observed return series.
        n_trials: Number of independent trials performed (e.g. parameter
            combinations in grid search). Must be ≥ 1.
        variance_of_trials: Variance of the Sharpe distribution across trials.
            If None, assumes V=1 (a reasonable proxy when actual trial-Sharpe
            variance is unknown; produces a moderately strict DSR).

    Returns:
        DSR ∈ [0, 1]. > 0.95 indicates statistically significant Sharpe
        even after accounting for the n_trials multiple-testing.
    """
    if n_trials < 1:
        n_trials = 1
    var_trials = 1.0 if variance_of_trials is None else max(variance_of_trials, 0.0)
    threshold = expected_max_sharpe(n_trials, var_trials)
    return probabilistic_sharpe_ratio(returns, sharpe_threshold=threshold)


def sharpe_inference(
    returns: np.ndarray | list[float],
    n_trials: int = 1,
    variance_of_trials: float | None = None,
    freq: int = 252,
) -> SharpeInferenceResult | None:
    """Full Sharpe statistical inference: SR + PSR + DSR + CI.

    One-stop function: feed in a return series, get back the Sharpe ratio
    along with statistical confidence about whether it's "real".

    Parameters:
        returns: Daily (or other frequency) return series.
        n_trials: Number of independent trials in the parameter search /
            strategy selection that produced these returns. Default 1
            (no multiple-testing correction).
        variance_of_trials: Variance of trial Sharpes (for DSR threshold).
        freq: Periods per year for annualization (252 daily, 52 weekly, 12 monthly).

    Returns:
        SharpeInferenceResult or None if data insufficient.
    """
    arr = np.asarray(returns, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n < 2:
        return None

    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))
    if std == 0:
        return None

    sharpe_obs = mean / std
    sharpe_annualized = sharpe_obs * math.sqrt(freq)

    skew = float(stats.skew(arr, bias=False)) if n >= 3 else 0.0
    excess_kurt = (
        float(stats.kurtosis(arr, fisher=True, bias=False)) if n >= 4 else 0.0
    )

    psr = probabilistic_sharpe_ratio(arr, sharpe_threshold=0.0)
    dsr = deflated_sharpe_ratio(arr, n_trials=n_trials, variance_of_trials=variance_of_trials)

    # 95% CI for the true (non-annualized) Sharpe via Lo (2002) SE
    se = _compute_sharpe_std_error(sharpe_obs, n, skew, excess_kurt)
    if math.isnan(se):
        ci_lower = float("nan")
        ci_upper = float("nan")
    else:
        z95 = 1.959963984540054  # Φ⁻¹(0.975)
        ci_lower = (sharpe_obs - z95 * se) * math.sqrt(freq)
        ci_upper = (sharpe_obs + z95 * se) * math.sqrt(freq)

    return SharpeInferenceResult(
        sharpe_observed=sharpe_obs,
        sharpe_annualized=sharpe_annualized,
        n_observations=n,
        skewness=skew,
        excess_kurtosis=excess_kurt,
        psr=psr,
        sharpe_threshold=0.0,
        dsr=dsr,
        n_trials=n_trials,
        psr_significant=(not math.isnan(psr)) and psr > 0.95,
        dsr_significant=(not math.isnan(dsr)) and dsr > 0.95,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
    )


__all__ = [
    "SharpeInferenceResult",
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "probabilistic_sharpe_ratio",
    "sharpe_inference",
]
