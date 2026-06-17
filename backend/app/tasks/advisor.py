"""交易建议定时生成任务（v3 增强版）。

每日 22:30（信号生成后30分钟）自动运行：
1. 加载所有活跃策略的基金池
2. 检测市场 regime 状态
3. 为每只基金生成综合交易建议（含相关性过滤和信号冷却）
4. 筛选高置信度建议
5. 自动保存建议结果到数据库
6. 通过通知模块推送给用户

v3 增强：
- 加载上次建议记录用于信号冷却
- 加载费率数据用于费用估算
- 通知内容包含 regime 状态和更丰富的信息
- 自动保存建议结果供前端历史查询

与 signals 任务的区别：
- signals: 单纯执行策略 on_bar，输出原始信号
- advisor: 综合技术面/动量/策略信号/预测/regime，输出可操作的买卖建议

Requirements: 交易建议功能
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.core.logging import get_logger
from app.services.advisor_execution import AdvisorExecutionBundle, AdvisorExecutionRequest, build_result_execution_context
from app.services.advisor_profiles import build_advisor_config
from app.tasks.celery_app import celery_app

log = get_logger(__name__)

def _build_advisor_config_for_risk_level(risk_level: str = "moderate"):
    """按风险偏好构建建议引擎配置。"""
    return build_advisor_config(risk_level)



@celery_app.task(
    name="app.tasks.advisor.generate_daily_advice",
    queue="backtest",
    bind=True,
    max_retries=2,
    soft_time_limit=30 * 60,
    time_limit=35 * 60,
)
def generate_daily_advice(self) -> dict[str, Any]:
    """每日自动生成交易建议并推送通知。

    流程：
    1. 从数据库加载所有策略的基金池（去重）
    2. 加载净值数据和最新策略信号
    3. 运行 TradingAdvisor 引擎
    4. 筛选高置信度建议（confidence > 0.5）
    5. 推送通知

    Returns:
        执行摘要
    """
    import asyncio

    from app.tasks.async_utils import run_async
    return run_async(_generate_advice_async())


async def _generate_advice_async() -> dict[str, Any]:
    """异步执行交易建议生成（v3 增强）。"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.services.advisor_feedback import AdvisorFeedbackLearner
    from app.services.trading_advisor import (
        AdvisorConfig,
        TradingAdvisor,
        detect_market_regime,
    )

    settings = get_settings()
    log.info("advisor.daily_task.start", version="v3")

    # 1. 加载所有策略的基金池
    fund_codes = _load_all_fund_codes()
    if not fund_codes:
        log.info("advisor.daily_task.no_funds")
        return {"status": "success", "message": "无基金需要分析", "advices_count": 0}

    log.info("advisor.daily_task.funds_loaded", count=len(fund_codes))

    # 2. 加载净值数据
    nav_data = _load_nav_data_sync(fund_codes)
    if not nav_data:
        log.warning("advisor.daily_task.no_nav_data")
        return {"status": "warning", "message": "无净值数据", "advices_count": 0}

    # 3. 加载最新策略信号
    strategy_signals = _load_signals_sync(fund_codes)

    # 4. 加载基金名称和类型
    fund_names = _load_fund_names_sync(fund_codes)
    fund_types = _load_fund_types_sync(fund_codes)

    # 5. v3: 加载费率数据
    fee_data = _load_fund_fees_sync(fund_codes)

    # 6. v3: 加载上次建议记录（用于信号冷却）
    last_advices = _load_last_advices_sync(fund_codes)

    # 7. v4: 计算截面因子评分
    cross_sectional_scores: dict[str, float] = {}
    try:
        from app.services.cross_sectional_scorer import (
            CrossSectionalConfig,
            cross_sectional_to_signal,
            run_cross_sectional_scoring,
        )

        # 按基金类型分组计算截面评分
        type_groups: dict[str | None, list[str]] = {}
        for code in fund_codes:
            ft = fund_types.get(code, (None, None))[0]
            if ft not in type_groups:
                type_groups[ft] = []
            type_groups[ft].append(code)

        cs_config = CrossSectionalConfig()

        for ft, codes_in_type in type_groups.items():
            # 构建该类型的基金数据
            type_fund_data = []
            for code in codes_in_type:
                if code in nav_data:
                    navs = [r[1] for r in nav_data[code]]
                    if len(navs) >= 252:
                        type_fund_data.append({
                            "fund_code": code,
                            "fund_name": fund_names.get(code),
                            "fund_type": ft,
                            "nav_values": navs,
                            "fund_size": None,  # 同步任务中暂不加载规模
                            "management_fee": None,
                        })

            if len(type_fund_data) >= cs_config.min_funds_for_ranking:
                cs_result = run_cross_sectional_scoring(type_fund_data, cs_config)
                for code in codes_in_type:
                    cross_sectional_scores[code] = cross_sectional_to_signal(
                        code, cs_result, cs_config
                    )
    except Exception as e:
        log.warning("advisor.cross_sectional_error", error=str(e))

    # 8. 运行建议引擎
    config = _build_advisor_config_for_risk_level("moderate")
    learned_weights = AdvisorFeedbackLearner.load_learned()
    advisor = TradingAdvisor(
        config=config,
        total_capital=100000.0,  # 默认10万作为参考基准
        last_advices=last_advices,
        cross_sectional_scores=cross_sectional_scores,
        learned_weights=learned_weights,
    )

    advices = advisor.generate_advice(
        fund_codes=fund_codes,
        nav_data=nav_data,
        strategy_signals=strategy_signals,
        fund_names=fund_names,
        fund_types=fund_types,
        fee_data=fee_data,
    )

    # 8. 筛选高置信度建议
    actionable = [a for a in advices if a.action != "hold" and a.confidence > 0.5]

    # 9. v3: 检测市场 regime（用于通知内容）
    regime_info = "normal"
    if nav_data:
        longest_code = max(nav_data.keys(), key=lambda c: len(nav_data[c]))
        longest_navs = [r[1] for r in nav_data[longest_code]]
        if len(longest_navs) >= 120:
            regime = detect_market_regime(longest_navs, config)
            regime_info = regime.regime

    log.info(
        "advisor.daily_task.advices_generated",
        total=len(advices),
        actionable=len(actionable),
        market_regime=regime_info,
    )

    # 10. v3: 自动保存建议结果到数据库
    _save_advice_result_sync(
        advices,
        fund_codes,
        learned_weights=learned_weights,
        cross_sectional_scores=cross_sectional_scores,
        strategy_signals=strategy_signals,
        nav_data=nav_data,
    )

    # 10.5: 刷新用户已保存的历史建议记录（用最新数据重新分析）
    refresh_count = _refresh_saved_advices_sync(nav_data, config)

    # 11. 推送通知（含 regime 信息）
    notification_result = _send_advice_notifications(actionable, regime_info)

    result = {
        "status": "success",
        "advice_date": date.today().isoformat(),
        "total_funds_analyzed": len(fund_codes),
        "advices_count": len(advices),
        "actionable_count": len(actionable),
        "buy_count": sum(1 for a in actionable if a.action == "buy"),
        "sell_count": sum(1 for a in actionable if a.action == "sell"),
        "market_regime": regime_info,
        "refreshed_saved_advices": refresh_count,
        "notification": notification_result,
    }

    # 标记任务完成（供 chain_guard 检测）
    try:
        from app.tasks.chain_guard import mark_task_done
        mark_task_done("daily-trading-advice")
    except Exception:
        pass

    log.info("advisor.daily_task.complete", **result)
    return result


