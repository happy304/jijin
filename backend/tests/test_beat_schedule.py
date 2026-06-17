"""Tests for :mod:`app.tasks.schedule`.

Verifies that :data:`BEAT_SCHEDULE` is correctly configured with the
expected crontab expressions for all scheduled tasks:

- Daily 21:00: Data ingestion (NAV, metadata, dividends, announcements)
- Daily 22:00: Strategy signal generation
- Weekly Sunday 02:00: Database backup
- Quarterly 15th-25th of Jan/Apr/Jul/Oct at 04:00: Holdings update

Each crontab expression is validated by checking its internal fields
(hour, minute, day_of_week, day_of_month, month_of_year) to ensure
the schedule fires at the correct times.

Requirements: 8.1, 8.2
"""

from __future__ import annotations

from celery.schedules import crontab

from app.core.config import Settings, get_settings
from app.tasks import schedule as schedule_module
from app.tasks.celery_app import celery_app, create_celery_app
from app.tasks.schedule import (
    BEAT_SCHEDULE,
    LIGHT_SCHEDULE_ENTRIES,
    RESEARCH_SCHEDULE_ENTRIES,
    get_beat_schedule,
)


# =====================================================================
# Basic structure tests
# =====================================================================


class TestBeatScheduleStructure:
    """Verify the schedule dict structure and completeness."""

    def test_beat_schedule_is_a_dict(self) -> None:
        assert isinstance(BEAT_SCHEDULE, dict)

    def test_beat_schedule_has_all_expected_entries(self) -> None:
        """All 7 scheduled entries must be present."""
        expected_entries = {
            "daily-fund-discovery",
            "weekly-ranking-cleanup",
            "daily-benchmark-nav",
            "daily-nav-ingest",
            "daily-fund-meta",
            "daily-dividends",
            "daily-announcements",
            "daily-strategy-signals",
            "daily-cross-sectional-scoring",
            "daily-trading-advice",
            "daily-oos-validation-refresh",
            "daily-advice-tracking",
            "daily-advisor-reminders-refresh",
            "weekly-engine-validation",
            "weekly-feedback-learning",
            "daily-catchup-check",
            "daily-valuation-ingest",
            "quarterly-holdings",
            "monthly-cs-ic-validation",
            "weekly-database-backup",
        }
        assert set(BEAT_SCHEDULE.keys()) == expected_entries

    def test_beat_schedule_has_ingest_entries(self) -> None:
        """Phase 1.11 populates the schedule with data ingestion tasks."""
        assert len(BEAT_SCHEDULE) >= 5
        assert "daily-nav-ingest" in BEAT_SCHEDULE
        assert "daily-fund-meta" in BEAT_SCHEDULE
        assert "daily-dividends" in BEAT_SCHEDULE
        assert "quarterly-holdings" in BEAT_SCHEDULE
        assert "daily-announcements" in BEAT_SCHEDULE

    def test_light_schedule_entries_are_registered(self) -> None:
        """Personal light mode only references known schedule entries."""
        assert LIGHT_SCHEDULE_ENTRIES <= set(BEAT_SCHEDULE)

    def test_research_schedule_entries_are_registered(self) -> None:
        """Research mode only references known schedule entries."""
        assert RESEARCH_SCHEDULE_ENTRIES <= set(BEAT_SCHEDULE)

    def test_research_schedule_extends_light_schedule(self) -> None:
        """Research mode keeps the personal light workflow and adds research jobs."""
        assert LIGHT_SCHEDULE_ENTRIES < RESEARCH_SCHEDULE_ENTRIES

    def test_all_entries_have_required_keys(self) -> None:
        """Each entry must have task, schedule, and options."""
        for name, entry in BEAT_SCHEDULE.items():
            assert "task" in entry, f"{name} missing 'task'"
            assert "schedule" in entry, f"{name} missing 'schedule'"
            assert "options" in entry, f"{name} missing 'options'"
            assert isinstance(entry["schedule"], crontab), (
                f"{name} schedule is not a crontab instance"
            )

    def test_all_entries_have_valid_queue(self) -> None:
        """Each entry must route to a known queue."""
        valid_queues = {"ingest", "backtest", "ai", "notify"}
        for name, entry in BEAT_SCHEDULE.items():
            queue = entry["options"].get("queue")
            assert queue in valid_queues, (
                f"{name} routes to unknown queue '{queue}'"
            )


# =====================================================================
# Schedule mode selection tests
# =====================================================================


