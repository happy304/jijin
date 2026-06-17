"""交易日历模块单元测试。

覆盖 2015-2025 年中国 A 股交易日历，验证：
- is_trading_day 正确判断交易日/非交易日
- trading_days 返回正确的交易日列表
- next_trading_day 返回正确的下一交易日
- prev_trading_day 返回正确的上一交易日
- 节假日（春节、国庆等）正确排除
- 周末正确排除
- 每年交易日数量在合理范围内（约 240-250 天）
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain.backtest.calendar import (
    is_trading_day,
    next_trading_day,
    prev_trading_day,
    trading_days,
)


# ---------------------------------------------------------------------------
# is_trading_day 基本测试
# ---------------------------------------------------------------------------


class TestIsTradingDay:
    """is_trading_day 函数测试。"""

    def test_normal_weekday_is_trading_day(self) -> None:
        """普通工作日应为交易日。"""
        # 2024-03-18 周一
        assert is_trading_day(date(2024, 3, 18)) is True

    def test_saturday_is_not_trading_day(self) -> None:
        """周六不是交易日。"""
        # 2024-03-16 周六
        assert is_trading_day(date(2024, 3, 16)) is False

    def test_sunday_is_not_trading_day(self) -> None:
        """周日不是交易日。"""
        # 2024-03-17 周日
        assert is_trading_day(date(2024, 3, 17)) is False

    def test_new_year_2024_is_not_trading_day(self) -> None:
        """2024 元旦不是交易日。"""
        assert is_trading_day(date(2024, 1, 1)) is False

    def test_spring_festival_2024(self) -> None:
        """2024 春节假期不是交易日。"""
        # 2024 春节：2月10日-2月17日
        for day in range(10, 18):
            assert is_trading_day(date(2024, 2, day)) is False

    def test_national_day_2024(self) -> None:
        """2024 国庆假期不是交易日。"""
        for day in range(1, 8):
            assert is_trading_day(date(2024, 10, day)) is False

    def test_qingming_2024(self) -> None:
        """2024 清明假期不是交易日。"""
        assert is_trading_day(date(2024, 4, 4)) is False
        assert is_trading_day(date(2024, 4, 5)) is False

    def test_labor_day_2024(self) -> None:
        """2024 劳动节假期不是交易日。"""
        for day in range(1, 6):
            assert is_trading_day(date(2024, 5, day)) is False

    def test_dragon_boat_2024(self) -> None:
        """2024 端午假期不是交易日。"""
        assert is_trading_day(date(2024, 6, 8)) is False
        assert is_trading_day(date(2024, 6, 10)) is False

    def test_mid_autumn_2024(self) -> None:
        """2024 中秋假期不是交易日。"""
        assert is_trading_day(date(2024, 9, 15)) is False
        assert is_trading_day(date(2024, 9, 16)) is False
        assert is_trading_day(date(2024, 9, 17)) is False

    def test_day_after_spring_festival_2024_is_trading(self) -> None:
        """2024 春节后第一个工作日是交易日。"""
        # 2024-02-19 周一，春节后首个交易日
        assert is_trading_day(date(2024, 2, 19)) is True

    def test_spring_festival_2020_extended(self) -> None:
        """2020 春节因疫情延长休市。"""
        # 2020 春节延长到 1月31日
        assert is_trading_day(date(2020, 1, 31)) is False
        # 2020-02-03 周一恢复交易
        assert is_trading_day(date(2020, 2, 3)) is True


# ---------------------------------------------------------------------------
# trading_days 测试
# ---------------------------------------------------------------------------


class TestTradingDays:
    """trading_days 函数测试。"""

    def test_single_trading_day(self) -> None:
        """单日范围，该日为交易日。"""
        result = trading_days(date(2024, 3, 18), date(2024, 3, 18))
        assert result == [date(2024, 3, 18)]

    def test_single_non_trading_day(self) -> None:
        """单日范围，该日为非交易日。"""
        result = trading_days(date(2024, 1, 1), date(2024, 1, 1))
        assert result == []

    def test_one_week(self) -> None:
        """一周内应有 5 个交易日（无节假日的普通周）。"""
        # 2024-03-18 (周一) 到 2024-03-22 (周五)
        result = trading_days(date(2024, 3, 18), date(2024, 3, 22))
        assert len(result) == 5
        assert result[0] == date(2024, 3, 18)
        assert result[-1] == date(2024, 3, 22)

    def test_week_with_weekend(self) -> None:
        """包含周末的范围应排除周末。"""
        # 2024-03-18 (周一) 到 2024-03-24 (周日)
        result = trading_days(date(2024, 3, 18), date(2024, 3, 24))
        assert len(result) == 5
        # 不包含 3/23 (周六) 和 3/24 (周日)
        assert date(2024, 3, 23) not in result
        assert date(2024, 3, 24) not in result

    def test_start_after_end_raises(self) -> None:
        """start > end 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="must not be after"):
            trading_days(date(2024, 3, 20), date(2024, 3, 18))

    def test_empty_range_holiday_period(self) -> None:
        """纯假期范围应返回空列表。"""
        # 2024 国庆 10/1 - 10/7 全部是假期，10/5-6 是周末
        result = trading_days(date(2024, 10, 1), date(2024, 10, 7))
        assert result == []

    def test_results_are_sorted(self) -> None:
        """返回结果应按日期升序排列。"""
        result = trading_days(date(2024, 1, 1), date(2024, 12, 31))
        assert result == sorted(result)

    def test_no_weekends_in_result(self) -> None:
        """结果中不应包含任何周末。"""
        result = trading_days(date(2024, 1, 1), date(2024, 12, 31))
        for d in result:
            assert d.weekday() < 5, f"{d} is a weekend day"

    def test_january_2024(self) -> None:
        """2024年1月交易日验证。"""
        result = trading_days(date(2024, 1, 1), date(2024, 1, 31))
        # 1月1日元旦休市，其余工作日正常
        assert date(2024, 1, 1) not in result
        assert date(2024, 1, 2) in result