def _load_all_fund_codes() -> list[str]:
    """从所有策略中提取基金代码（去重）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.data.models.strategies import Strategy

    settings = get_settings()
    engine = create_engine(settings.database_sync_url)
    fund_codes: set[str] = set()

    try:
        with Session(engine) as session:
            strategies = session.query(Strategy).all()
            for s in strategies:
                universe = s.universe
                if isinstance(universe, dict):
                    codes = universe.get("fund_codes", [])
                elif isinstance(universe, list):
                    codes = universe
                else:
                    codes = []
                fund_codes.update(codes)
    finally:
        engine.dispose()

    return list(fund_codes)[:50]  # 限制最多50只，避免超时


def _load_nav_data_sync(
    fund_codes: list[str],
    lookback_days: int = 750,
) -> dict[str, list[tuple[str, float]]]:
    """同步加载净值数据。"""
    from datetime import timedelta

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.core.config import get_settings

    if not fund_codes:
        return {}

    settings = get_settings()
    engine = create_engine(settings.database_sync_url)
    end_date = date.today()
    start_date = end_date - timedelta(days=lookback_days)

    nav_data: dict[str, list[tuple[str, float]]] = {}

    try:
        with Session(engine) as session:
            placeholders = ", ".join([f":code_{i}" for i in range(len(fund_codes))])
            query = text(
                f"SELECT fund_code, trade_date, COALESCE(adj_nav, unit_nav) as nav "
                f"FROM fund_nav "
                f"WHERE fund_code IN ({placeholders}) "
                f"AND trade_date BETWEEN :start_date AND :end_date "
                f"AND (adj_nav IS NOT NULL OR unit_nav IS NOT NULL) "
                f"ORDER BY fund_code, trade_date"
            )
            params: dict[str, Any] = {
                f"code_{i}": code for i, code in enumerate(fund_codes)
            }
            params["start_date"] = start_date
            params["end_date"] = end_date

            result = session.execute(query, params)
            for row in result:
                fund_code = row[0]
                trade_date = row[1]
                adj_nav = float(row[2])
                if fund_code not in nav_data:
                    nav_data[fund_code] = []
                nav_data[fund_code].append((str(trade_date), adj_nav))
    except Exception as e:
        log.error("advisor.load_nav_error", error=str(e))
    finally:
        engine.dispose()

    return nav_data


def _load_signals_sync(fund_codes: list[str]) -> dict[str, dict[str, Any]]:
    """同步加载最新策略信号。"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.core.config import get_settings

    if not fund_codes:
        return {}

    settings = get_settings()
    engine = create_engine(settings.database_sync_url)
    signals: dict[str, dict[str, Any]] = {}

    try:
        with Session(engine) as session:
            placeholders = ", ".join([f":code_{i}" for i in range(len(fund_codes))])
            # 使用子查询获取每只基金最新信号
            query = text(
                f"SELECT s.fund_code, s.direction, s.strength, "
                f"s.target_weight, s.reason "
                f"FROM signals s "
                f"INNER JOIN ("
                f"  SELECT fund_code, MAX(signal_date) as max_date "
                f"  FROM signals "
                f"  WHERE fund_code IN ({placeholders}) "
                f"  AND signal_date >= :min_date "
                f"  GROUP BY fund_code"
                f") latest ON s.fund_code = latest.fund_code "
                f"AND s.signal_date = latest.max_date"
            )
            params: dict[str, Any] = {
                f"code_{i}": code for i, code in enumerate(fund_codes)
            }
            params["min_date"] = date.today() - timedelta(days=90)  # 扩大到90天覆盖季频策略

            result = session.execute(query, params)
            for row in result:
                signals[row[0]] = {
                    "direction": row[1],
                    "strength": float(row[2]) if row[2] else None,
                    "target_weight": float(row[3]) if row[3] else None,
                    "reason": row[4],
                }
    except Exception as e:
        log.warning("advisor.load_signals_error", error=str(e))
    finally:
        engine.dispose()

    return signals


