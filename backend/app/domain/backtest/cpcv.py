"""Combinatorial Purged Cross-Validation (CPCV).

Lopez de Prado (2018) showed that Walk-Forward produces a single OOS path,
making it impossible to compute the **Probability of Backtest Overfitting
(PBO)**. CPCV generates C(N, N-k) backtest paths from N time-series splits,
each path being a combination of k test folds. This yields a distribution
of OOS performance from which PBO can be estimated.

Algorithm (simplified for fund backtesting):
    1. Split the full time series into N equal-length non-overlapping groups.
    2. For each combination of k groups chosen as the test set:
       a. The remaining N-k groups form the training set.
       b. Purge: remove ``purge_days`` from the end of each training group
          that is immediately before a test group.
       c. Embargo: skip ``embargo_days`` after each training group boundary.
       d. Run the strategy on the training set (parameter optimization).
       e. Evaluate on the test set (OOS performance).
    3. Collect all OOS Sharpe ratios → compute PBO.

PBO = fraction of OOS paths where the strategy that was "best in-sample"
      actually underperforms the median OOS strategy. PBO < 0.5 is good;
      PBO > 0.5 means the strategy is likely overfit.

References:
    - Bailey, Borwein, Lopez de Prado & Zhu (2017): "The Probability of
      Backtest Overfitting." Journal of Computational Finance.
    - Lopez de Prado (2018): "Advances in Financial Machine Learning", Ch. 12.
"""

from __future__ import annotations

import itertools
import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CPCVConfig:
    """CPCV configuration.

    Attributes:
        n_splits: Number of time-series groups (N). More splits = more
            combinations but shorter per-group data. Typical: 6-10.
        n_test_splits: Number of groups used as test in each combination (k).
            Must be < n_splits. Typical: 2.
        purge_days: Days to purge from training groups adjacent to test.
        embargo_days: Days to skip after purge boundary.
    """

    n_splits: int = 6
    n_test_splits: int = 2
    purge_days: int = 0
    embargo_days: int = 5

    def __post_init__(self) -> None:
        if self.n_splits < 3:
            raise ValueError(f"n_splits must be >= 3, got {self.n_splits}")
        if self.n_test_splits < 1 or self.n_test_splits >= self.n_splits:
            raise ValueError(
                f"n_test_splits must be in [1, n_splits-1], got {self.n_test_splits}"
            )
        if self.purge_days < 0:
            raise ValueError(f"purge_days must be >= 0, got {self.purge_days}")
        if self.embargo_days < 0:
            raise ValueError(f"embargo_days must be >= 0, got {self.embargo_days}")

    @property
    def n_combinations(self) -> int:
        """Total number of train/test combinations C(N, k)."""
        return math.comb(self.n_splits, self.n_test_splits)


@dataclass
class CPCVPath:
    """Result of a single CPCV train/test combination.

    Attributes:
        test_groups: Indices of groups used as test set.
        train_groups: Indices of groups used as training set.
        is_sharpe: In-sample Sharpe from training.
        oos_sharpe: Out-of-sample Sharpe from test.
        is_return: In-sample total return.
        oos_return: Out-of-sample total return.
    """

    test_groups: tuple[int, ...]
    train_groups: tuple[int, ...]
    is_sharpe: float = 0.0
    oos_sharpe: float = 0.0
    is_return: float = 0.0
    oos_return: float = 0.0


@dataclass
class CPCVResult:
    """CPCV analysis result.

    Attributes:
        paths: All evaluated combinations.
        pbo: Probability of Backtest Overfitting.
            Fraction of paths where the IS-best strategy underperforms
            the median OOS. PBO < 0.5 = likely not overfit.
        avg_oos_sharpe: Mean OOS Sharpe across all paths.
        std_oos_sharpe: Std of OOS Sharpe.
        avg_is_sharpe: Mean IS Sharpe.
        n_paths: Total number of paths evaluated.
        config: Configuration used.
        is_overfit: True if PBO > 0.5.
    """

    paths: list[CPCVPath] = field(default_factory=list)
    pbo: float = 0.0
    avg_oos_sharpe: float = 0.0
    std_oos_sharpe: float = 0.0
    avg_is_sharpe: float = 0.0
    n_paths: int = 0
    config: CPCVConfig = field(default_factory=CPCVConfig)
    is_overfit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "pbo": round(self.pbo, 4),
            "avg_oos_sharpe": round(self.avg_oos_sharpe, 4),
            "std_oos_sharpe": round(self.std_oos_sharpe, 4),
            "avg_is_sharpe": round(self.avg_is_sharpe, 4),
            "n_paths": self.n_paths,
            "is_overfit": self.is_overfit,
            "n_splits": self.config.n_splits,
            "n_test_splits": self.config.n_test_splits,
        }


# Type for the user-provided backtest function
# Input: list of date ranges [(start, end), ...] for training, and [(start, end), ...] for test
# Output: (is_sharpe, oos_sharpe, is_return, oos_return)
BacktestFn = Callable[
    [list[tuple[date, date]], list[tuple[date, date]]],
    tuple[float, float, float, float],
]