# ---------------------------------------------------------------------------
# next_trading_day 测试
# ---------------------------------------------------------------------------


class TestNextTradingDay:
    """next_trading_day 函数测试。"""

    def test_from_trading_day(self) -> None:
        """从交易日出发，返回下一个交易日。"""
        # 2024-03-18 周一 → 2024-03-19 周二
        assert next_trading_day(date(2024, 3, 18)) == date(2024, 3, 19)

    def test_from_friday(self) -> None:
        """从周五出发，跳过周末返回周一。"""
        # 2024-03-22 周五 → 2024-03-25 周一
        assert next_trading_day(date(2024, 3, 22)) == date(2024, 3, 25)

    def test_from_saturday(self) -> None:
        """从周六出发，返回下周一。"""
        # 2024-03-23 周六 → 2024-03-25 周一
        assert next_trading_day(date(2024, 3, 23)) == date(2024, 3, 25)

    def test_from_sunday(self) -> None:
        """从周日出发，返回下周一。"""
        # 2024-03-24 周日 → 2024-03-25 周一
        assert next_trading_day(date(2024, 3, 24)) == date(2024, 3, 25)

    def test_before_spring_festival_2024(self) -> None:
        """春节前最后一个交易日的下一交易日应跳过整个假期。"""
        # 2024-02-08 周四（春节前最后交易日）→ 2024-02-19 周一
        assert next_trading_day(date(2024, 2, 8)) == date(2024, 2, 19)

    def test_before_national_day_2024(self) -> None:
        """国庆前最后一个交易日的下一交易日应跳过整个假期。"""
        # 2024-09-30 周一 → 2024-10-08 周二
        assert next_trading_day(date(2024, 9, 30)) == date(2024, 10, 8)

    def test_from_new_year_eve_2023(self) -> None:
        """2023年12月31日（周日）的下一交易日应跳过元旦。"""
        # 2023-12-31 周日 → 2024-01-02 周二（1月1日元旦休市）
        assert next_trading_day(date(2023, 12, 31)) == date(2024, 1, 2)