class TestScheduleModeSelection:
    """Verify personal/research/full schedule tiers."""

    def test_light_mode_keeps_only_personal_default_entries(self) -> None:
        schedule = get_beat_schedule("light")
        assert set(schedule) == LIGHT_SCHEDULE_ENTRIES
        assert "daily-nav-ingest" in schedule
        assert "weekly-database-backup" in schedule
        assert "daily-trading-advice" not in schedule
        assert "daily-oos-validation-refresh" not in schedule

    def test_research_mode_adds_research_jobs_without_full_governance(self) -> None:
        schedule = get_beat_schedule("research")
        assert set(schedule) == RESEARCH_SCHEDULE_ENTRIES
        assert "daily-fund-discovery" in schedule
        assert "daily-cross-sectional-scoring" in schedule
        assert "daily-strategy-signals" in schedule
        assert "daily-trading-advice" not in schedule
        assert "weekly-feedback-learning" not in schedule

    def test_full_mode_keeps_all_registered_entries(self) -> None:
        assert get_beat_schedule("full") == BEAT_SCHEDULE


# =====================================================================
# Celery app integration tests
# =====================================================================


class TestCeleryAppIntegration:
    """Verify the schedule is wired into the Celery app."""

    def test_celery_app_beat_schedule_uses_configured_mode(self) -> None:
        """The shared app follows ``SCHEDULE_MODE`` instead of always using full mode."""
        expected = get_beat_schedule(get_settings().schedule_mode)
        assert dict(celery_app.conf.beat_schedule) == expected

    def test_freshly_built_app_wires_full_schedule_when_configured(self) -> None:
        """Factory-built apps can still opt into the complete advanced schedule."""
        app = create_celery_app(Settings(_env_file=None, SCHEDULE_MODE="full"))  # type: ignore[call-arg]
        assert dict(app.conf.beat_schedule) == schedule_module.BEAT_SCHEDULE

    def test_freshly_built_app_wires_light_schedule_when_configured(self) -> None:
        """Personal light mode keeps only the core data freshness workflow."""
        app = create_celery_app(Settings(_env_file=None, SCHEDULE_MODE="light"))  # type: ignore[call-arg]
        assert set(app.conf.beat_schedule) == LIGHT_SCHEDULE_ENTRIES


# =====================================================================
# Crontab expression validation — Daily 21:00 data ingestion
# =====================================================================


class TestDailyNavIngestSchedule:
    """Verify daily-nav-ingest fires at 21:00 every day."""

    def test_task_name(self) -> None:
        assert BEAT_SCHEDULE["daily-nav-ingest"]["task"] == (
            "app.tasks.ingest.update_daily_nav"
        )

    def test_hour_is_21(self) -> None:
        sched = BEAT_SCHEDULE["daily-nav-ingest"]["schedule"]
        assert sched.hour == {21}

    def test_minute_is_0(self) -> None:
        sched = BEAT_SCHEDULE["daily-nav-ingest"]["schedule"]
        assert sched.minute == {0}

    def test_runs_every_day_of_week(self) -> None:
        """No day_of_week restriction — runs all 7 days."""
        sched = BEAT_SCHEDULE["daily-nav-ingest"]["schedule"]
        assert sched.day_of_week == {0, 1, 2, 3, 4, 5, 6}

    def test_runs_every_day_of_month(self) -> None:
        sched = BEAT_SCHEDULE["daily-nav-ingest"]["schedule"]
        assert sched.day_of_month == set(range(1, 32))

    def test_runs_every_month(self) -> None:
        sched = BEAT_SCHEDULE["daily-nav-ingest"]["schedule"]
        assert sched.month_of_year == set(range(1, 13))

    def test_queue_is_ingest(self) -> None:
        assert BEAT_SCHEDULE["daily-nav-ingest"]["options"]["queue"] == "ingest"


class TestDailyFundMetaSchedule:
    """Verify daily-fund-meta fires at 21:05 every day."""

    def test_task_name(self) -> None:
        assert BEAT_SCHEDULE["daily-fund-meta"]["task"] == (
            "app.tasks.ingest.update_fund_meta"
        )

    def test_hour_is_21(self) -> None:
        sched = BEAT_SCHEDULE["daily-fund-meta"]["schedule"]
        assert sched.hour == {21}

    def test_minute_is_5(self) -> None:
        sched = BEAT_SCHEDULE["daily-fund-meta"]["schedule"]
        assert sched.minute == {5}

    def test_queue_is_ingest(self) -> None:
        assert BEAT_SCHEDULE["daily-fund-meta"]["options"]["queue"] == "ingest"


