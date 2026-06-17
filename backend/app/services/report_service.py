"""绩效报告生成器 — HTML/PDF 报告导出。

生成包含以下内容的绩效报告：
- 摘要指标表
- 净值曲线
- 月度收益热力图（年 × 月网格，颜色编码）
- 滚动 Sharpe 折线图
- 滚动 Beta 折线图
- 回撤持续时间分布（直方图）

报告以 HTML 格式输出，图表使用 matplotlib 生成并以 base64 内嵌。
支持保存为文件或返回 HTML 字符串。

需求: 6.6
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass, field
from typing import Optional

import matplotlib

matplotlib.use("Agg")  # Non-interactive backend for server-side rendering

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from app.domain.factors.benchmark import beta
from app.domain.factors.risk_adjusted import sharpe

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class ReportConfig:
    """报告生成配置。"""

    title: str = "绩效分析报告"
    rolling_window: int = 60
    figsize: tuple[int, int] = (10, 4)
    dpi: int = 100
    heatmap_cmap: str = "RdYlGn"
    font_family: str = "sans-serif"


# ---------------------------------------------------------------------------
# Chart generation helpers
# ---------------------------------------------------------------------------


def _fig_to_base64(fig: plt.Figure, dpi: int = 100) -> str:
    """将 matplotlib Figure 转为 base64 编码的 PNG 字符串。"""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def _generate_equity_curve(
    nav: pd.Series,
    benchmark_nav: Optional[pd.Series] = None,
    config: Optional[ReportConfig] = None,
) -> str:
    """生成净值曲线图，返回 base64 PNG。"""
    cfg = config or ReportConfig()
    fig, ax = plt.subplots(figsize=cfg.figsize)

    ax.plot(nav.index, nav.values, label="策略净值", linewidth=1.5, color="#2196F3")
    if benchmark_nav is not None and len(benchmark_nav) > 0:
        ax.plot(
            benchmark_nav.index,
            benchmark_nav.values,
            label="基准净值",
            linewidth=1.2,
            color="#9E9E9E",
            linestyle="--",
        )

    ax.set_title("净值曲线", fontsize=12)
    ax.set_xlabel("日期")
    ax.set_ylabel("净值")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()

    return _fig_to_base64(fig, cfg.dpi)


def _generate_monthly_heatmap(
    nav: pd.Series,
    config: Optional[ReportConfig] = None,
) -> str:
    """生成月度收益热力图（年 × 月网格），返回 base64 PNG。"""
    cfg = config or ReportConfig()

    # Compute daily returns then resample to monthly
    returns = nav.pct_change().dropna()
    if len(returns) == 0:
        fig, ax = plt.subplots(figsize=cfg.figsize)
        ax.text(0.5, 0.5, "数据不足", ha="center", va="center", fontsize=14)
        ax.set_axis_off()
        return _fig_to_base64(fig, cfg.dpi)

    # Monthly returns: compound daily returns within each month
    monthly = (1 + returns).resample("ME").prod() - 1

    # Build year × month pivot table
    monthly_df = pd.DataFrame(
        {
            "year": monthly.index.year,
            "month": monthly.index.month,
            "return": monthly.values,
        }
    )
    pivot = monthly_df.pivot_table(index="year", columns="month", values="return")

    # Ensure all 12 months are present
    for m in range(1, 13):
        if m not in pivot.columns:
            pivot[m] = np.nan
    pivot = pivot.reindex(columns=range(1, 13))

    fig, ax = plt.subplots(figsize=(cfg.figsize[0], max(3, len(pivot) * 0.6 + 1)))

    # Create heatmap using imshow
    data = pivot.values * 100  # Convert to percentage
    im = ax.imshow(
        data,
        cmap=cfg.heatmap_cmap,
        aspect="auto",
        vmin=-10,
        vmax=10,
    )

    # Labels
    month_labels = [f"{m}月" for m in range(1, 13)]
    ax.set_xticks(range(12))
    ax.set_xticklabels(month_labels)
    ax.set_yticks(range(len(pivot)))
    ax.set_yticklabels(pivot.index.astype(int))

    # Annotate cells
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            val = data[i, j]
            if not np.isnan(val):
                color = "white" if abs(val) > 5 else "black"
                ax.text(j, i, f"{val:.1f}%", ha="center", va="center", fontsize=8, color=color)

    ax.set_title("月度收益热力图 (%)", fontsize=12)
    fig.colorbar(im, ax=ax, shrink=0.8, label="%")
    fig.tight_layout()

    return _fig_to_base64(fig, cfg.dpi)


def _generate_rolling_sharpe(
    nav: pd.Series,
    window: int = 60,
    config: Optional[ReportConfig] = None,
) -> str:
    """生成滚动 Sharpe 折线图，返回 base64 PNG。"""
    cfg = config or ReportConfig()

    rolling_sharpe = sharpe(nav, window=window)
    if isinstance(rolling_sharpe, (int, float)) or len(rolling_sharpe) == 0:
        fig, ax = plt.subplots(figsize=cfg.figsize)
        ax.text(0.5, 0.5, "数据不足", ha="center", va="center", fontsize=14)
        ax.set_axis_off()
        return _fig_to_base64(fig, cfg.dpi)

    fig, ax = plt.subplots(figsize=cfg.figsize)
    valid = rolling_sharpe.dropna()
    if len(valid) > 0:
        ax.plot(valid.index, valid.values, linewidth=1.2, color="#4CAF50")
        ax.axhline(y=0, color="gray", linestyle="--", linewidth=0.8)
        ax.fill_between(
            valid.index,
            valid.values,
            0,
            where=valid.values >= 0,
            alpha=0.1,
            color="green",
        )
        ax.fill_between(
            valid.index,
            valid.values,
            0,
            where=valid.values < 0,
            alpha=0.1,
            color="red",
        )

    ax.set_title(f"滚动 Sharpe Ratio ({window}日窗口)", fontsize=12)
    ax.set_xlabel("日期")
    ax.set_ylabel("Sharpe Ratio")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()

    return _fig_to_base64(fig, cfg.dpi)


def _generate_rolling_beta(
    nav: pd.Series,
    benchmark_nav: pd.Series,
    window: int = 60,
    config: Optional[ReportConfig] = None,
) -> str:
    """生成滚动 Beta 折线图，返回 base64 PNG。"""
    cfg = config or ReportConfig()

    rolling_beta = beta(nav, benchmark_nav, window=window)
    if isinstance(rolling_beta, (int, float)) or len(rolling_beta) == 0:
        fig, ax = plt.subplots(figsize=cfg.figsize)
        ax.text(0.5, 0.5, "数据不足", ha="center", va="center", fontsize=14)
        ax.set_axis_off()
        return _fig_to_base64(fig, cfg.dpi)

    fig, ax = plt.subplots(figsize=cfg.figsize)
    valid = rolling_beta.dropna()
    if len(valid) > 0:
        ax.plot(valid.index, valid.values, linewidth=1.2, color="#FF9800")
        ax.axhline(y=1.0, color="gray", linestyle="--", linewidth=0.8, label="Beta=1")

    ax.set_title(f"滚动 Beta ({window}日窗口)", fontsize=12)
    ax.set_xlabel("日期")
    ax.set_ylabel("Beta")
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()

    return _fig_to_base64(fig, cfg.dpi)


def _compute_drawdown_durations(nav: pd.Series) -> list[int]:
    """计算所有回撤持续时间（交易日数）。

    回撤定义为从峰值下跌到恢复到新高的持续天数。
    """
    if nav is None or len(nav) < 2:
        return []

    nav_clean = nav.dropna()
    if len(nav_clean) < 2:
        return []

    cummax = nav_clean.cummax()
    drawdown = nav_clean / cummax - 1

    durations: list[int] = []
    current_duration = 0

    for dd in drawdown.values:
        if dd < 0:
            current_duration += 1
        else:
            if current_duration > 0:
                durations.append(current_duration)
            current_duration = 0

    # If still in drawdown at end, record it
    if current_duration > 0:
        durations.append(current_duration)

    return durations


def _generate_drawdown_duration_distribution(
    nav: pd.Series,
    config: Optional[ReportConfig] = None,
) -> str:
    """生成回撤持续时间分布直方图，返回 base64 PNG。"""
    cfg = config or ReportConfig()

    durations = _compute_drawdown_durations(nav)

    fig, ax = plt.subplots(figsize=cfg.figsize)

    if len(durations) == 0:
        ax.text(0.5, 0.5, "无回撤记录", ha="center", va="center", fontsize=14)
        ax.set_axis_off()
        return _fig_to_base64(fig, cfg.dpi)

    bins = min(20, max(5, len(set(durations))))
    ax.hist(durations, bins=bins, color="#F44336", alpha=0.7, edgecolor="white")
    ax.axvline(
        x=np.mean(durations),
        color="black",
        linestyle="--",
        linewidth=1.2,
        label=f"均值: {np.mean(durations):.0f}天",
    )

    ax.set_title("回撤持续时间分布", fontsize=12)
    ax.set_xlabel("持续天数")
    ax.set_ylabel("频次")
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3, axis="y")

    return _fig_to_base64(fig, cfg.dpi)


# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                         "Helvetica Neue", Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
            padding: 20px;
        }}
        .container {{
            max-width: 1100px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            padding: 40px;
        }}
        h1 {{
            text-align: center;
            color: #1a237e;
            margin-bottom: 8px;
            font-size: 24px;
        }}
        .subtitle {{
            text-align: center;
            color: #666;
            margin-bottom: 30px;
            font-size: 14px;
        }}
        h2 {{
            color: #333;
            border-bottom: 2px solid #2196F3;
            padding-bottom: 8px;
            margin: 30px 0 15px 0;
            font-size: 18px;
        }}
        .metrics-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 15px 0;
            font-size: 14px;
        }}
        .metrics-table th {{
            background: #f8f9fa;
            padding: 10px 12px;
            text-align: left;
            border: 1px solid #dee2e6;
            font-weight: 600;
        }}
        .metrics-table td {{
            padding: 8px 12px;
            border: 1px solid #dee2e6;
        }}
        .metrics-table tr:nth-child(even) {{
            background: #f8f9fa;
        }}
        .positive {{ color: #4CAF50; font-weight: 600; }}
        .negative {{ color: #F44336; font-weight: 600; }}
        .chart-section {{
            margin: 25px 0;
            text-align: center;
        }}
        .chart-section img {{
            max-width: 100%;
            height: auto;
            border-radius: 4px;
        }}
        .footer {{
            text-align: center;
            color: #999;
            font-size: 12px;
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #eee;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{title}</h1>
        <div class="subtitle">{subtitle}</div>

        <h2>摘要指标</h2>
        {metrics_table}

        <h2>净值曲线</h2>
        <div class="chart-section">
            <img src="data:image/png;base64,{equity_curve}" alt="净值曲线">
        </div>

        <h2>月度收益热力图</h2>
        <div class="chart-section">
            <img src="data:image/png;base64,{monthly_heatmap}" alt="月度收益热力图">
        </div>

        <h2>滚动 Sharpe Ratio</h2>
        <div class="chart-section">
            <img src="data:image/png;base64,{rolling_sharpe}" alt="滚动 Sharpe">
        </div>

        <h2>滚动 Beta</h2>
        <div class="chart-section">
            <img src="data:image/png;base64,{rolling_beta}" alt="滚动 Beta">
        </div>

        <h2>回撤持续时间分布</h2>
        <div class="chart-section">
            <img src="data:image/png;base64,{drawdown_distribution}" alt="回撤持续时间分布">
        </div>

        <div class="footer">
            报告由基金量化平台自动生成 | {generated_at}
        </div>
    </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Report Service
# ---------------------------------------------------------------------------


@dataclass
class ReportResult:
    """报告生成结果。"""

    html: str
    title: str
    generated_at: str
    charts: dict[str, str] = field(default_factory=dict)


class ReportService:
    """绩效报告生成服务。

    生成包含图表和指标的 HTML 报告，支持导出为文件。
    图表使用 matplotlib 生成并以 base64 内嵌到 HTML 中。
    """

    def __init__(self, config: Optional[ReportConfig] = None) -> None:
        """初始化报告服务。

        Parameters:
            config: 报告配置，为 None 时使用默认配置。
        """
        self.config = config or ReportConfig()

    def generate_report(
        self,
        nav: pd.Series,
        benchmark_nav: Optional[pd.Series] = None,
        strategy_name: str = "",
        metrics: Optional[dict] = None,
    ) -> ReportResult:
        """生成完整绩效报告。

        Parameters:
            nav: 策略净值序列（日期索引）。
            benchmark_nav: 基准净值序列（可选）。
            strategy_name: 策略名称。
            metrics: 预计算的绩效指标字典（可选）。
                如果不提供，将从 NAV 序列计算基本指标。

        Returns:
            ReportResult 包含 HTML 字符串和元数据。
        """
        generated_at = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        title = self.config.title
        subtitle = f"策略: {strategy_name}" if strategy_name else ""
        if len(nav) > 0:
            start_date = nav.index[0].strftime("%Y-%m-%d")
            end_date = nav.index[-1].strftime("%Y-%m-%d")
            subtitle += f" | 区间: {start_date} ~ {end_date}"

        # Generate charts
        charts: dict[str, str] = {}
        charts["equity_curve"] = _generate_equity_curve(nav, benchmark_nav, self.config)
        charts["monthly_heatmap"] = _generate_monthly_heatmap(nav, self.config)
        charts["rolling_sharpe"] = _generate_rolling_sharpe(
            nav, window=self.config.rolling_window, config=self.config
        )

        if benchmark_nav is not None and len(benchmark_nav) > 0:
            charts["rolling_beta"] = _generate_rolling_beta(
                nav, benchmark_nav, window=self.config.rolling_window, config=self.config
            )
        else:
            # Generate placeholder for rolling beta when no benchmark
            fig, ax = plt.subplots(figsize=self.config.figsize)
            ax.text(0.5, 0.5, "无基准数据", ha="center", va="center", fontsize=14)
            ax.set_axis_off()
            charts["rolling_beta"] = _fig_to_base64(fig, self.config.dpi)

        charts["drawdown_distribution"] = _generate_drawdown_duration_distribution(
            nav, self.config
        )

        # Build metrics table
        metrics_table = self._build_metrics_table(nav, benchmark_nav, metrics)

        # Render HTML
        html = _HTML_TEMPLATE.format(
            title=title,
            subtitle=subtitle,
            metrics_table=metrics_table,
            equity_curve=charts["equity_curve"],
            monthly_heatmap=charts["monthly_heatmap"],
            rolling_sharpe=charts["rolling_sharpe"],
            rolling_beta=charts["rolling_beta"],
            drawdown_distribution=charts["drawdown_distribution"],
            generated_at=generated_at,
        )

        return ReportResult(
            html=html,
            title=title,
            generated_at=generated_at,
            charts=charts,
        )

    def save_html(
        self,
        nav: pd.Series,
        output_path: str,
        benchmark_nav: Optional[pd.Series] = None,
        strategy_name: str = "",
        metrics: Optional[dict] = None,
    ) -> str:
        """生成报告并保存为 HTML 文件。

        Parameters:
            nav: 策略净值序列。
            output_path: 输出文件路径。
            benchmark_nav: 基准净值序列（可选）。
            strategy_name: 策略名称。
            metrics: 预计算的绩效指标字典（可选）。

        Returns:
            输出文件的绝对路径。
        """
        result = self.generate_report(
            nav=nav,
            benchmark_nav=benchmark_nav,
            strategy_name=strategy_name,
            metrics=metrics,
        )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result.html)

        logger.info("Report saved to %s", output_path)
        return output_path

    def _build_metrics_table(
        self,
        nav: pd.Series,
        benchmark_nav: Optional[pd.Series],
        metrics: Optional[dict],
    ) -> str:
        """构建摘要指标 HTML 表格。"""
        if metrics is not None:
            rows = self._metrics_dict_to_rows(metrics)
        else:
            rows = self._compute_basic_metrics(nav, benchmark_nav)

        if not rows:
            return "<p>无可用指标数据</p>"

        html_rows = []
        for name, value in rows:
            formatted = self._format_metric_value(value)
            html_rows.append(f"        <tr><td>{name}</td><td>{formatted}</td></tr>")

        return (
            '<table class="metrics-table">\n'
            "    <thead><tr><th>指标</th><th>数值</th></tr></thead>\n"
            "    <tbody>\n"
            + "\n".join(html_rows)
            + "\n    </tbody>\n</table>"
        )

    def _compute_basic_metrics(
        self,
        nav: pd.Series,
        benchmark_nav: Optional[pd.Series],
    ) -> list[tuple[str, Optional[float]]]:
        """从 NAV 序列计算基本指标。"""
        from app.domain.factors.returns import annualized_return, total_return
        from app.domain.factors.risk import max_drawdown, volatility
        from app.domain.factors.risk_adjusted import sharpe as sharpe_fn, sortino

        rows: list[tuple[str, Optional[float]]] = []

        if nav is None or len(nav) < 2:
            return rows

        tr = total_return(nav)
        rows.append(("总收益率", tr if not np.isnan(tr) else None))

        ar = annualized_return(nav)
        rows.append(("年化收益率", ar if not np.isnan(ar) else None))

        vol = volatility(nav)
        rows.append(("年化波动率", vol if not np.isnan(vol) else None))

        mdd = max_drawdown(nav)
        rows.append(("最大回撤", mdd if not np.isnan(mdd) else None))

        sr = sharpe_fn(nav)
        rows.append(("Sharpe Ratio", sr if not np.isnan(sr) else None))

        so = sortino(nav)
        rows.append(("Sortino Ratio", so if not np.isnan(so) else None))

        if benchmark_nav is not None and len(benchmark_nav) >= 2:
            b = beta(nav, benchmark_nav)
            rows.append(("Beta", b if not np.isnan(b) else None))

        return rows

    def _metrics_dict_to_rows(self, metrics: dict) -> list[tuple[str, Optional[float]]]:
        """将指标字典转为行列表。"""
        label_map = {
            "total_return": "总收益率",
            "annualized_return": "年化收益率",
            "volatility": "年化波动率",
            "max_drawdown": "最大回撤",
            "sharpe": "Sharpe Ratio",
            "sortino": "Sortino Ratio",
            "information_ratio": "Information Ratio",
            "beta": "Beta",
            "tracking_error": "跟踪误差",
            "calmar": "Calmar Ratio",
        }

        rows: list[tuple[str, Optional[float]]] = []

        # Handle nested dict (from PerformanceReport.to_dict())
        if "returns" in metrics:
            flat: dict[str, Optional[float]] = {}
            for section in ["returns", "risk", "risk_adjusted", "benchmark"]:
                if section in metrics and isinstance(metrics[section], dict):
                    flat.update(metrics[section])
            for key, label in label_map.items():
                if key in flat:
                    rows.append((label, flat[key]))
        else:
            # Flat dict
            for key, label in label_map.items():
                if key in metrics:
                    rows.append((label, metrics[key]))

        return rows

    @staticmethod
    def _format_metric_value(value: Optional[float]) -> str:
        """格式化指标值为 HTML 字符串。"""
        if value is None:
            return '<span style="color: #999;">N/A</span>'

        # Determine if this is a percentage-like value
        abs_val = abs(value)

        if abs_val < 10:
            # Likely a ratio (Sharpe, Beta, etc.) or small percentage
            if abs_val < 1 and abs_val > 0:
                # Percentage format
                formatted = f"{value * 100:.2f}%"
            else:
                formatted = f"{value:.4f}"
        else:
            formatted = f"{value:.2f}"

        css_class = ""
        if value > 0:
            css_class = ' class="positive"'
        elif value < 0:
            css_class = ' class="negative"'

        return f"<span{css_class}>{formatted}</span>"
