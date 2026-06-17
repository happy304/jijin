"""趋势类因子模块。

实现基于趋势跟踪的量化因子：
- trend_strength: 趋势强度（基于线性回归 R² 和斜率方向）
- momentum_decay: 动量衰减因子（近期动量相对远期动量的比值）
- dual_momentum: 双动量因子（绝对动量 × 相对动量信号）

所有函数遵循因子库契约：
- 输入：pd.Series（日期索引的净值序列）
- 输出：float（标量）或 pd.Series（滚动模式）
- 空数据/不足数据返回 np.nan，不抛异常
- 确定性：相同输入产生相同输出
"""

from __future__ import annotations

from typing import Union

import numpy as np
import pandas as pd

from app.domain.factors.registry import factor


# ---------------------------------------------------------------------------
# 趋势强度因子
# ---------------------------------------------------------------------------


@factor("trend_strength", category="trend", window=60)
def trend_strength(
    nav: pd.Series,
    window: int = 60,
) -> Union[float, pd.Series]:
    """趋势强度因子：基于线性回归 R² 和斜率方向。

    使用 OLS 线性回归拟合净值序列，R² 衡量趋势的线性程度，
    斜率方向决定正负号。值域 [-1, 1]：
    - +1: 完美上升趋势
    - -1: 完美下降趋势
    - 0: 无明显趋势（震荡）

    计算公式：trend_strength = sign(slope) × R²

    Parameters:
        nav: 日期索引的净值序列
        window: 回看窗口天数（默认 60 个交易日）

    Returns:
        趋势强度值 [-1, 1]，数据不足时返回 np.nan
    """
    if nav is None or len(nav) < window:
        return np.nan

    nav_clean = nav.dropna()
    if len(nav_clean) < window:
        return np.nan

    # 取最近 window 个数据点
    y = nav_clean.iloc[-window:].values.astype(np.float64)
    x = np.arange(len(y), dtype=np.float64)

    # OLS 线性回归
    x_mean = x.mean()
    y_mean = y.mean()

    ss_xy = ((x - x_mean) * (y - y_mean)).sum()
    ss_xx = ((x - x_mean) ** 2).sum()
    ss_yy = ((y - y_mean) ** 2).sum()

    if ss_xx == 0 or ss_yy == 0:
        return np.nan

    slope = ss_xy / ss_xx
    r_squared = (ss_xy**2) / (ss_xx * ss_yy)

    # 趋势强度 = sign(slope) × R²
    sign = 1.0 if slope > 0 else (-1.0 if slope < 0 else 0.0)
    return float(sign * r_squared)


# ---------------------------------------------------------------------------
# 动量衰减因子
# ---------------------------------------------------------------------------


@factor("momentum_decay", category="trend", window=120)
def momentum_decay(
    nav: pd.Series,
    short_window: int = 20,
    long_window: int = 120,
) -> float:
    """动量衰减因子：近期动量相对远期动量的比值。

    衡量动量是否在加速或减速：
    - > 1: 动量加速（近期表现优于长期趋势）
    - = 1: 动量稳定
    - < 1: 动量衰减（近期表现弱于长期趋势）
    - < 0: 动量反转（近期方向与长期相反）

    计算公式：
    short_mom = (NAV[-1] / NAV[-short_window]) - 1
    long_mom = (NAV[-1] / NAV[-long_window]) - 1
    decay = short_mom / (long_mom / (long_window / short_window))

    即：将长期动量按时间比例折算为短期等效，再与实际短期动量比较。

    Parameters:
        nav: 日期索引的净值序列
        short_window: 短期窗口（默认 20 个交易日，约 1 个月）
        long_window: 长期窗口（默认 120 个交易日，约 6 个月）

    Returns:
        动量衰减比率，数据不足时返回 np.nan
    """
    if nav is None or len(nav) < long_window:
        return np.nan

    nav_clean = nav.dropna()
    if len(nav_clean) < long_window:
        return np.nan

    current = float(nav_clean.iloc[-1])
    short_start = float(nav_clean.iloc[-short_window])
    long_start = float(nav_clean.iloc[-long_window])

    if short_start <= 0 or long_start <= 0 or current <= 0:
        return np.nan

    short_mom = current / short_start - 1.0
    long_mom = current / long_start - 1.0

    # 将长期动量按时间比例折算为短期等效
    time_ratio = short_window / long_window
    long_mom_equivalent = long_mom * time_ratio

    if abs(long_mom_equivalent) < 1e-10:
        # 长期动量接近零，无法计算比率
        if abs(short_mom) < 1e-10:
            return 1.0  # 两者都接近零，视为稳定
        return np.nan

    return float(short_mom / long_mom_equivalent)


# ---------------------------------------------------------------------------
# 双动量因子
# ---------------------------------------------------------------------------


@factor("dual_momentum", category="trend", window=252)
def dual_momentum(
    nav: pd.Series,
    benchmark_nav: pd.Series | None = None,
    lookback: int = 252,
    risk_free_rate: float = 0.02,
) -> float:
    """双动量因子：结合绝对动量和相对动量。

    Gary Antonacci 的双动量框架：
    1. 绝对动量：基金收益是否超过无风险利率
    2. 相对动量：基金收益是否超过基准

    输出值：
    - > 0: 双动量看多（绝对+相对均为正）
    - = 0: 信号中性
    - < 0: 双动量看空

    具体计算：
    abs_signal = 1 if (fund_return > rf_return) else -1
    rel_signal = 1 if (fund_return > benchmark_return) else -1
    dual_momentum = abs_signal × rel_signal × |fund_return - rf_return|

    Parameters:
        nav: 日期索引的基金净值序列
        benchmark_nav: 日期索引的基准净值序列（如沪深300）
        lookback: 回看窗口天数（默认 252，约 1 年）
        risk_free_rate: 年化无风险利率（默认 2%）

    Returns:
        双动量信号值，数据不足时返回 np.nan
    """
    if nav is None or len(nav) < lookback:
        return np.nan

    nav_clean = nav.dropna()
    if len(nav_clean) < lookback:
        return np.nan

    # 计算基金收益
    current = float(nav_clean.iloc[-1])
    start = float(nav_clean.iloc[-lookback])
    if start <= 0:
        return np.nan
    fund_return = current / start - 1.0

    # 计算无风险收益（按实际天数折算）
    n_days = lookback
    rf_return = risk_free_rate * (n_days / 252.0)

    # 绝对动量信号
    abs_signal = 1.0 if fund_return > rf_return else -1.0

    # 相对动量信号
    if benchmark_nav is not None and len(benchmark_nav) >= lookback:
        bench_clean = benchmark_nav.dropna()
        if len(bench_clean) >= lookback:
            bench_current = float(bench_clean.iloc[-1])
            bench_start = float(bench_clean.iloc[-lookback])
            if bench_start > 0:
                bench_return = bench_current / bench_start - 1.0
                rel_signal = 1.0 if fund_return > bench_return else -1.0
            else:
                rel_signal = 0.0
        else:
            rel_signal = 0.0
    else:
        # 无基准时，相对动量信号设为中性
        rel_signal = 1.0 if fund_return > 0 else -1.0

    # 双动量 = 绝对信号 × 相对信号 × |超额收益|
    excess = abs(fund_return - rf_return)
    return float(abs_signal * rel_signal * excess)