def _load_fund_names_sync(fund_codes: list[str]) -> dict[str, str]:
    """同步加载基金名称。"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.core.config import get_settings

    if not fund_codes:
        return {}

    settings = get_settings()
    engine = create_engine(settings.database_sync_url)
    names: dict[str, str] = {}

    try:
        with Session(engine) as session:
            placeholders = ", ".join([f":code_{i}" for i in range(len(fund_codes))])
            query = text(
                f"SELECT code, name FROM funds WHERE code IN ({placeholders})"
            )
            params = {f"code_{i}": code for i, code in enumerate(fund_codes)}
            result = session.execute(query, params)
            for row in result:
                names[row[0]] = row[1]
    except Exception:
        pass
    finally:
        engine.dispose()

    return names


def _load_fund_types_sync(fund_codes: list[str]) -> dict[str, tuple[str | None, str | None]]:
    """同步加载基金类型信息。"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.core.config import get_settings

    if not fund_codes:
        return {}

    settings = get_settings()
    engine = create_engine(settings.database_sync_url)
    types: dict[str, tuple[str | None, str | None]] = {}

    try:
        with Session(engine) as session:
            placeholders = ", ".join([f":code_{i}" for i in range(len(fund_codes))])
            query = text(
                f"SELECT code, fund_type, sub_type FROM funds "
                f"WHERE code IN ({placeholders})"
            )
            params = {f"code_{i}": code for i, code in enumerate(fund_codes)}
            result = session.execute(query, params)
            for row in result:
                types[row[0]] = (row[1], row[2])
    except Exception:
        pass
    finally:
        engine.dispose()

    return types


def _load_fund_fees_sync(fund_codes: list[str]) -> dict[str, dict[str, float]]:
    """同步加载基金费率数据（v3 新增）。"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.core.config import get_settings

    if not fund_codes:
        return {}

    settings = get_settings()
    engine = create_engine(settings.database_sync_url)
    fees: dict[str, dict[str, float]] = {}

    try:
        with Session(engine) as session:
            placeholders = ", ".join([f":code_{i}" for i in range(len(fund_codes))])
            query = text(
                f"SELECT fund_code, fee_type, MIN(rate) as min_rate "
                f"FROM fund_fees "
                f"WHERE fund_code IN ({placeholders}) "
                f"GROUP BY fund_code, fee_type"
            )
            params = {f"code_{i}": code for i, code in enumerate(fund_codes)}
            result = session.execute(query, params)
            for row in result:
                fund_code = row[0]
                fee_type = row[1]
                rate = float(row[2])
                if fund_code not in fees:
                    fees[fund_code] = {"subscribe_rate": 0.0, "redeem_rate": 0.0}
                if fee_type == "subscribe":
                    fees[fund_code]["subscribe_rate"] = rate
                elif fee_type == "redeem":
                    fees[fund_code]["redeem_rate"] = rate
    except Exception as e:
        log.warning("advisor.load_fees_error", error=str(e))
    finally:
        engine.dispose()

    return fees


def _load_last_advices_sync(fund_codes: list[str]) -> dict[str, dict[str, str]]:
    """同步加载上次建议记录，用于信号冷却（v3 新增）。

    Returns:
        {fund_code: {action: str, date: str}}
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.core.config import get_settings

    if not fund_codes:
        return {}

    settings = get_settings()
    engine = create_engine(settings.database_sync_url)
    last_advices: dict[str, dict[str, str]] = {}

    try:
        with Session(engine) as session:
            # 查找最近的建议记录
            query = text(
                "SELECT advice_date, advices FROM advisor_results "
                "ORDER BY created_at DESC LIMIT 3"
            )
            result = session.execute(query)
            for row in result:
                advice_date = str(row[0])
                advices_json = row[1]
                if not advices_json:
                    continue
                # advices_json 是 JSONB 列表
                if isinstance(advices_json, list):
                    for adv in advices_json:
                        code = adv.get("fund_code")
                        if code and code in fund_codes and code not in last_advices:
                            action = adv.get("action", "hold")
                            adv_date = adv.get("advice_date") or advice_date
                            last_advices[code] = {"action": action, "date": adv_date}
    except Exception as e:
        log.warning("advisor.load_last_advices_error", error=str(e))
    finally:
        engine.dispose()

    return last_advices


