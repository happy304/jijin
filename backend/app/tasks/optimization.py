"""参数优化 Celery 任务模块。

提供并行回测任务，供 ParallelOptimizer 分发到 Celery worker 执行。

需求: 5.7
"""

from __future__ import annotations

from typing import Any

from app.tasks.celery_app import celery_app


@celery_app.task(
    name="app.tasks.optimization.run_optimization_backtest",
    queue="backtest",
    time_limit=600,
    soft_time_limit=550,
    acks_late=True,
)
def run_optimization_backtest(
    strategy_class_path: str,
    params: dict[str, Any],
    nav_data_serialized: dict[str, dict[str, str]],
    start_date: str,
    end_date: str,
    initial_capital: str,
    fund_meta_serialized: dict[str, dict[str, Any]] | None,
    objective: str,
) -> dict[str, Any]:
    """Celery 任务：运行单次参数优化回测。

    此任务被 ParallelOptimizer 分发到 backtest 队列并行执行。
    每个任务独立运行一次回测，返回指标结果。

    Args:
        strategy_class_path: 策略类完整模块路径
        params: 策略参数字典
        nav_data_serialized: 序列化的净值数据
        start_date: 回测起始日期 ISO 格式
        end_date: 回测结束日期 ISO 格式
        initial_capital: 初始资金字符串
        fund_meta_serialized: 序列化的基金元数据
        objective: 优化目标指标

    Returns:
        {"params": dict, "metrics": dict, "objective_value": float}
    """
    from app.services.optimization import _run_single_backtest_task

    return _run_single_backtest_task(
        strategy_class_path=strategy_class_path,
        params=params,
        nav_data_serialized=nav_data_serialized,
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        fund_meta_serialized=fund_meta_serialized,
        objective=objective,
    )