# ---------------------------------------------------------------------------
# prev_trading_day 测试
# ---------------------------------------------------------------------------


class TestPrevTradingDay:
    """prev_trading_day 函数测试。"""

    def test_from_trading_day(self) -> None:
        """从交易日出发，返回前一个交易日。"""
        # 2024-03-19 周二 → 2024-03-18 周一
        assert prev_trading_day(date(2024, 3, 19)) == date(2024, 3, 18)

    def test_from_monday(self) -> None:
        """从周一出发，跳过周末返回上周五。"""
        # 2024-03-25 周一 → 2024-03-22 周五
        assert prev_trading_day(date(2024, 3, 25)) == date(2024, 3, 22)

    def test_from_saturday(self) -> None:
        """从周六出发，返回周五。"""
        # 2024-03-23 周六 → 2024-03-22 周五
        assert prev_trading_day(date(2024, 3, 23)) == date(2024, 3, 22)

    def test_after_spring_festival_2024(self) -> None:
        """春节后第一个交易日的前一交易日应跳过整个假期。"""
        # 2024-02-19 周一 → 2024-02-08 周四
        assert prev_trading_day(date(2024, 2, 19)) == date(2024, 2, 8)

    def test_after_national_day_2024(self) -> None:
        """国庆后第一个交易日的前一交易日应跳过整个假期。"""
        # 2024-10-08 周二 → 2024-09-30 周一
        assert prev_trading_day(date(2024, 10, 8)) == date(2024, 9, 30)


# ---------------------------------------------------------------------------
# 10 年日历覆盖测试（2015-2025）
# ---------------------------------------------------------------------------


class TestTenYearCoverage:
    """验证 2015-2025 年每年交易日数量在合理范围内。

    中国 A 股每年交易日通常在 240-250 天之间。
    """

    @pytest.mark.parametrize("year", range(2015, 2026))
    def test_yearly_trading_days_count(self, year: int) -> None:
        """每年交易日数量应在 [240, 255] 范围内。"""
        days = trading_days(date(year, 1, 1), date(year, 12, 31))
        count = len(days)
        assert 240 <= count <= 255, (
            f"{year} 年交易日数量 {count} 不在合理范围 [240, 255] 内"
        )

    @pytest.mark.parametrize("year", range(2015, 2026))
    def test_no_weekends_in_yearly_calendar(self, year: int) -> None:
        """每年的交易日中不应包含周末。"""
        days = trading_days(date(year, 1, 1), date(year, 12, 31))
        for d in days:
            assert d.weekday() < 5, f"{year} 年 {d} 是周末但被标记为交易日"

    @pytest.mark.parametrize("year", range(2015, 2026))
    def test_spring_festival_excluded(self, year: int) -> None:
        """每年春节期间（至少 5 个连续非交易日）应被排除。"""
        days = trading_days(date(year, 1, 1), date(year, 12, 31))
        days_set = set(days)

        # 春节通常在 1-2 月，找到最长的连续非交易日段
        jan_feb = trading_days(date(year, 1, 15), date(year, 2, 28))
        if not jan_feb:
            return

        # 验证春节期间有连续非交易日
        all_dates = []
        current = date(year, 1, 15)
        end = date(year, 2, 28)
        while current <= end:
            all_dates.append(current)
            current += timedelta(days=1)

        max_gap = 0
        current_gap = 0
        for d in all_dates:
            if d not in days_set:
                current_gap += 1
                max_gap = max(max_gap, current_gap)
            else:
                current_gap = 0

        # 春节至少有 7 天连续非交易日（含周末）
        assert max_gap >= 7, f"{year} 年春节期间连续非交易日不足 7 天"

    @pytest.mark.parametrize("year", range(2015, 2026))
    def test_national_day_excluded(self, year: int) -> None:
        """每年国庆期间应有至少 7 天连续非交易日。"""
        days = trading_days(date(year, 9, 25), date(year, 10, 10))
        days_set = set(days)

        all_dates = []
        current = date(year, 9, 25)
        end = date(year, 10, 10)
        while current <= end:
            all_dates.append(current)
            current += timedelta(days=1)

        max_gap = 0
        current_gap = 0
        for d in all_dates:
            if d not in days_set:
                current_gap += 1
                max_gap = max(max_gap, current_gap)
            else:
                current_gap = 0

        assert max_gap >= 7, f"{year} 年国庆期间连续非交易日不足 7 天"

    def test_consecutive_trading_days_are_monotonic(self) -> None:
        """10 年交易日列表应严格单调递增。"""
        days = trading_days(date(2015, 1, 1), date(2025, 12, 31))
        for i in range(1, len(days)):
            assert days[i] > days[i - 1]

    def test_total_trading_days_10_years(self) -> None:
        """10 年总交易日数量应在合理范围内（约 2400-2750）。"""
        days = trading_days(date(2015, 1, 1), date(2025, 12, 31))
        total = len(days)
        assert 2400 <= total <= 2750, f"10 年总交易日 {total} 不在合理范围内"