def run_cpcv(
    all_dates: list[date],
    backtest_fn: BacktestFn,
    config: CPCVConfig | None = None,
    max_paths: int | None = None,
) -> CPCVResult:
    """Run Combinatorial Purged Cross-Validation.

    Parameters:
        all_dates: Sorted list of all trading dates in the sample.
        backtest_fn: A callable that accepts (train_ranges, test_ranges)
            and returns (is_sharpe, oos_sharpe, is_return, oos_return).
            The caller is responsible for running the strategy on the
            given date ranges and computing metrics.
        config: CPCV configuration. Defaults to N=6, k=2.
        max_paths: If set, randomly sample this many combinations instead
            of exhaustively evaluating all C(N,k). Useful when C(N,k) is
            very large (e.g. N=10, k=3 → 120 combinations).

    Returns:
        CPCVResult with PBO and all path metrics.

    Raises:
        ValueError: If dates are insufficient for the requested splits.
    """
    if config is None:
        config = CPCVConfig()

    n = len(all_dates)
    if n < config.n_splits * 10:
        raise ValueError(
            f"Need at least {config.n_splits * 10} dates for {config.n_splits} splits, "
            f"got {n}"
        )

    # Split dates into N equal groups
    group_size = n // config.n_splits
    groups: list[list[date]] = []
    for i in range(config.n_splits):
        start_idx = i * group_size
        end_idx = start_idx + group_size if i < config.n_splits - 1 else n
        groups.append(all_dates[start_idx:end_idx])

    # Generate all C(N, k) combinations of test groups
    all_combos = list(itertools.combinations(range(config.n_splits), config.n_test_splits))

    # Optionally subsample
    if max_paths is not None and max_paths < len(all_combos):
        rng = np.random.default_rng(42)
        indices = rng.choice(len(all_combos), size=max_paths, replace=False)
        all_combos = [all_combos[i] for i in sorted(indices)]

    logger.info(
        "CPCV: %d splits, k=%d test, %d combinations to evaluate",
        config.n_splits,
        config.n_test_splits,
        len(all_combos),
    )

    paths: list[CPCVPath] = []

    for combo in all_combos:
        test_group_indices = set(combo)
        train_group_indices = tuple(
            i for i in range(config.n_splits) if i not in test_group_indices
        )

        # Build date ranges for train and test
        # Apply purge: for each training group that immediately precedes a test group,
        # remove purge_days from its end
        train_ranges: list[tuple[date, date]] = []
        for gi in train_group_indices:
            g = groups[gi]
            end_trim = 0
            # Check if the next group is a test group
            if gi + 1 in test_group_indices:
                end_trim = config.purge_days + config.embargo_days
            effective_end = max(0, len(g) - end_trim)
            if effective_end < 2:
                continue
            train_ranges.append((g[0], g[effective_end - 1]))

        test_ranges: list[tuple[date, date]] = []
        for gi in sorted(test_group_indices):
            g = groups[gi]
            # Apply embargo at the start of test groups that follow a training group
            start_trim = 0
            if gi - 1 in set(train_group_indices):
                start_trim = config.embargo_days
            effective_start = min(start_trim, len(g) - 1)
            test_ranges.append((g[effective_start], g[-1]))

        if not train_ranges or not test_ranges:
            continue

        try:
            is_sharpe, oos_sharpe, is_return, oos_return = backtest_fn(
                train_ranges, test_ranges
            )
        except Exception as exc:
            logger.warning("CPCV path %s failed: %s", combo, exc)
            continue

        paths.append(
            CPCVPath(
                test_groups=combo,
                train_groups=train_group_indices,
                is_sharpe=is_sharpe,
                oos_sharpe=oos_sharpe,
                is_return=is_return,
                oos_return=oos_return,
            )
        )

    if not paths:
        return CPCVResult(config=config)

    # Compute PBO
    # PBO = fraction of paths where the IS-best strategy underperforms median OOS
    # Simplified: for each path, check if its OOS Sharpe < median OOS Sharpe
    oos_sharpes = np.array([p.oos_sharpe for p in paths])
    is_sharpes = np.array([p.is_sharpe for p in paths])
    median_oos = float(np.median(oos_sharpes))

    # For each path: was it "IS-best" (above median IS) but "OOS-bad" (below median OOS)?
    # Standard PBO: rank IS performance, take the top-ranked path, check if its OOS < 0
    # Simplified version: fraction of paths where OOS < 0 among those with IS > median IS
    is_median = float(np.median(is_sharpes))
    is_good_mask = is_sharpes > is_median
    if is_good_mask.sum() > 0:
        pbo = float(np.mean(oos_sharpes[is_good_mask] < 0))
    else:
        pbo = 0.0

    avg_oos = float(np.mean(oos_sharpes))
    std_oos = float(np.std(oos_sharpes, ddof=1)) if len(oos_sharpes) > 1 else 0.0
    avg_is = float(np.mean(is_sharpes))

    return CPCVResult(
        paths=paths,
        pbo=pbo,
        avg_oos_sharpe=avg_oos,
        std_oos_sharpe=std_oos,
        avg_is_sharpe=avg_is,
        n_paths=len(paths),
        config=config,
        is_overfit=pbo > 0.5,
    )


__all__ = [
    "BacktestFn",
    "CPCVConfig",
    "CPCVPath",
    "CPCVResult",
    "run_cpcv",
]