def _save_advice_result_sync(
    advices: list,
    fund_codes: list[str],
    *,
    learned_weights: Any | None = None,
    cross_sectional_scores: dict[str, float] | None = None,
    strategy_signals: dict[str, dict[str, Any]] | None = None,
    nav_data: dict[str, list[tuple[str, float]]] | None = None,
) -> None:
    """自动保存建议结果到数据库（v3 新增）。

    每日定时任务生成的建议自动保存，供前端历史查询。
    如果已有相同基金组合的记录则覆盖更新。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.config import get_settings

    if not advices:
        return

    settings = get_settings()
    engine = create_engine(settings.database_sync_url)

    try:
        from app.data.models.advisor_results import AdvisorResult

        with Session(engine) as session:
            advices_data = [a.to_dict() for a in advices]
            sorted_codes = sorted(fund_codes)
            summary = {
                "buy_count": sum(1 for a in advices if a.action == "buy"),
                "sell_count": sum(1 for a in advices if a.action == "sell"),
                "hold_count": sum(1 for a in advices if a.action == "hold"),
                "total_buy_amount": round(sum(a.suggested_amount for a in advices if a.action == "buy"), 2),
                "total_sell_amount": round(sum(a.suggested_amount for a in advices if a.action == "sell"), 2),
                "high_confidence_signals": sum(1 for a in advices if a.confidence > 0.6),
            }

            bundle = AdvisorExecutionBundle(
                config=build_advisor_config("moderate"),
                nav_data=nav_data or {},
                strategy_signals=strategy_signals or {},
                fund_names={a.fund_code: (a.fund_name or a.fund_code) for a in advices},
                fund_types={a.fund_code: (a.fund_type, None) for a in advices},
                fee_data={},
                fund_rules={},
                last_advices={},
                cross_sectional_scores=cross_sectional_scores or {},
                macro_score=0.0,
                engine_health=None,
                oos_snapshots={},
                learned_weights=learned_weights,
                execution_context={
                    "analysis_mode": "nightly",
                    "fund_count": len(sorted_codes),
                    "fund_codes": sorted_codes,
                    "strategy_signal_stats": {
                        "total": len(strategy_signals or {}),
                    },
                    "cross_sectional_coverage": {
                        "scored_funds": len(cross_sectional_scores or {}),
                    },
                },
            )
            request = AdvisorExecutionRequest(
                fund_codes=sorted_codes,
                total_capital=100000.0,
                risk_level="moderate",
                mode="nightly",
                enable_reliability_layers=False,
                enable_learned_weights=learned_weights is not None,
            )
            execution_context = build_result_execution_context(request, bundle, advices)

            from sqlalchemy import type_coerce
            from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB

            jsonb_val = type_coerce(sorted_codes, PG_JSONB)
            existing_rows = (
                session.query(AdvisorResult)
                .filter(
                    AdvisorResult.fund_codes.op("@>")(jsonb_val),
                    AdvisorResult.fund_codes.op("<@")(jsonb_val),
                    AdvisorResult.strategy_id.is_(None),
                    AdvisorResult.strategy_name == "每日自动建议",
                )
                .order_by(AdvisorResult.id.asc())
                .all()
            )

            if existing_rows:
                keep = existing_rows[0]
                for dup in existing_rows[1:]:
                    session.delete(dup)

                keep.advice_date = date.today()
                keep.advices = advices_data
                keep.summary = summary
                keep.learned_params_version_id = getattr(learned_weights, "version_id", None)
                keep.source_result_id = None
                keep.analysis_mode = "nightly"
                keep.execution_context = execution_context
                keep.tracked_returns = None
                keep.tracked_at = None
                keep.note = "系统每日 22:30 自动生成"
                session.commit()
                log.info("advisor.daily_task.result_updated", advices_count=len(advices))
            else:
                result = AdvisorResult(
                    advice_date=date.today(),
                    fund_codes=sorted_codes,
                    total_capital=100000.0,
                    risk_level="moderate",
                    strategy_id=None,
                    strategy_name="每日自动建议",
                    current_positions=None,
                    positions_detail=None,
                    learned_params_version_id=getattr(learned_weights, "version_id", None),
                    source_result_id=None,
                    analysis_mode="nightly",
                    execution_context=execution_context,
                    advices=advices_data,
                    summary=summary,
                    note="系统每日 22:30 自动生成",
                )
                session.add(result)
                session.commit()
                log.info("advisor.daily_task.result_saved", advices_count=len(advices))
    except Exception as e:
        log.warning("advisor.save_result_error", error=str(e))
    finally:
        engine.dispose()


def _refresh_saved_advices_sync(
    nav_data: dict[str, list[tuple[str, float]]],
    config: "AdvisorConfig",
) -> int:
    """刷新用户已保存的历史建议记录（用最新数据重新分析）。

    遍历 advisor_results 表中所有非"每日自动建议"的记录，
    对每条记录的基金组合重新运行分析引擎，更新建议内容。

    Returns:
        成功刷新的记录数
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.services.trading_advisor import (
        AdvisorConfig,
        TradingAdvisor,
    )

    settings = get_settings()
    engine = create_engine(settings.database_sync_url)
    refreshed = 0

    try:
        from app.data.models.advisor_results import AdvisorResult

        with Session(engine) as session:
            # 查找所有用户保存的记录（排除自动生成的）
            rows = (
                session.query(AdvisorResult)
                .filter(
                    (AdvisorResult.strategy_name != "每日自动建议")
                    | (AdvisorResult.strategy_name.is_(None))
                )
                .all()
            )

            if not rows:
                return 0

            # 加载辅助数据（复用已有的同步加载函数）
            all_codes: set[str] = set()
            for row in rows:
                if row.fund_codes:
                    all_codes.update(row.fund_codes)

            all_codes_list = list(all_codes)
            fund_names = _load_fund_names_sync(all_codes_list)
            fund_types = _load_fund_types_sync(all_codes_list)
            fee_data = _load_fund_fees_sync(all_codes_list)
            last_advices = _load_last_advices_sync(all_codes_list)

            # 对于 nav_data 中没有的基金，补充加载
            missing_codes = [c for c in all_codes_list if c not in nav_data]
            extra_nav = _load_nav_data_sync(missing_codes) if missing_codes else {}
            merged_nav = {**nav_data, **extra_nav}

            # 加载策略信号
            strategy_signals = _load_signals_sync(all_codes_list)

            for row in rows:
                try:
                    codes = row.fund_codes
                    if not codes:
                        continue

                    # 检查是否有足够的净值数据
                    row_nav = {c: merged_nav[c] for c in codes if c in merged_nav}
                    if not row_nav:
                        continue

                    # 确定风险等级和资金
                    risk_level = row.risk_level or "moderate"
                    row_config = build_advisor_config(risk_level)

                    total_capital = float(row.total_capital) if row.total_capital else 100000.0
                    current_positions = row.current_positions or {}
                    positions_detail = row.positions_detail or {}

                    # 运行建议引擎
                    advisor = TradingAdvisor(
                        config=row_config,
                        total_capital=total_capital,
                        current_positions=current_positions,
                        positions_detail=positions_detail,
                        last_advices=last_advices,
                        learned_weights=AdvisorFeedbackLearner.load_learned(),
                    )

                    new_advices = advisor.generate_advice(
                        fund_codes=codes,
                        nav_data=row_nav,
                        strategy_signals=strategy_signals,
                        fund_names=fund_names,
                        fund_types=fund_types,
                        fee_data=fee_data,
                    )

                    # 更新记录
                    advices_data = [a.to_dict() for a in new_advices]
                    summary = {
                        "buy_count": sum(1 for a in new_advices if a.action == "buy"),
                        "sell_count": sum(1 for a in new_advices if a.action == "sell"),
                        "hold_count": sum(1 for a in new_advices if a.action == "hold"),
                        "total_buy_amount": round(
                            sum(a.suggested_amount for a in new_advices if a.action == "buy"), 2
                        ),
                        "total_sell_amount": round(
                            sum(a.suggested_amount for a in new_advices if a.action == "sell"), 2
                        ),
                        "high_confidence_signals": sum(
                            1 for a in new_advices if a.confidence > 0.6
                        ),
                    }

                    row.advice_date = date.today()
                    row.advices = advices_data
                    row.summary = summary
                    row.tracked_returns = None
                    row.tracked_at = None
                    refreshed += 1

                except Exception as e:
                    log.warning(
                        "advisor.refresh_saved_error",
                        result_id=row.id,
                        error=str(e),
                    )
                    continue

            if refreshed > 0:
                session.commit()
                log.info("advisor.refresh_saved_complete", refreshed=refreshed)

    except Exception as e:
        log.warning("advisor.refresh_saved_advices_error", error=str(e))
    finally:
        engine.dispose()

    return refreshed


