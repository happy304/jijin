"""Strategy-aware Monte Carlo simulation engine.

Extends the base Monte Carlo engine to incorporate strategy-specific
investment behaviors:

- DCA (定投): Periodic fixed-amount investments with optional smart modes
- Momentum (动量轮动): Periodic rebalancing to top-N funds by score
- Risk Parity (风险平价): Periodic rebalancing to equal-risk-contribution weights
- Mean-Variance (均值方差): Periodic rebalancing to optimal weights
- Timing (择时): Signal-based position sizing (fully invested or cash)
- FOF: Multi-factor scoring with periodic rebalancing

Mathematical foundations:
- DCA terminal wealth: W(T) = Σ_{k=0}^{N-1} C_k * S(T)/S(t_k)
  where C_k is the investment at time t_k, S(t) is the price path
- Momentum scoring: score_i = (S_i(t) / S_i(t - L) - 1) for return-based
- Risk Parity: solve for w such that w_i * (Σw)_i = budget/n for all i
- Timing: position = 1 if signal > threshold else 0

References:
- Kirkby et al. (2020) "An Analysis of Dollar Cost Averaging"
- Roncalli (2013) "Introduction to Risk Parity and Budgeting"
- Jegadeesh & Titman (1993) "Returns to Buying Winners"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from app.domain.simulation.monte_carlo import (
    MonteCarloEngine,
    SimulationConfig,
    SimulationResult,
)


# ---------------------------------------------------------------------------
# Strategy Configuration
# ---------------------------------------------------------------------------

FREQ_TO_DAYS = {
    "daily": 1,
    "weekly": 5,
    "biweekly": 10,
    "monthly": 21,
    "quarterly": 63,
}


@dataclass
class StrategySimConfig:
    """Strategy-specific simulation parameters."""

    strategy_type: str  # dca/momentum/risk_parity/mean_variance/timing/fof
    params: dict = field(default_factory=dict)
    universe_codes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Strategy-Aware Simulation Engine
# ---------------------------------------------------------------------------


class StrategySimulationEngine:
    """Monte Carlo engine that respects strategy investment behavior.

    Instead of assuming lump-sum buy-and-hold, this engine simulates
    the actual cash flows and rebalancing decisions of each strategy type.
    """

    def __init__(
        self,
        sim_config: SimulationConfig,
        strategy_config: StrategySimConfig,
    ) -> None:
        self.sim_config = sim_config
        self.strategy_config = strategy_config
        self._rng = np.random.default_rng(sim_config.random_seed)
        self._base_engine = MonteCarloEngine(sim_config)

    def run(
        self,
        historical_returns: pd.DataFrame | pd.Series,
        weights: NDArray[np.float64] | None = None,
        progress_callback: callable | None = None,
    ) -> SimulationResult:
        """Run strategy-aware Monte Carlo simulation.

        Dispatches to the appropriate strategy simulation method based on
        strategy_type. Falls back to base engine for unsupported types.
        """
        paths = self.simulate_paths(historical_returns, weights, progress_callback)

        if progress_callback:
            progress_callback(80, "计算风险指标")

        result = self._base_engine._compute_results(paths)

        if progress_callback:
            progress_callback(95, "生成分位数路径")

        return result

    def simulate_paths(
        self,
        historical_returns: pd.DataFrame | pd.Series,
        weights: NDArray[np.float64] | None = None,
        progress_callback: callable | None = None,
    ) -> NDArray[np.float64]:
        """Generate strategy-aware simulation paths without post-processing metrics."""
        strategy_type = self.strategy_config.strategy_type

        if strategy_type == "dca":
            return self._simulate_dca(historical_returns, progress_callback)
        if strategy_type == "momentum":
            return self._simulate_momentum(historical_returns, progress_callback)
        if strategy_type == "risk_parity":
            return self._simulate_risk_parity(historical_returns, progress_callback)
        if strategy_type == "mean_variance":
            return self._simulate_mean_variance(historical_returns, progress_callback)
        if strategy_type == "timing":
            return self._simulate_timing(historical_returns, progress_callback)
        if strategy_type == "fof":
            return self._simulate_fof(historical_returns, weights, progress_callback)
        return self._base_engine.simulate_paths(historical_returns, weights, progress_callback)

    def _compute_results(self, paths: NDArray[np.float64]) -> SimulationResult:
        """Delegate result computation to the shared base engine."""
        return self._base_engine._compute_results(paths)

    # ------------------------------------------------------------------
    # DCA (定投) Simulation
    # ------------------------------------------------------------------

    def _simulate_dca(
        self,
        historical_returns: pd.DataFrame | pd.Series,
        progress_callback: callable | None = None,
    ) -> NDArray[np.float64]:
        """Simulate Dollar-Cost Averaging strategy.

        Mathematical model:
        - At each investment date t_k, invest fixed amount C
        - Shares purchased: n_k = C / S(t_k)
        - Total shares at time T: N(T) = Σ n_k for all t_k <= T
        - Portfolio value at time t: V(t) = N(t) * S(t) + cash_reserve

        For smart DCA (均线偏离):
        - If S(t_k) < MA(t_k): invest 2*C (加倍)
        - If S(t_k) > MA(t_k) * (1 + threshold): invest 0.5*C (减半)

        For value averaging (价值平均):
        - Target value at t_k: V_target = C * k * (1 + g)^k
        - Invest: max(0, V_target - V_current)
        """
        params = self.strategy_config.params
        amount = float(params.get("amount", 1000))
        frequency = params.get("frequency", "monthly")
        dca_mode = params.get("dca_mode", "fixed")
        ma_window = int(params.get("ma_window", 250))

        invest_interval = FREQ_TO_DAYS.get(frequency, 21)
        n_steps = self.sim_config.horizon_days
        n_sims = self.sim_config.num_simulations
        initial_capital = self.sim_config.initial_capital

        # Get portfolio returns (single series)
        if isinstance(historical_returns, pd.Series):
            returns = historical_returns.dropna().values
        else:
            n_funds = historical_returns.shape[1]
            w = np.ones(n_funds) / n_funds
            returns = (historical_returns.dropna().values @ w)

        # Generate price paths using the selected method
        # We need daily price paths (normalized to 1.0 at start)
        price_paths = self._generate_normalized_paths(returns)
        # price_paths shape: (n_sims, n_steps + 1), starting at 1.0

        if progress_callback:
            progress_callback(40, "模拟定投现金流")

        # Simulate DCA cash flows on each path
        # Portfolio value = shares * current_price + uninvested_cash
        portfolio_paths = np.zeros((n_sims, n_steps + 1))

        # Initial state: invest initial_capital as first purchase
        # Then periodic investments of `amount`
        for sim_idx in range(n_sims):
            price_path = price_paths[sim_idx]  # normalized prices
            shares = initial_capital / price_path[0]  # initial lump sum
            cash = 0.0
            total_invested = initial_capital

            portfolio_paths[sim_idx, 0] = shares * price_path[0]

            for t in range(1, n_steps + 1):
                current_price = price_path[t]

                # Check if this is an investment day
                if t % invest_interval == 0:
                    invest_amount = self._compute_dca_amount(
                        dca_mode, amount, current_price, price_path,
                        t, ma_window, shares, total_invested, invest_interval,
                    )
                    if invest_amount > 0:
                        new_shares = invest_amount / current_price
                        shares += new_shares
                        total_invested += invest_amount

                portfolio_paths[sim_idx, t] = shares * current_price

        if progress_callback:
            progress_callback(60, "定投路径生成完成")

        return portfolio_paths

    def _compute_dca_amount(
        self,
        mode: str,
        base_amount: float,
        current_price: float,
        price_path: NDArray[np.float64],
        t: int,
        ma_window: int,
        shares: float,
        total_invested: float,
        invest_interval: int,
    ) -> float:
        """Compute the investment amount for a DCA period.

        Args:
            mode: 'fixed', 'value_avg', or 'smart'
            base_amount: Base periodic investment amount
            current_price: Current normalized price
            price_path: Full price path up to current time
            t: Current time step
            ma_window: Moving average window for smart mode
            shares: Current shares held
            total_invested: Total amount invested so far
            invest_interval: Days between investments
        """
        if mode == "fixed":
            return base_amount

        elif mode == "smart":
            # Smart DCA: adjust amount based on MA deviation
            # If price < MA: invest 2x (buy more when cheap)
            # If price > MA * 1.1: invest 0.5x (buy less when expensive)
            lookback = min(t, ma_window)
            if lookback < 5:
                return base_amount
            ma = np.mean(price_path[max(0, t - lookback):t])
            deviation = (current_price - ma) / ma
            if deviation < -0.1:
                return base_amount * 2.0
            elif deviation < -0.05:
                return base_amount * 1.5
            elif deviation > 0.1:
                return base_amount * 0.5
            elif deviation > 0.05:
                return base_amount * 0.75
            return base_amount

        elif mode == "value_avg":
            # Value Averaging: invest enough to reach target value
            # Target grows linearly: V_target(k) = initial + amount * k
            k = t // invest_interval
            # Assume modest growth target (annualized 8%)
            growth_rate = 0.08 / 252 * invest_interval
            target_value = total_invested + base_amount * (1 + growth_rate) ** k
            current_value = shares * current_price
            needed = target_value - current_value
            # Don't sell (no negative investment), cap at 3x base
            return max(0.0, min(needed, base_amount * 3.0))

        return base_amount

    # ------------------------------------------------------------------
    # Momentum (动量轮动) Simulation
    # ------------------------------------------------------------------

    def _simulate_momentum(
        self,
        historical_returns: pd.DataFrame | pd.Series,
        progress_callback: callable | None = None,
    ) -> NDArray[np.float64]:
        """Simulate momentum rotation strategy.

        At each rebalance date:
        1. Compute momentum score for each fund over lookback period
        2. Select top-N funds by score
        3. Equal-weight the selected funds
        4. Hold until next rebalance

        Score types:
        - 'return': cumulative return over lookback
        - 'sharpe': annualized Sharpe ratio over lookback
        - 'ir': information ratio (excess return / tracking error)
        """
        params = self.strategy_config.params
        lookback_months = int(params.get("lookback_months", 6))
        top_n = int(params.get("top_n", 3))
        rebalance_freq = params.get("rebalance_freq", "monthly")
        score_factor = params.get("score_factor", "return")

        lookback_days = lookback_months * 21
        rebalance_interval = FREQ_TO_DAYS.get(rebalance_freq, 21)
        n_steps = self.sim_config.horizon_days
        n_sims = self.sim_config.num_simulations
        initial_capital = self.sim_config.initial_capital

        # For momentum, we need multi-fund returns
        if isinstance(historical_returns, pd.Series):
            # Single fund: fall back to base engine behavior
            returns = historical_returns.dropna().values
            price_paths = self._generate_normalized_paths(returns)
            return price_paths * initial_capital

        df = historical_returns.dropna()
        n_funds = df.shape[1]
        returns_matrix = df.values  # (n_hist, n_funds)

        if progress_callback:
            progress_callback(30, "生成多基金价格路径")

        # Generate correlated multi-fund paths
        # Use Cholesky decomposition to preserve correlation structure
        fund_paths = self._generate_correlated_paths(returns_matrix)
        # fund_paths shape: (n_sims, n_steps + 1, n_funds)

        if progress_callback:
            progress_callback(50, "模拟动量轮动")

        portfolio_paths = np.zeros((n_sims, n_steps + 1))
        portfolio_paths[:, 0] = initial_capital

        for sim_idx in range(n_sims):
            capital = initial_capital
            # Current weights (start equal)
            weights = np.ones(n_funds) / n_funds

            for t in range(1, n_steps + 1):
                # Daily return of current portfolio
                fund_daily_returns = (
                    fund_paths[sim_idx, t, :] / fund_paths[sim_idx, t - 1, :] - 1
                )
                daily_portfolio_return = np.dot(weights, fund_daily_returns)
                capital *= (1 + daily_portfolio_return)

                # Rebalance check
                if t % rebalance_interval == 0 and t >= lookback_days:
                    # Compute momentum scores from simulated paths
                    scores = self._compute_momentum_scores(
                        fund_paths[sim_idx, max(0, t - lookback_days):t + 1, :],
                        score_factor,
                    )
                    # Select top-N
                    top_indices = np.argsort(scores)[-top_n:]
                    weights = np.zeros(n_funds)
                    weights[top_indices] = 1.0 / top_n

                portfolio_paths[sim_idx, t] = capital

        if progress_callback:
            progress_callback(60, "动量轮动路径生成完成")

        return portfolio_paths

    def _compute_momentum_scores(
        self,
        price_window: NDArray[np.float64],
        score_factor: str,
    ) -> NDArray[np.float64]:
        """Compute momentum scores for fund selection.

        Args:
            price_window: Shape (lookback_days, n_funds), price paths
            score_factor: 'return', 'sharpe', or 'ir'

        Returns:
            Array of scores, one per fund.
        """
        n_funds = price_window.shape[1]
        scores = np.zeros(n_funds)

        if len(price_window) < 5:
            return scores

        # Daily returns from price window
        daily_rets = np.diff(price_window, axis=0) / price_window[:-1]

        for i in range(n_funds):
            fund_rets = daily_rets[:, i]
            valid = fund_rets[np.isfinite(fund_rets)]
            if len(valid) < 5:
                scores[i] = -np.inf
                continue

            if score_factor == "return":
                # Cumulative return over lookback
                scores[i] = price_window[-1, i] / price_window[0, i] - 1
            elif score_factor == "sharpe":
                # Annualized Sharpe ratio
                mean_r = np.mean(valid)
                std_r = np.std(valid, ddof=1)
                scores[i] = (mean_r / std_r * np.sqrt(252)) if std_r > 1e-10 else 0
            elif score_factor == "ir":
                # Information ratio (vs equal-weight benchmark)
                bench_rets = np.mean(daily_rets, axis=1)
                excess = valid[:len(bench_rets)] - bench_rets[:len(valid)]
                te = np.std(excess, ddof=1)
                scores[i] = (np.mean(excess) / te * np.sqrt(252)) if te > 1e-10 else 0

        return scores

    # ------------------------------------------------------------------
    # Risk Parity (风险平价) Simulation
    # ------------------------------------------------------------------

    def _simulate_risk_parity(
        self,
        historical_returns: pd.DataFrame | pd.Series,
        progress_callback: callable | None = None,
    ) -> NDArray[np.float64]:
        """Simulate risk parity strategy with periodic rebalancing.

        Risk parity allocates weights such that each asset contributes
        equally to portfolio risk:
            w_i * (Σw)_i = σ_p^2 / n  for all i

        For the simplified case (diagonal dominance), the analytical
        approximation is:
            w_i ∝ 1 / σ_i

        For the full case, we use the iterative CCD algorithm.
        """
        params = self.strategy_config.params
        rebalance_freq = params.get("rebalance_freq", "monthly")
        cov_method = params.get("cov_method", "sample")
        lookback_days = int(params.get("lookback_days", 252))

        rebalance_interval = FREQ_TO_DAYS.get(rebalance_freq, 21)
        n_steps = self.sim_config.horizon_days
        n_sims = self.sim_config.num_simulations
        initial_capital = self.sim_config.initial_capital

        if isinstance(historical_returns, pd.Series):
            returns = historical_returns.dropna().values
            price_paths = self._generate_normalized_paths(returns)
            return price_paths * initial_capital

        df = historical_returns.dropna()
        n_funds = df.shape[1]
        returns_matrix = df.values

        if progress_callback:
            progress_callback(30, "生成多基金价格路径")

        fund_paths = self._generate_correlated_paths(returns_matrix)

        if progress_callback:
            progress_callback(50, "模拟风险平价调仓")

        portfolio_paths = np.zeros((n_sims, n_steps + 1))
        portfolio_paths[:, 0] = initial_capital

        for sim_idx in range(n_sims):
            capital = initial_capital
            # Initial weights: inverse volatility (quick approximation)
            weights = self._compute_risk_parity_weights(
                returns_matrix[-lookback_days:], cov_method
            )

            for t in range(1, n_steps + 1):
                fund_daily_returns = (
                    fund_paths[sim_idx, t, :] / fund_paths[sim_idx, t - 1, :] - 1
                )
                daily_portfolio_return = np.dot(weights, fund_daily_returns)
                capital *= (1 + daily_portfolio_return)

                # Rebalance
                if t % rebalance_interval == 0:
                    # Use simulated path data for covariance estimation
                    lookback_start = max(0, t - lookback_days)
                    sim_returns = np.diff(
                        fund_paths[sim_idx, lookback_start:t + 1, :], axis=0
                    ) / fund_paths[sim_idx, lookback_start:t, :]
                    if len(sim_returns) >= 30:
                        weights = self._compute_risk_parity_weights(
                            sim_returns, cov_method
                        )

                portfolio_paths[sim_idx, t] = capital

        if progress_callback:
            progress_callback(60, "风险平价路径生成完成")

        return portfolio_paths

    def _compute_risk_parity_weights(
        self,
        returns: NDArray[np.float64],
        cov_method: str = "sample",
    ) -> NDArray[np.float64]:
        """Compute risk parity weights using inverse-volatility approximation.

        For the full risk parity solution, we'd solve:
            min Σ_i Σ_j (w_i*(Σw)_i - w_j*(Σw)_j)^2

        The inverse-volatility approximation (w_i ∝ 1/σ_i) is a good
        first-order solution when correlations are moderate.

        For better accuracy with high correlations, we use the
        Cyclical Coordinate Descent (CCD) method from Griveau-Billion
        et al. (2013).
        """
        n_funds = returns.shape[1]

        if cov_method == "ewm":
            # Exponentially weighted covariance (halflife = 63 days)
            halflife = 63
            alpha = 1 - np.exp(-np.log(2) / halflife)
            n = len(returns)
            decay_weights = (1 - alpha) ** np.arange(n - 1, -1, -1)
            decay_weights /= decay_weights.sum()
            weighted_returns = returns * decay_weights[:, np.newaxis]
            cov_matrix = np.cov(weighted_returns.T, aweights=decay_weights)
        elif cov_method == "shrinkage":
            # Ledoit-Wolf shrinkage estimator (simplified)
            sample_cov = np.cov(returns.T)
            # Shrink toward diagonal
            target = np.diag(np.diag(sample_cov))
            # Shrinkage intensity (simplified constant)
            shrinkage = 0.3
            cov_matrix = (1 - shrinkage) * sample_cov + shrinkage * target
        else:
            cov_matrix = np.cov(returns.T)

        # Ensure positive semi-definite
        eigvals = np.linalg.eigvalsh(cov_matrix)
        if np.min(eigvals) < 0:
            cov_matrix += (-np.min(eigvals) + 1e-8) * np.eye(n_funds)

        # Inverse-volatility weights as starting point
        vols = np.sqrt(np.diag(cov_matrix))
        vols = np.maximum(vols, 1e-10)
        weights = (1.0 / vols)
        weights /= weights.sum()

        # Refine with CCD iterations (Roncalli's algorithm)
        budget = np.ones(n_funds) / n_funds  # equal risk budget
        weights = self._risk_parity_ccd(cov_matrix, budget, weights, max_iter=50)

        return weights

    @staticmethod
    def _risk_parity_ccd(
        cov: NDArray[np.float64],
        budget: NDArray[np.float64],
        w0: NDArray[np.float64],
        max_iter: int = 50,
        tol: float = 1e-8,
    ) -> NDArray[np.float64]:
        """Cyclical Coordinate Descent for risk parity.

        Solves: w_i * (Σw)_i = b_i * σ_p^2 for all i
        where b_i is the risk budget for asset i.

        Based on Griveau-Billion, Richard, Roncalli (2013):
        "A Fast Algorithm for Computing High-dimensional Risk Parity Portfolios"
        """
        n = len(w0)
        w = w0.copy()

        for _ in range(max_iter):
            w_old = w.copy()
            for i in range(n):
                # Marginal risk contribution of asset i
                sigma_w = cov @ w
                mrc_i = sigma_w[i]

                if mrc_i <= 0:
                    continue

                # Target: w_i * mrc_i = budget_i * total_risk
                total_risk = np.dot(w, sigma_w)
                target_contrib = budget[i] * total_risk

                # Update w_i
                # From quadratic: a*w_i^2 + b*w_i - target = 0
                a = cov[i, i]
                b = sigma_w[i] - cov[i, i] * w[i]
                # w_i = (-b + sqrt(b^2 + 4*a*target)) / (2*a)
                discriminant = b * b + 4 * a * target_contrib
                if discriminant >= 0 and a > 0:
                    w[i] = (-b + np.sqrt(discriminant)) / (2 * a)
                    w[i] = max(w[i], 1e-10)

            # Normalize
            w /= w.sum()

            # Check convergence
            if np.max(np.abs(w - w_old)) < tol:
                break

        return w

    # ------------------------------------------------------------------
    # Mean-Variance (均值方差) Simulation
    # ------------------------------------------------------------------

    def _simulate_mean_variance(
        self,
        historical_returns: pd.DataFrame | pd.Series,
        progress_callback: callable | None = None,
    ) -> NDArray[np.float64]:
        """Simulate mean-variance optimized portfolio with rebalancing.

        Markowitz optimization:
            max  w'μ - (λ/2) * w'Σw
            s.t. Σw_i = 1, w_i >= 0

        Uses quadratic programming via analytical solution for the
        unconstrained case, then clips and renormalizes for long-only.
        """
        params = self.strategy_config.params
        rebalance_freq = params.get("rebalance_freq", "monthly")
        risk_aversion = float(params.get("risk_aversion", 2.5))
        lookback_days = int(params.get("lookback_days", 252))

        rebalance_interval = FREQ_TO_DAYS.get(rebalance_freq, 21)
        n_steps = self.sim_config.horizon_days
        n_sims = self.sim_config.num_simulations
        initial_capital = self.sim_config.initial_capital

        if isinstance(historical_returns, pd.Series):
            returns = historical_returns.dropna().values
            price_paths = self._generate_normalized_paths(returns)
            return price_paths * initial_capital

        df = historical_returns.dropna()
        n_funds = df.shape[1]
        returns_matrix = df.values

        if progress_callback:
            progress_callback(30, "生成多基金价格路径")

        fund_paths = self._generate_correlated_paths(returns_matrix)

        if progress_callback:
            progress_callback(50, "模拟均值方差优化调仓")

        portfolio_paths = np.zeros((n_sims, n_steps + 1))
        portfolio_paths[:, 0] = initial_capital

        for sim_idx in range(n_sims):
            capital = initial_capital
            weights = self._compute_mv_weights(
                returns_matrix[-lookback_days:], risk_aversion
            )

            for t in range(1, n_steps + 1):
                fund_daily_returns = (
                    fund_paths[sim_idx, t, :] / fund_paths[sim_idx, t - 1, :] - 1
                )
                daily_portfolio_return = np.dot(weights, fund_daily_returns)
                capital *= (1 + daily_portfolio_return)

                if t % rebalance_interval == 0:
                    lookback_start = max(0, t - lookback_days)
                    sim_returns = np.diff(
                        fund_paths[sim_idx, lookback_start:t + 1, :], axis=0
                    ) / fund_paths[sim_idx, lookback_start:t, :]
                    if len(sim_returns) >= 30:
                        weights = self._compute_mv_weights(sim_returns, risk_aversion)

                portfolio_paths[sim_idx, t] = capital

        if progress_callback:
            progress_callback(60, "均值方差路径生成完成")

        return portfolio_paths

    def _compute_mv_weights(
        self,
        returns: NDArray[np.float64],
        risk_aversion: float,
    ) -> NDArray[np.float64]:
        """Compute mean-variance optimal weights (long-only).

        Analytical solution (unconstrained):
            w* = (1/λ) * Σ^{-1} * μ

        Then project to long-only simplex by clipping negatives
        and renormalizing.
        """
        mu = np.mean(returns, axis=0) * 252  # annualized expected returns
        cov = np.cov(returns.T) * 252  # annualized covariance

        n = len(mu)
        # Regularize covariance
        cov += 1e-6 * np.eye(n)

        try:
            cov_inv = np.linalg.inv(cov)
            w = (1.0 / risk_aversion) * cov_inv @ mu
        except np.linalg.LinAlgError:
            w = np.ones(n) / n

        # Long-only constraint: clip and normalize
        w = np.maximum(w, 0)
        w_sum = w.sum()
        if w_sum < 1e-10:
            w = np.ones(n) / n
        else:
            w /= w_sum

        return w

    # ------------------------------------------------------------------
    # Timing (择时) Simulation
    # ------------------------------------------------------------------

    def _simulate_timing(
        self,
        historical_returns: pd.DataFrame | pd.Series,
        progress_callback: callable | None = None,
    ) -> NDArray[np.float64]:
        """Simulate market timing strategy.

        Timing methods:
        - dual_ma: Long when short MA > long MA, else cash
        - macd: Long when MACD > signal line, else cash
        - valuation: Long when below historical percentile, else cash

        Position sizing is binary: 100% invested or 100% cash.
        Cash earns risk-free rate (annualized 2%).
        """
        params = self.strategy_config.params
        timing_method = params.get("timing_method", "dual_ma")
        short_window = int(params.get("short_window", 20))
        long_window = int(params.get("long_window", 60))
        threshold = float(params.get("threshold", 0.02))

        n_steps = self.sim_config.horizon_days
        n_sims = self.sim_config.num_simulations
        initial_capital = self.sim_config.initial_capital
        daily_rf = 0.02 / 252  # risk-free daily rate

        if isinstance(historical_returns, pd.Series):
            returns = historical_returns.dropna().values
        else:
            n_funds = historical_returns.shape[1]
            w = np.ones(n_funds) / n_funds
            returns = (historical_returns.dropna().values @ w)

        price_paths = self._generate_normalized_paths(returns)

        if progress_callback:
            progress_callback(40, "模拟择时信号")

        portfolio_paths = np.zeros((n_sims, n_steps + 1))
        portfolio_paths[:, 0] = initial_capital

        for sim_idx in range(n_sims):
            capital = initial_capital
            in_market = True  # start invested

            for t in range(1, n_steps + 1):
                price_return = price_paths[sim_idx, t] / price_paths[sim_idx, t - 1] - 1

                if in_market:
                    capital *= (1 + price_return)
                else:
                    capital *= (1 + daily_rf)

                # Generate timing signal
                if t >= long_window:
                    in_market = self._compute_timing_signal(
                        price_paths[sim_idx, :t + 1],
                        timing_method, short_window, long_window, threshold,
                    )

                portfolio_paths[sim_idx, t] = capital

        if progress_callback:
            progress_callback(60, "择时路径生成完成")

        return portfolio_paths

    def _compute_timing_signal(
        self,
        prices: NDArray[np.float64],
        method: str,
        short_window: int,
        long_window: int,
        threshold: float,
    ) -> bool:
        """Compute timing signal: True = invested, False = cash.

        Args:
            prices: Price path up to current time
            method: 'dual_ma', 'macd', or 'valuation'
            short_window: Short-term lookback
            long_window: Long-term lookback
            threshold: Signal threshold
        """
        if method == "dual_ma":
            # Dual moving average crossover
            short_ma = np.mean(prices[-short_window:])
            long_ma = np.mean(prices[-long_window:])
            # Buy when short MA crosses above long MA (with threshold)
            return bool(short_ma > long_ma * (1 + threshold))

        elif method == "macd":
            # MACD: difference between 12-day and 26-day EMA
            # Signal line: 9-day EMA of MACD
            ema_short = self._ema(prices, short_window)
            ema_long = self._ema(prices, long_window)
            macd = ema_short - ema_long
            signal = self._ema_from_values(
                np.array([macd]), 9
            ) if len(prices) > long_window else macd
            return bool(macd > signal + threshold * prices[-1])

        elif method == "valuation":
            # Valuation percentile: invest when price is below
            # historical median (contrarian)
            current = prices[-1]
            percentile = np.mean(prices[:-1] <= current)
            # Invest when valuation is below 60th percentile
            return bool(percentile < (0.6 + threshold))

        return True

    # ------------------------------------------------------------------
    # FOF Simulation
    # ------------------------------------------------------------------

    def _simulate_fof(
        self,
        historical_returns: pd.DataFrame | pd.Series,
        weights: NDArray[np.float64] | None = None,
        progress_callback: callable | None = None,
    ) -> NDArray[np.float64]:
        """Simulate FOF (Fund of Funds) strategy.

        Combines multi-factor scoring with periodic rebalancing:
        1. Score each fund using multiple factors (Sharpe, drawdown, return)
        2. Select top-N funds
        3. Optimize weights using specified method
        4. Rebalance periodically
        """
        params = self.strategy_config.params
        top_n = int(params.get("top_n", 5))
        rebalance_freq = params.get("rebalance_freq", "quarterly")
        optimization = params.get("optimization", "equal_weight")

        rebalance_interval = FREQ_TO_DAYS.get(rebalance_freq, 63)
        n_steps = self.sim_config.horizon_days
        n_sims = self.sim_config.num_simulations
        initial_capital = self.sim_config.initial_capital

        if isinstance(historical_returns, pd.Series):
            returns = historical_returns.dropna().values
            price_paths = self._generate_normalized_paths(returns)
            return price_paths * initial_capital

        df = historical_returns.dropna()
        n_funds = df.shape[1]
        returns_matrix = df.values

        if progress_callback:
            progress_callback(30, "生成多基金价格路径")

        fund_paths = self._generate_correlated_paths(returns_matrix)

        if progress_callback:
            progress_callback(50, "模拟 FOF 多因子选基")

        portfolio_paths = np.zeros((n_sims, n_steps + 1))
        portfolio_paths[:, 0] = initial_capital

        # Parse score weights
        score_weights_str = params.get("score_weights", '{"sharpe":0.4,"max_drawdown":0.3,"return":0.3}')
        if isinstance(score_weights_str, str):
            import json
            try:
                score_weights = json.loads(score_weights_str)
            except (json.JSONDecodeError, TypeError):
                score_weights = {"sharpe": 0.4, "max_drawdown": 0.3, "return": 0.3}
        else:
            score_weights = score_weights_str

        for sim_idx in range(n_sims):
            capital = initial_capital
            # Initial selection and weights
            selected, weights = self._fof_select_and_weight(
                returns_matrix, top_n, n_funds, optimization, score_weights
            )
            full_weights = np.zeros(n_funds)
            full_weights[selected] = weights

            for t in range(1, n_steps + 1):
                fund_daily_returns = (
                    fund_paths[sim_idx, t, :] / fund_paths[sim_idx, t - 1, :] - 1
                )
                daily_portfolio_return = np.dot(full_weights, fund_daily_returns)
                capital *= (1 + daily_portfolio_return)

                if t % rebalance_interval == 0 and t >= 63:
                    lookback_start = max(0, t - 252)
                    sim_returns = np.diff(
                        fund_paths[sim_idx, lookback_start:t + 1, :], axis=0
                    ) / fund_paths[sim_idx, lookback_start:t, :]
                    if len(sim_returns) >= 30:
                        selected, weights = self._fof_select_and_weight(
                            sim_returns, top_n, n_funds, optimization, score_weights
                        )
                        full_weights = np.zeros(n_funds)
                        full_weights[selected] = weights

                portfolio_paths[sim_idx, t] = capital

        if progress_callback:
            progress_callback(60, "FOF 路径生成完成")

        return portfolio_paths

    def _fof_select_and_weight(
        self,
        returns: NDArray[np.float64],
        top_n: int,
        n_funds: int,
        optimization: str,
        score_weights: dict,
    ) -> tuple[NDArray[np.int64], NDArray[np.float64]]:
        """Select top-N funds and compute weights for FOF.

        Multi-factor scoring:
        - sharpe: annualized Sharpe ratio
        - max_drawdown: inverse of max drawdown (lower DD = higher score)
        - return: annualized return
        """
        top_n = min(top_n, n_funds)
        scores = np.zeros(n_funds)

        for i in range(n_funds):
            fund_rets = returns[:, i]
            valid = fund_rets[np.isfinite(fund_rets)]
            if len(valid) < 20:
                scores[i] = -np.inf
                continue

            # Sharpe
            mean_r = np.mean(valid)
            std_r = np.std(valid, ddof=1)
            sharpe = (mean_r / std_r * np.sqrt(252)) if std_r > 1e-10 else 0

            # Max drawdown (from cumulative returns)
            cum = np.cumprod(1 + valid)
            running_max = np.maximum.accumulate(cum)
            dd = np.min((cum - running_max) / running_max)
            inv_dd = 1.0 / (abs(dd) + 0.01)  # inverse drawdown

            # Annualized return
            ann_return = mean_r * 252

            # Weighted score
            w_sharpe = score_weights.get("sharpe", 0.4)
            w_dd = score_weights.get("max_drawdown", 0.3)
            w_ret = score_weights.get("return", 0.3)
            scores[i] = w_sharpe * sharpe + w_dd * inv_dd + w_ret * ann_return * 10

        selected = np.argsort(scores)[-top_n:]

        # Compute weights for selected funds
        if optimization == "equal_weight":
            weights = np.ones(top_n) / top_n
        elif optimization == "risk_parity":
            sel_returns = returns[:, selected]
            if len(sel_returns) >= 30:
                weights = self._compute_risk_parity_weights(sel_returns, "sample")
            else:
                weights = np.ones(top_n) / top_n
        elif optimization == "min_variance":
            sel_returns = returns[:, selected]
            if len(sel_returns) >= 30:
                cov = np.cov(sel_returns.T)
                cov += 1e-6 * np.eye(top_n)
                try:
                    cov_inv = np.linalg.inv(cov)
                    ones = np.ones(top_n)
                    weights = cov_inv @ ones / (ones @ cov_inv @ ones)
                    weights = np.maximum(weights, 0)
                    weights /= weights.sum()
                except np.linalg.LinAlgError:
                    weights = np.ones(top_n) / top_n
            else:
                weights = np.ones(top_n) / top_n
        else:
            weights = np.ones(top_n) / top_n

        return selected, weights

    # ------------------------------------------------------------------
    # Path Generation Utilities
    # ------------------------------------------------------------------

    def _generate_normalized_paths(
        self,
        returns: NDArray[np.float64],
    ) -> NDArray[np.float64]:
        """Generate normalized price paths (starting at 1.0).

        Uses the configured simulation method (GBM/Bootstrap/Hybrid).
        Returns paths normalized to start at 1.0 for easy DCA/timing math.
        """
        n_steps = self.sim_config.horizon_days
        n_sims = self.sim_config.num_simulations
        method = self.sim_config.method

        if method == "gbm":
            # 从对数收益率估计参数（与 GBM 模型假设一致）
            log_returns_hist = np.log(1 + returns)
            mu = np.mean(log_returns_hist)
            sigma = np.std(log_returns_hist, ddof=1)
            Z = self._rng.standard_normal((n_sims, n_steps))
            log_returns = (mu - 0.5 * sigma**2) + sigma * Z
            cum_log = np.zeros((n_sims, n_steps + 1))
            cum_log[:, 1:] = np.cumsum(log_returns, axis=1)
            paths = np.exp(cum_log)

        elif method == "bootstrap":
            n_hist = len(returns)
            indices = self._rng.integers(0, n_hist, size=(n_sims, n_steps))
            sampled = returns[indices]
            cum = np.cumprod(1 + sampled, axis=1)
            paths = np.ones((n_sims, n_steps + 1))
            paths[:, 1:] = cum

        else:  # hybrid
            # 统一在对数收益率空间操作
            log_returns_hist = np.log(1 + returns)
            mu_log = np.mean(log_returns_hist)
            sigma_log = np.std(log_returns_hist, ddof=1)
            Z = self._rng.standard_normal((n_sims, n_steps))
            daily_rets = (mu_log - 0.5 * sigma_log**2) + sigma_log * Z
            # Replace extremes with empirical tail (in log-return space)
            threshold = 2.0 * sigma_log
            extreme_mask = np.abs(daily_rets - (mu_log - 0.5 * sigma_log**2)) > threshold
            n_extreme = extreme_mask.sum()
            if n_extreme > 0:
                tail_log_values = log_returns_hist[
                    np.abs(log_returns_hist - mu_log) > threshold
                ]
                if len(tail_log_values) > 0:
                    daily_rets[extreme_mask] = self._rng.choice(
                        tail_log_values, size=n_extreme
                    )
            cum_log = np.zeros((n_sims, n_steps + 1))
            cum_log[:, 1:] = np.cumsum(daily_rets, axis=1)
            paths = np.exp(cum_log)

        return paths

    def _generate_correlated_paths(
        self,
        returns_matrix: NDArray[np.float64],
    ) -> NDArray[np.float64]:
        """Generate correlated multi-fund price paths.

        Uses Cholesky decomposition to preserve the correlation structure
        observed in historical data:
            Z_correlated = L @ Z_independent
        where L is the lower Cholesky factor of the correlation matrix.

        参数估计修正（v2）：
        - GBM 和 Hybrid 方法从对数收益率估计 μ 和 Σ
        - 确保与对数正态模型假设一致

        Returns:
            Shape (n_sims, n_steps + 1, n_funds), normalized price paths.
        """
        n_steps = self.sim_config.horizon_days
        n_sims = self.sim_config.num_simulations
        n_funds = returns_matrix.shape[1]
        method = self.sim_config.method

        # 从对数收益率估计参数（GBM/Hybrid 使用）
        log_returns_matrix = np.log(1 + returns_matrix)
        mu_log = np.mean(log_returns_matrix, axis=0)  # (n_funds,)
        cov_log = np.cov(log_returns_matrix.T)  # (n_funds, n_funds)

        # Ensure positive definite for Cholesky
        eigvals = np.linalg.eigvalsh(cov_log)
        if np.min(eigvals) <= 0:
            cov_log += (-np.min(eigvals) + 1e-8) * np.eye(n_funds)

        try:
            L = np.linalg.cholesky(cov_log)
        except np.linalg.LinAlgError:
            # Fallback: use diagonal (independent)
            L = np.diag(np.sqrt(np.diag(cov_log)))

        if method == "gbm":
            sigma_log = np.sqrt(np.diag(cov_log))
            paths = np.ones((n_sims, n_steps + 1, n_funds))

            for t in range(n_steps):
                Z = self._rng.standard_normal((n_sims, n_funds))
                correlated_Z = Z @ L.T  # Apply correlation
                # GBM step for each fund (in log-return space)
                log_ret = (mu_log - 0.5 * sigma_log**2) + correlated_Z
                paths[:, t + 1, :] = paths[:, t, :] * np.exp(log_ret)

        elif method == "bootstrap":
            # Block bootstrap preserving cross-sectional correlation
            # Bootstrap 直接使用简单收益率，无需对数变换
            n_hist = len(returns_matrix)
            paths = np.ones((n_sims, n_steps + 1, n_funds))
            indices = self._rng.integers(0, n_hist, size=(n_sims, n_steps))

            for t in range(n_steps):
                sampled_rets = returns_matrix[indices[:, t], :]  # (n_sims, n_funds)
                paths[:, t + 1, :] = paths[:, t, :] * (1 + sampled_rets)

        else:  # hybrid
            sigma_log = np.sqrt(np.diag(cov_log))
            paths = np.ones((n_sims, n_steps + 1, n_funds))

            for t in range(n_steps):
                Z = self._rng.standard_normal((n_sims, n_funds))
                correlated_Z = Z @ L.T
                daily_log_rets = (mu_log - 0.5 * sigma_log**2) + correlated_Z
                # Replace extremes per fund (in log-return space)
                for f in range(n_funds):
                    threshold = 2.0 * sigma_log[f]
                    extreme = np.abs(daily_log_rets[:, f] - (mu_log[f] - 0.5 * sigma_log[f]**2)) > threshold
                    n_ext = extreme.sum()
                    if n_ext > 0:
                        tail_log = log_returns_matrix[:, f]
                        tail_vals = tail_log[np.abs(tail_log - mu_log[f]) > threshold]
                        if len(tail_vals) > 0:
                            daily_log_rets[extreme, f] = self._rng.choice(
                                tail_vals, size=n_ext
                            )
                paths[:, t + 1, :] = paths[:, t, :] * np.exp(daily_log_rets)

        return paths

    # ------------------------------------------------------------------
    # Helper utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _ema(prices: NDArray[np.float64], span: int) -> float:
        """Compute Exponential Moving Average (last value only).

        EMA_t = α * P_t + (1 - α) * EMA_{t-1}
        where α = 2 / (span + 1)
        """
        alpha = 2.0 / (span + 1)
        ema = prices[0]
        for p in prices[1:]:
            ema = alpha * p + (1 - alpha) * ema
        return float(ema)

    @staticmethod
    def _ema_from_values(values: NDArray[np.float64], span: int) -> float:
        """Compute EMA from a series of values."""
        if len(values) == 0:
            return 0.0
        alpha = 2.0 / (span + 1)
        ema = values[0]
        for v in values[1:]:
            ema = alpha * v + (1 - alpha) * ema
        return float(ema)