class TestDailyDividendsSchedule:
    """Verify daily-dividends fires at 21:10 every day."""

    def test_task_name(self) -> None:
        assert BEAT_SCHEDULE["daily-dividends"]["task"] == (
            "app.tasks.ingest.update_dividends"
        )

    def test_hour_is_21(self) -> None:
        sched = BEAT_SCHEDULE["daily-dividends"]["schedule"]
        assert sched.hour == {21}

    def test_minute_is_10(self) -> None:
        sched = BEAT_SCHEDULE["daily-dividends"]["schedule"]
        assert sched.minute == {10}

    def test_queue_is_ingest(self) -> None:
        assert BEAT_SCHEDULE["daily-dividends"]["options"]["queue"] == "ingest"


class TestDailyAnnouncementsSchedule:
    """Verify daily-announcements fires at 21:30 every day."""

    def test_task_name(self) -> None:
        assert BEAT_SCHEDULE["daily-announcements"]["task"] == (
            "app.tasks.ingest.update_announcements"
        )

    def test_hour_is_21(self) -> None:
        sched = BEAT_SCHEDULE["daily-announcements"]["schedule"]
        assert sched.hour == {21}

    def test_minute_is_30(self) -> None:
        sched = BEAT_SCHEDULE["daily-announcements"]["schedule"]
        assert sched.minute == {30}

    def test_queue_is_ingest(self) -> None:
        assert BEAT_SCHEDULE["daily-announcements"]["options"]["queue"] == "ingest"


# =====================================================================
# Crontab expression validation — Daily 22:00 strategy signals
# =====================================================================


class TestDailyStrategySignalsSchedule:
    """Verify daily-strategy-signals fires at 22:00 every day.

    Requirement 8.2: after data update completes, trigger signal generation.
    The 22:00 time gives a 30-minute buffer after the last ingestion task
    (announcements at 21:30).
    """

    def test_task_name(self) -> None:
        assert BEAT_SCHEDULE["daily-strategy-signals"]["task"] == (
            "app.tasks.signals.generate_strategy_signals"
        )

    def test_hour_is_22(self) -> None:
        sched = BEAT_SCHEDULE["daily-strategy-signals"]["schedule"]
        assert sched.hour == {22}

    def test_minute_is_0(self) -> None:
        sched = BEAT_SCHEDULE["daily-strategy-signals"]["schedule"]
        assert sched.minute == {0}

    def test_runs_every_day_of_week(self) -> None:
        """Signal generation runs daily (market holidays handled by task logic)."""
        sched = BEAT_SCHEDULE["daily-strategy-signals"]["schedule"]
        assert sched.day_of_week == {0, 1, 2, 3, 4, 5, 6}

    def test_runs_every_day_of_month(self) -> None:
        sched = BEAT_SCHEDULE["daily-strategy-signals"]["schedule"]
        assert sched.day_of_month == set(range(1, 32))

    def test_runs_every_month(self) -> None:
        sched = BEAT_SCHEDULE["daily-strategy-signals"]["schedule"]
        assert sched.month_of_year == set(range(1, 13))

    def test_queue_is_backtest(self) -> None:
        """Signal generation uses the backtest queue (CPU-intensive)."""
        assert BEAT_SCHEDULE["daily-strategy-signals"]["options"]["queue"] == "backtest"

    def test_signal_generation_after_data_ingestion(self) -> None:
        """Signal generation (22:00) must be scheduled after all ingestion tasks."""
        signal_sched = BEAT_SCHEDULE["daily-strategy-signals"]["schedule"]
        signal_hour = max(signal_sched.hour)

        # All ingestion tasks run at hour 21
        ingestion_entries = [
            "daily-nav-ingest",
            "daily-fund-meta",
            "daily-dividends",
            "daily-announcements",
        ]
        for entry_name in ingestion_entries:
            ingest_sched = BEAT_SCHEDULE[entry_name]["schedule"]
            ingest_hour = max(ingest_sched.hour)
            assert signal_hour > ingest_hour, (
                f"Signal generation ({signal_hour}:00) must be after "
                f"{entry_name} ({ingest_hour}:XX)"
            )


# =====================================================================
# Crontab expression validation — Weekly backup at 02:00 Sunday
# =====================================================================