def _send_advice_notifications(advices: list, regime_info: str = "normal") -> dict[str, Any]:
    """推送交易建议通知（v3 增强：含 regime 信息）。"""
    from app.notify.service import NotificationService, SignalNotification

    if not advices:
        return {"total": 0, "sent": 0, "failed": 0}

    # v3: regime 中文标签
    regime_labels = {
        "bull": "🟢牛市",
        "bear": "🔴熊市",
        "crisis": "⚠️危机",
        "volatile": "🟡高波动",
        "normal": "⚪正常",
    }
    regime_text = regime_labels.get(regime_info, regime_info)

    notifications = []
    for advice in advices:
        # 构建通知内容
        action_text = {"buy": "买入", "sell": "卖出", "hold": "持有"}.get(
            advice.action, advice.action
        )
        fund_name = advice.fund_name or advice.fund_code

        reason_text = "；".join(advice.reasons[:3]) if advice.reasons else ""

        notifications.append(
            SignalNotification(
                strategy_id=0,
                strategy_name="交易建议引擎v3",
                fund_code=advice.fund_code,
                direction=advice.action,
                signal_date=advice.advice_date,
                strength=advice.confidence,
                target_weight=advice.position_after,
                reason=(
                    f"【{action_text}】{fund_name} "
                    f"建议金额 ¥{advice.suggested_amount:.0f} "
                    f"(置信度 {advice.confidence*100:.0f}%) "
                    f"[市场:{regime_text}] | {reason_text}"
                ),
            )
        )

    try:
        from app.notify.service import send_signal_notifications

        result = send_signal_notifications(notifications)
        return {
            "total": result.total,
            "sent": result.sent,
            "failed": result.failed,
        }
    except Exception as e:
        log.error("advisor.notification_error", error=str(e))
        return {"total": len(notifications), "sent": 0, "failed": len(notifications)}


