"""中国 A 股交易日历模块。

提供交易日判断、交易日列表查询、下一交易日查询等功能。

数据源优先级：
1. exchange_calendars 库（XSHG 上交所日历，覆盖 2000 年至今）
2. 本地硬编码节假日集合（2015-2026，作为 fallback）

当 exchange_calendars 不可用时（未安装或初始化失败），自动回退到本地数据。

用法示例::

    from datetime import date
    from app.domain.backtest.calendar import (
        is_trading_day,
        trading_days,
        next_trading_day,
    )

    assert is_trading_day(date(2024, 1, 2))
    days = trading_days(date(2024, 1, 1), date(2024, 1, 31))
    nxt = next_trading_day(date(2024, 9, 30))
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING

_logger = logging.getLogger(__name__)
_warned_years: set[int] = set()


# ---------------------------------------------------------------------------
# exchange_calendars 集成
# ---------------------------------------------------------------------------

_xcal_available: bool = False
_xshg_calendar = None

try:
    import exchange_calendars as xcals

    # XSHG = 上海证券交易所（与深交所交易日完全一致）
    _xshg_calendar = xcals.get_calendar("XSHG")
    _xcal_available = True
    _logger.info(
        "exchange_calendars 已加载: XSHG 日历范围 %s ~ %s",
        _xshg_calendar.first_session.date(),
        _xshg_calendar.last_session.date(),
    )
except (ImportError, Exception) as _exc:
    _logger.warning(
        "exchange_calendars 不可用 (%s)，回退到本地硬编码日历（2015-2026）",
        str(_exc),
    )


def _xcal_is_trading_day(d: date) -> bool | None:
    """使用 exchange_calendars 判断是否为交易日。

    Returns:
        True/False 如果日期在日历范围内，None 如果超出范围。
    """
    if not _xcal_available or _xshg_calendar is None:
        return None

    import pandas as pd

    ts = pd.Timestamp(d)
    # 检查是否在日历覆盖范围内
    if ts < _xshg_calendar.first_session or ts > _xshg_calendar.last_session:
        return None

    return _xshg_calendar.is_session(ts)


def _xcal_trading_days(start: date, end: date) -> list[date] | None:
    """使用 exchange_calendars 获取交易日列表。

    Returns:
        交易日列表，如果日期范围超出日历覆盖则返回 None。
    """
    if not _xcal_available or _xshg_calendar is None:
        return None

    import pandas as pd

    ts_start = pd.Timestamp(start)
    ts_end = pd.Timestamp(end)

    # 检查是否完全在日历覆盖范围内
    if ts_start < _xshg_calendar.first_session:
        return None
    if ts_end > _xshg_calendar.last_session:
        # 部分超出：截断到日历最后一天
        ts_end = _xshg_calendar.last_session

    sessions = _xshg_calendar.sessions_in_range(ts_start, ts_end)
    return [s.date() for s in sessions]


# ---------------------------------------------------------------------------
# 本地 fallback 警告
# ---------------------------------------------------------------------------


def _warn_calendar_range(year: int) -> None:
    """对超出已知日历范围的年份记录一次警告。"""
    if year not in _warned_years:
        _warned_years.add(year)
        _logger.warning(
            "交易日历数据仅覆盖 2015-2026 年，%d 年的节假日未知，"
            "仅按周末判断非交易日，结果可能不准确。",
            year,
        )


# ---------------------------------------------------------------------------
# 中国 A 股节假日数据（2015-2025）
# 来源：中国证监会每年发布的休市安排公告
# ---------------------------------------------------------------------------

_HOLIDAYS_RAW: dict[int, list[str]] = {
    2015: [
        # 元旦
        "0101", "0102", "0103",
        # 春节
        "0218", "0219", "0220", "0221", "0222", "0223", "0224",
        # 清明
        "0405", "0406",
        # 劳动节
        "0501",
        # 端午
        "0620", "0622",
        # 中秋+国庆（抗战胜利纪念日调休）
        "0903", "0904",
        # 国庆+中秋
        "1001", "1002", "1003", "1004", "1005", "1006", "1007",
    ],
    2016: [
        # 元旦
        "0101",
        # 春节
        "0207", "0208", "0209", "0210", "0211", "0212", "0213",
        # 清明
        "0402", "0403", "0404",
        # 劳动节
        "0501", "0502",
        # 端午
        "0609", "0610", "0611",
        # 中秋
        "0915", "0916", "0917",
        # 国庆
        "1001", "1002", "1003", "1004", "1005", "1006", "1007",
    ],
    2017: [
        # 元旦
        "0101", "0102",
        # 春节
        "0127", "0128", "0129", "0130", "0131", "0201", "0202",
        # 清明
        "0402", "0403", "0404",
        # 劳动节
        "0501",
        # 端午
        "0528", "0529", "0530",
        # 国庆+中秋
        "1001", "1002", "1003", "1004", "1005", "1006", "1007", "1008",
    ],
    2018: [
        # 元旦
        "0101",
        # 春节
        "0215", "0216", "0217", "0218", "0219", "0220", "0221",
        # 清明
        "0405", "0406", "0407",
        # 劳动节
        "0429", "0430", "0501",
        # 端午
        "0616", "0617", "0618",
        # 中秋
        "0922", "0923", "0924",
        # 国庆
        "1001", "1002", "1003", "1004", "1005", "1006", "1007",
    ],
    2019: [
        # 元旦
        "0101",
        # 春节
        "0204", "0205", "0206", "0207", "0208", "0209", "0210",
        # 清明
        "0405",
        # 劳动节
        "0501", "0502", "0503", "0504",
        # 端午
        "0607",
        # 中秋
        "0913",
        # 国庆
        "1001", "1002", "1003", "1004", "1005", "1006", "1007",
    ],
    2020: [
        # 元旦
        "0101",
        # 春节（含疫情延长）
        "0124", "0125", "0126", "0127", "0128", "0129", "0130", "0131",
        # 清明
        "0404", "0405", "0406",
        # 劳动节
        "0501", "0502", "0503", "0504", "0505",
        # 端午
        "0625", "0626", "0627",
        # 国庆+中秋
        "1001", "1002", "1003", "1004", "1005", "1006", "1007", "1008",
    ],
    2021: [
        # 元旦
        "0101", "0102", "0103",
        # 春节
        "0211", "0212", "0213", "0214", "0215", "0216", "0217",
        # 清明
        "0403", "0404", "0405",
        # 劳动节
        "0501", "0502", "0503", "0504", "0505",
        # 端午
        "0612", "0613", "0614",
        # 中秋
        "0919", "0920", "0921",
        # 国庆
        "1001", "1002", "1003", "1004", "1005", "1006", "1007",
    ],
    2022: [
        # 元旦
        "0101", "0102", "0103",
        # 春节
        "0131", "0201", "0202", "0203", "0204", "0205", "0206",
        # 清明
        "0403", "0404", "0405",
        # 劳动节
        "0430", "0501", "0502", "0503", "0504",
        # 端午
        "0603", "0604", "0605",
        # 中秋
        "0910", "0911", "0912",
        # 国庆
        "1001", "1002", "1003", "1004", "1005", "1006", "1007",
    ],
    2023: [
        # 元旦
        "0101", "0102",
        # 春节
        "0121", "0122", "0123", "0124", "0125", "0126", "0127",
        # 清明
        "0405",
        # 劳动节
        "0429", "0430", "0501", "0502", "0503",
        # 端午
        "0622", "0623", "0624",
        # 中秋+国庆
        "0929", "0930", "1001", "1002", "1003", "1004", "1005", "1006",
    ],
    2024: [
        # 元旦
        "0101",
        # 春节（含2月9日调休）
        "0209", "0210", "0211", "0212", "0213", "0214", "0215", "0216", "0217",
        # 清明
        "0404", "0405", "0406",
        # 劳动节
        "0501", "0502", "0503", "0504", "0505",
        # 端午
        "0608", "0609", "0610",
        # 中秋
        "0915", "0916", "0917",
        # 国庆
        "1001", "1002", "1003", "1004", "1005", "1006", "1007",
    ],
    2025: [
        # 元旦
        "0101",
        # 春节
        "0128", "0129", "0130", "0131", "0201", "0202", "0203", "0204",
        # 清明
        "0404", "0405", "0406",
        # 劳动节
        "0501", "0502", "0503", "0504", "0505",
        # 端午
        "0531", "0601", "0602",
        # 中秋+国庆
        "1001", "1002", "1003", "1004", "1005", "1006", "1007", "1008",
    ],
    2026: [
        # 元旦 (Jan 1-3)
        "0101", "0102", "0103",
        # 春节 (Feb 15-23, 含周末; 交易日休市: Feb 16-23)
        "0215", "0216", "0217", "0218", "0219", "0220", "0221", "0222", "0223",
        # 清明 (Apr 4-6)
        "0404", "0405", "0406",
        # 劳动节 (May 1-5)
        "0501", "0502", "0503", "0504", "0505",
        # 端午 (Jun 19-21)
        "0619", "0620", "0621",
        # 中秋 (Sep 25-27)
        "0925", "0926", "0927",
        # 国庆 (Oct 1-7)
        "1001", "1002", "1003", "1004", "1005", "1006", "1007",
    ],
}


@lru_cache(maxsize=1)
def _build_holiday_set() -> frozenset[date]:
    """将原始节假日数据构建为 frozenset[date]，O(1) 查询。"""
    holidays: set[date] = set()
    for year, dates in _HOLIDAYS_RAW.items():
        for mmdd in dates:
            month = int(mmdd[:2])
            day = int(mmdd[2:])
            holidays.add(date(year, month, day))
    return frozenset(holidays)


def _holidays() -> frozenset[date]:
    """获取节假日集合（带缓存）。"""
    return _build_holiday_set()


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def is_trading_day(d: date) -> bool:
    """判断给定日期是否为交易日。

    优先使用 exchange_calendars（XSHG）判断，覆盖 2000 年至今。
    如果 exchange_calendars 不可用或日期超出其范围，回退到本地数据。

    交易日定义：工作日（周一至周五）且不在节假日集合中。

    Args:
        d: 待判断的日期

    Returns:
        True 表示是交易日，False 表示非交易日
    """
    # 优先使用 exchange_calendars
    xcal_result = _xcal_is_trading_day(d)
    if xcal_result is not None:
        return xcal_result

    # 回退到本地数据
    # 周末不是交易日
    if d.weekday() >= 5:
        return False
    # 节假日不是交易日
    if d in _holidays():
        return False
    # 超出已知日历范围时记录警告（仅首次）
    if d.year < 2015 or d.year > 2026:
        _warn_calendar_range(d.year)
    return True


def trading_days(start: date, end: date) -> list[date]:
    """返回 [start, end] 闭区间内的所有交易日列表。

    优先使用 exchange_calendars（XSHG），覆盖 2000 年至今。
    如果不可用或日期超出范围，回退到本地硬编码数据。

    Args:
        start: 起始日期（含）
        end: 结束日期（含）

    Returns:
        按日期升序排列的交易日列表

    Raises:
        ValueError: 如果 start > end
    """
    if start > end:
        raise ValueError(f"start ({start}) must not be after end ({end})")

    # 优先使用 exchange_calendars
    xcal_result = _xcal_trading_days(start, end)
    if xcal_result is not None:
        return xcal_result

    # 回退到本地数据
    holidays = _holidays()
    result: list[date] = []
    current = start
    one_day = timedelta(days=1)

    while current <= end:
        if current.weekday() < 5 and current not in holidays:
            result.append(current)
        current += one_day

    return result


def next_trading_day(d: date) -> date:
    """返回给定日期之后的下一个交易日。

    注意：如果 d 本身是交易日，返回的是 d 之后的下一个交易日，不包含 d。

    Args:
        d: 参考日期

    Returns:
        d 之后最近的交易日
    """
    # 优先使用 exchange_calendars
    if _xcal_available and _xshg_calendar is not None:
        import pandas as pd

        ts = pd.Timestamp(d)
        if ts < _xshg_calendar.last_session:
            try:
                # sessions_window 获取 d 之后的 session
                next_sessions = _xshg_calendar.sessions_window(
                    pd.Timestamp(d + timedelta(days=1)), count=1
                )
                if len(next_sessions) > 0:
                    return next_sessions[0].date()
            except Exception:
                pass  # 回退到本地逻辑

    # 回退到本地数据
    holidays = _holidays()
    current = d + timedelta(days=1)
    while True:
        if current.weekday() < 5 and current not in holidays:
            return current
        current += timedelta(days=1)


def prev_trading_day(d: date) -> date:
    """返回给定日期之前的上一个交易日。

    注意：如果 d 本身是交易日，返回的是 d 之前的上一个交易日，不包含 d。

    Args:
        d: 参考日期

    Returns:
        d 之前最近的交易日
    """
    # 优先使用 exchange_calendars
    if _xcal_available and _xshg_calendar is not None:
        import pandas as pd

        ts = pd.Timestamp(d)
        if ts > _xshg_calendar.first_session:
            try:
                # 获取 d 之前的 session
                prev_sessions = _xshg_calendar.sessions_window(
                    pd.Timestamp(d - timedelta(days=1)), count=-1
                )
                if len(prev_sessions) > 0:
                    return prev_sessions[-1].date()
            except Exception:
                pass  # 回退到本地逻辑

    # 回退到本地数据
    holidays = _holidays()
    current = d - timedelta(days=1)
    while True:
        if current.weekday() < 5 and current not in holidays:
            return current
        current -= timedelta(days=1)