# ---------------------------------------------------------------------------
# 边界与特殊场景测试
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """边界条件与特殊场景测试。"""

    def test_same_start_end_trading_day(self) -> None:
        """start == end 且为交易日时返回单元素列表。"""
        result = trading_days(date(2024, 3, 18), date(2024, 3, 18))
        assert result == [date(2024, 3, 18)]

    def test_same_start_end_non_trading_day(self) -> None:
        """start == end 且为非交易日时返回空列表。"""
        result = trading_days(date(2024, 3, 16), date(2024, 3, 16))
        assert result == []

    def test_next_trading_day_consistency(self) -> None:
        """next_trading_day 的结果应该是交易日。"""
        test_dates = [
            date(2024, 1, 1),
            date(2024, 2, 9),
            date(2024, 5, 1),
            date(2024, 10, 1),
            date(2024, 12, 31),
        ]
        for d in test_dates:
            nxt = next_trading_day(d)
            assert is_trading_day(nxt), f"next_trading_day({d}) = {nxt} 不是交易日"
            assert nxt > d, f"next_trading_day({d}) = {nxt} 不在 d 之后"

    def test_prev_trading_day_consistency(self) -> None:
        """prev_trading_day 的结果应该是交易日。"""
        test_dates = [
            date(2024, 1, 2),
            date(2024, 2, 19),
            date(2024, 5, 6),
            date(2024, 10, 8),
        ]
        for d in test_dates:
            prev = prev_trading_day(d)
            assert is_trading_day(prev), f"prev_trading_day({d}) = {prev} 不是交易日"
            assert prev < d, f"prev_trading_day({d}) = {prev} 不在 d 之前"

    def test_next_then_prev_returns_original_if_trading_day(self) -> None:
        """对交易日执行 next 再 prev 应回到原日期。"""
        d = date(2024, 3, 18)  # 周一，交易日
        assert is_trading_day(d)
        nxt = next_trading_day(d)
        back = prev_trading_day(nxt)
        assert back == d

    def test_trading_days_across_year_boundary(self) -> None:
        """跨年查询应正常工作。"""
        result = trading_days(date(2023, 12, 28), date(2024, 1, 5))
        # 2023-12-28 周四, 12-29 周五 是交易日
        # 12-30 周六, 12-31 周日 非交易日
        # 2024-01-01 元旦非交易日
        # 2024-01-02 周二, 01-03 周三, 01-04 周四, 01-05 周五 是交易日
        assert date(2023, 12, 28) in result
        assert date(2023, 12, 29) in result
        assert date(2024, 1, 1) not in result
        assert date(2024, 1, 2) in result

    def test_2020_covid_extended_holiday(self) -> None:
        """2020 年春节因疫情延长，1月31日仍为休市日。"""
        # 正常春节假期到 1/30，但 2020 延长到 1/31
        assert is_trading_day(date(2020, 1, 31)) is False
        # 2月3日恢复交易
        assert is_trading_day(date(2020, 2, 3)) is True