__all__ = [
    "generate_daily_advice",
    "refresh_oos_validation_cache",
    "run_walk_forward_task",
    "compute_cross_sectional_scores",
    "validate_cross_sectional_ic",
]


@celery_app.task(
    name="app.tasks.advisor.validate_cross_sectional_ic",
    queue="backtest",
    bind=True,
    max_retries=0,
    soft_time_limit=15 * 60,
    time_limit=18 * 60,
)
def validate_cross_sectional_ic(self) -> dict[str, Any]:
    """每月自动验证截面因子 IC（v4 新增）。

    每月 1 日 04:00 运行：
    1. 对 stock/mixed/index 分别计算 12 期滚动截面 IC
    2. 如果 IC_mean 连续下降到 0.03 以下，发出告警
    3. 结果记录到日志，供前端引擎健康度页面展示

    Returns:
        各类型的 IC 验证结果
    """
    from app.tasks.async_utils import run_async
    return run_async(_validate_cs_ic_async())


async def _validate_cs_ic_async() -> dict[str, Any]:
    """异步执行截面 IC 验证。"""
    from app.data.session import get_engine, get_sessionmaker
    from app.services.cross_sectional_scorer import (
        CrossSectionalConfig,
        compute_cross_sectional_ic,
        load_fund_data_for_scoring,
    )

    log.info("cs_ic_validation.monthly.start")

    get_engine()
    factory = get_sessionmaker()

    fund_types_to_check = ["stock", "mixed", "index"]
    config = CrossSectionalConfig(min_funds_for_ranking=10)
    results: dict[str, Any] = {}
    alerts: list[str] = []

    async with factory() as session:
        for fund_type in fund_types_to_check:
            try:
                fund_data = await load_fund_data_for_scoring(
                    session, fund_type=fund_type, min_history_days=252
                )

                if len(fund_data) < 10:
                    results[fund_type] = {"status": "skipped", "reason": "基金数不足"}
                    continue

                ics = compute_cross_sectional_ic(
                    fund_data, forward_days=20, config=config,
                    n_periods=12, period_step_days=21,
                )

                composite_ic = ics.get("composite")
                composite_ir = ics.get("composite_ir", 0)
                composite_pos = ics.get("composite_positive_pct", 0)
                n_periods = ics.get("n_periods_computed", 0)

                results[fund_type] = {
                    "status": "ok",
                    "ic_mean": composite_ic,
                    "ic_ir": composite_ir,
                    "positive_pct": composite_pos,
                    "n_periods": n_periods,
                    "n_funds": ics.get("n_funds"),
                }

                # 告警判断
                if composite_ic is not None and composite_ic < 0.03:
                    alert_msg = (
                        f"⚠ {fund_type} 截面因子 IC 衰减: "
                        f"IC_mean={composite_ic:.4f} (阈值 0.03), "
                        f"建议降低该类型的截面权重"
                    )
                    alerts.append(alert_msg)
                    results[fund_type]["alert"] = alert_msg
                    log.warning("cs_ic_validation.decay_detected",
                               fund_type=fund_type, ic_mean=composite_ic)

            except Exception as e:
                results[fund_type] = {"status": "error", "error": str(e)}
                log.error("cs_ic_validation.error", fund_type=fund_type, error=str(e))

    # 发送告警通知
    if alerts:
        try:
            from app.notify.service import NotificationService, SignalNotification, send_signal_notifications
            notifications = [
                SignalNotification(
                    strategy_id=0,
                    strategy_name="截面因子IC监控",
                    fund_code="SYSTEM",
                    direction="hold",
                    signal_date=date.today().isoformat(),
                    strength=0.0,
                    target_weight=None,
                    reason="\n".join(alerts),
                )
            ]
            send_signal_notifications(notifications)
        except Exception as e:
            log.warning("cs_ic_validation.notify_error", error=str(e))

    result = {
        "status": "success",
        "date": date.today().isoformat(),
        "results": results,
        "alerts": alerts,
    }

    log.info("cs_ic_validation.monthly.complete", **{
        k: v for k, v in result.items() if k != "results"
    })
    return result