class TestWeeklyDatabaseBackupSchedule:
    """Verify weekly-database-backup fires at 02:00 every Sunday.

    Requirement 2.10: system SHALL auto-execute backup every Sunday at 02:00.
    """

    def test_task_name(self) -> None:
        assert BEAT_SCHEDULE["weekly-database-backup"]["task"] == (
            "app.tasks.backup.run_database_backup"
        )

    def test_hour_is_2(self) -> None:
        sched = BEAT_SCHEDULE["weekly-database-backup"]["schedule"]
        assert sched.hour == {2}

    def test_minute_is_0(self) -> None:
        sched = BEAT_SCHEDULE["weekly-database-backup"]["schedule"]
        assert sched.minute == {0}

    def test_day_of_week_is_sunday(self) -> None:
        """Sunday is day 0 in Celery's crontab (0=Sunday, 6=Saturday)."""
        sched = BEAT_SCHEDULE["weekly-database-backup"]["schedule"]
        # Celery maps 'sunday' to 0
        assert sched.day_of_week == {0}

    def test_runs_every_day_of_month(self) -> None:
        """No day_of_month restriction — any Sunday qualifies."""
        sched = BEAT_SCHEDULE["weekly-database-backup"]["schedule"]
        assert sched.day_of_month == set(range(1, 32))

    def test_runs_every_month(self) -> None:
        sched = BEAT_SCHEDULE["weekly-database-backup"]["schedule"]
        assert sched.month_of_year == set(range(1, 13))

    def test_queue_is_ingest(self) -> None:
        assert BEAT_SCHEDULE["weekly-database-backup"]["options"]["queue"] == "ingest"


# =====================================================================
# Crontab expression validation — Quarterly holdings update
# =====================================================================


class TestQuarterlyHoldingsSchedule:
    """Verify quarterly-holdings fires at 04:00 on days 15-25 of Jan/Apr/Jul/Oct.

    Quarterly reports are typically published 15-25 days into the quarter
    following the reporting period. This schedule covers that window.
    """

    def test_task_name(self) -> None:
        assert BEAT_SCHEDULE["quarterly-holdings"]["task"] == (
            "app.tasks.ingest.update_holdings"
        )

    def test_hour_is_4(self) -> None:
        sched = BEAT_SCHEDULE["quarterly-holdings"]["schedule"]
        assert sched.hour == {4}

    def test_minute_is_0(self) -> None:
        sched = BEAT_SCHEDULE["quarterly-holdings"]["schedule"]
        assert sched.minute == {0}

    def test_day_of_month_is_15_to_25(self) -> None:
        """Holdings update runs on days 15 through 25 of the month."""
        sched = BEAT_SCHEDULE["quarterly-holdings"]["schedule"]
        expected_days = set(range(15, 26))  # 15, 16, ..., 25
        assert sched.day_of_month == expected_days

    def test_month_of_year_is_quarterly(self) -> None:
        """Only runs in January, April, July, October (quarter start months)."""
        sched = BEAT_SCHEDULE["quarterly-holdings"]["schedule"]
        assert sched.month_of_year == {1, 4, 7, 10}

    def test_runs_every_day_of_week(self) -> None:
        """No day_of_week restriction — any qualifying date runs."""
        sched = BEAT_SCHEDULE["quarterly-holdings"]["schedule"]
        assert sched.day_of_week == {0, 1, 2, 3, 4, 5, 6}

    def test_queue_is_ingest(self) -> None:
        assert BEAT_SCHEDULE["quarterly-holdings"]["options"]["queue"] == "ingest"


# =====================================================================
# Cross-cutting schedule validation
# =====================================================================


class TestScheduleOrdering:
    """Verify logical ordering constraints between scheduled tasks."""

    def test_data_ingestion_before_signal_generation(self) -> None:
        """All data ingestion tasks must complete before signal generation starts.

        Ingestion window: 21:00 - 21:30
        Signal generation: 22:00
        Buffer: 30 minutes for ingestion to complete
        """
        signal_hour = max(BEAT_SCHEDULE["daily-strategy-signals"]["schedule"].hour)
        signal_minute = max(BEAT_SCHEDULE["daily-strategy-signals"]["schedule"].minute)

        # Latest ingestion task is announcements at 21:30
        last_ingest_hour = max(
            BEAT_SCHEDULE["daily-announcements"]["schedule"].hour
        )
        last_ingest_minute = max(
            BEAT_SCHEDULE["daily-announcements"]["schedule"].minute
        )

        # Signal generation must start after the last ingestion task
        signal_time = signal_hour * 60 + signal_minute
        ingest_time = last_ingest_hour * 60 + last_ingest_minute
        assert signal_time > ingest_time

    def test_backup_runs_at_low_traffic_time(self) -> None:
        """Backup at 02:00 is well outside the ingestion/signal window."""
        backup_hour = max(BEAT_SCHEDULE["weekly-database-backup"]["schedule"].hour)
        assert backup_hour == 2  # Early morning, minimal system load

    def test_holdings_update_at_low_traffic_time(self) -> None:
        """Holdings update at 04:00 is well outside the daily task window."""
        holdings_hour = max(BEAT_SCHEDULE["quarterly-holdings"]["schedule"].hour)
        assert holdings_hour == 4  # Early morning