@celery_app.task(
    name="app.tasks.advisor.compute_cross_sectional_scores",
    queue="backtest",
    bind=True,
    max_retries=1,
    soft_time_limit=10 * 60,
    time_limit=12 * 60,
)
def compute_cross_sectional_scores(self) -> dict[str, Any]:
    """每日预计算截面因子评分（v4 新增）。

    在 daily-trading-advice 之前运行（22:15），
    为所有基金类型计算截面排名，结果缓存供后续使用。

    流程：
    1. 按基金类型分组加载数据
    2. 对每个类型运行截面因子评分
    3. 计算截面 IC（用于监控因子有效性）
    4. 将结果存入 Redis 缓存

    Returns:
        执行摘要
    """
    from app.tasks.async_utils import run_async
    return run_async(_compute_cross_sectional_async())


async def _compute_cross_sectional_async() -> dict[str, Any]:
    """异步执行截面因子评分。"""
    from app.data.session import get_engine, get_sessionmaker
    from app.services.cross_sectional_scorer import (
        CrossSectionalConfig,
        compute_cross_sectional_ic,
        load_fund_data_for_scoring,
        run_cross_sectional_scoring,
    )

    log.info("cross_sectional.daily_task.start")

    get_engine()
    session_factory = get_sessionmaker()

    fund_types_to_score = ["stock", "mixed", "bond", "index", "qdii", "fof"]
    config = CrossSectionalConfig()
    results_summary: dict[str, Any] = {}

    async with session_factory() as session:
        for fund_type in fund_types_to_score:
            try:
                fund_data = await load_fund_data_for_scoring(
                    session, fund_type=fund_type, min_history_days=252
                )

                if len(fund_data) < config.min_funds_for_ranking:
                    results_summary[fund_type] = {
                        "status": "skipped",
                        "reason": f"基金数不足 ({len(fund_data)}/{config.min_funds_for_ranking})",
                    }
                    continue

                # 运行截面评分
                cs_result = run_cross_sectional_scoring(fund_data, config)

                # 计算截面 IC（验证因子有效性）
                ics = compute_cross_sectional_ic(fund_data, forward_days=20, config=config)

                results_summary[fund_type] = {
                    "status": "success",
                    "n_funds": cs_result.n_funds_qualified,
                    "top_3": cs_result.top_funds[:3],
                    "composite_ic": ics.get("composite"),
                    "factor_ics": {
                        k: v for k, v in ics.items()
                        if k not in ("n_funds", "error", "composite")
                    },
                }

                log.info(
                    "cross_sectional.type_scored",
                    fund_type=fund_type,
                    n_funds=cs_result.n_funds_qualified,
                    composite_ic=ics.get("composite"),
                )

            except Exception as e:
                log.warning(
                    "cross_sectional.type_error",
                    fund_type=fund_type,
                    error=str(e),
                )
                results_summary[fund_type] = {
                    "status": "error",
                    "error": str(e),
                }

    result = {
        "status": "success",
        "date": date.today().isoformat(),
        "types_scored": len([r for r in results_summary.values() if r.get("status") == "success"]),
        "details": results_summary,
    }

    log.info("cross_sectional.daily_task.complete", **{
        k: v for k, v in result.items() if k != "details"
    })
    return result


@celery_app.task(
    name="app.tasks.advisor.refresh_oos_validation_cache",
    queue="backtest",
    bind=True,
    max_retries=0,
    soft_time_limit=5 * 60,
    time_limit=8 * 60,
)
def refresh_oos_validation_cache(
    self,
    risk_level: str = "moderate",
    lookback_days: int | None = None,
    n_folds: int = 5,
    rebalance_freq: int = 5,
    max_funds: int = 50,
    max_age_days: int = 1,
    dispatch_every_n: int = 10,
    dispatch_countdown_step: int = 30,
) -> dict[str, Any]:
    """批量刷新样本外验证缓存。

    该任务只负责枚举基金并派发单基金 Walk-Forward 子任务，
    避免单个批任务执行时间过长。
    """
    from app.services.advisor_oos import OOSValidationStore

    normalized_risk_level = risk_level if risk_level in {"conservative", "moderate", "aggressive"} else "moderate"
    requested_lookback_days = lookback_days if lookback_days and lookback_days > 0 else None
    max_funds = max(1, int(max_funds or 50))
    max_age_days = max(1, int(max_age_days or 1))
    dispatch_every_n = max(1, int(dispatch_every_n or 10))
    dispatch_countdown_step = max(0, int(dispatch_countdown_step or 0))

    fund_codes = _load_all_fund_codes()[:max_funds]
    stale_fund_codes = OOSValidationStore.stale_fund_codes(
        fund_codes,
        risk_level=normalized_risk_level,
        max_age_days=max_age_days,
    )

    submitted: list[dict[str, Any]] = []
    for idx, fund_code in enumerate(stale_fund_codes):
        batch_index = idx // dispatch_every_n
        countdown = batch_index * dispatch_countdown_step
        task = run_walk_forward_task.apply_async(
            kwargs={
                "fund_code": fund_code,
                "lookback_days": requested_lookback_days,
                "n_folds": n_folds,
                "rebalance_freq": rebalance_freq,
                "risk_level": normalized_risk_level,
            },
            countdown=countdown,
        )
        submitted.append({
            "fund_code": fund_code,
            "task_id": task.id,
            "countdown": countdown,
            "batch_index": batch_index,
        })

    result = {
        "status": "submitted",
        "date": date.today().isoformat(),
        "risk_level": normalized_risk_level,
        "total_funds": len(fund_codes),
        "submitted_count": len(submitted),
        "skipped_count": max(0, len(fund_codes) - len(stale_fund_codes)),
        "lookback_days": requested_lookback_days,
        "n_folds": n_folds,
        "rebalance_freq": rebalance_freq,
        "max_age_days": max_age_days,
        "dispatch_every_n": dispatch_every_n,
        "dispatch_countdown_step": dispatch_countdown_step,
        "submitted": submitted,
    }
    log.info(
        "advisor.oos_refresh_cache.submitted",
        **{k: v for k, v in result.items() if k != "submitted"},
    )
    return result


@celery_app.task(
    name="app.tasks.advisor.run_walk_forward_task",
    queue="backtest",
    bind=True,
    max_retries=0,
    soft_time_limit=10 * 60,  # 10 分钟软限制
    time_limit=12 * 60,  # 12 分钟硬限制
)
def run_walk_forward_task(
    self,
    fund_code: str,
    lookback_days: int | None = 750,
    n_folds: int = 5,
    rebalance_freq: int = 5,
    risk_level: str = "moderate",
) -> dict:
    """Celery 异步任务：运行 Walk-Forward 样本外验证。

    适用于数据量大、折叠数多的场景，避免 API 超时。
    结果通过 Celery result backend (Redis) 存储，前端可轮询获取。

    Args:
        fund_code: 基金代码
        lookback_days: 历史数据天数
        n_folds: 折叠数
        rebalance_freq: 调仓频率
        risk_level: 风险偏好

    Returns:
        WalkForwardResult.to_dict()
    """
    import asyncio

    from app.tasks.async_utils import run_async
    return run_async(
        _run_walk_forward_async(
            fund_code, lookback_days, n_folds, rebalance_freq, risk_level
        )
    )


async def _run_walk_forward_async(
    fund_code: str,
    lookback_days: int | None = 750,
    n_folds: int = 5,
    rebalance_freq: int = 5,
    risk_level: str = "moderate",
) -> dict:
    """异步执行 Walk-Forward 验证。"""
    from app.data.session import get_engine, get_sessionmaker
    from app.services.advisor_backtest import load_and_run_walk_forward

    normalized_lookback_days = lookback_days if lookback_days and lookback_days > 0 else None
    config = _build_advisor_config_for_risk_level(risk_level)

    get_engine()
    session_factory = get_sessionmaker()

    async with session_factory() as session:
        result = await load_and_run_walk_forward(
            fund_code=fund_code,
            session=session,
            lookback_days=normalized_lookback_days,
            n_folds=n_folds,
            rebalance_freq=rebalance_freq,
            config=config,
            risk_level=risk_level,
        )
        return result.to_dict()
