"""Celery Beat schedule definitions.

Configures periodic task execution for the Fund Quant Platform:

- Daily 21:00 (Asia/Shanghai): Data update (NAV, metadata, dividends)
- Daily 22:00 (Asia/Shanghai): Strategy signal generation
- Weekly Sunday 02:00 (Asia/Shanghai): Database backup
- Quarterly 15th-25th of Jan/Apr/Jul/Oct at 04:00: Holdings update

All times are in Asia/Shanghai (UTC+8) timezone, configured via
``celery_timezone`` in the Celery app settings.

Schedule entries follow Celery's standard shape::

    BEAT_SCHEDULE["entry-name"] = {
        "task": "app.tasks.module.task_name",
        "schedule": crontab(...),
        "options": {"queue": "queue_name"},
    }

Requirements: 1.11, 2.10, 8.1, 8.2
"""

from __future__ import annotations

from typing import Any

from celery.schedules import crontab

from app.core.config import ScheduleMode

# Schedule tiers for personal-use deployments.  ``full`` keeps every
# registered periodic task; lighter modes reduce background noise and resource
# usage while preserving the core data freshness workflow.
LIGHT_SCHEDULE_ENTRIES: frozenset[str] = frozenset(
    {
        "daily-benchmark-nav",
        "daily-nav-ingest",
        "daily-fund-meta",
        "daily-dividends",
        "weekly-database-backup",
    }
)

RESEARCH_SCHEDULE_ENTRIES: frozenset[str] = LIGHT_SCHEDULE_ENTRIES | frozenset(
    {
        "daily-fund-discovery",
        "daily-cross-sectional-scoring",
        "daily-strategy-signals",
        "monthly-cs-ic-validation",
        "quarterly-holdings",
    }
)

# Mapping of ``entry_name -> celery beat entry definition``.
BEAT_SCHEDULE: dict[str, dict[str, Any]] = {
    # ==================================================================
    # Fund Auto-Discovery — Daily 20:30 (before ingestion window)
    # ==================================================================

    # ------------------------------------------------------------------
    # Daily fund discovery — every day at 20:30 (Asia/Shanghai)
    # Fetches ranking data and registers new funds before the 21:00
    # ingestion window so newly discovered funds are included.
    # ------------------------------------------------------------------
    "daily-fund-discovery": {
        "task": "app.tasks.discovery.discover_funds",
        "schedule": crontab(hour=20, minute=30),
        "options": {"queue": "ingest"},
    },
    # ------------------------------------------------------------------
    # Weekly ranking cleanup — every Monday at 03:00
    # Removes ranking snapshots older than 30 days
    # ------------------------------------------------------------------
    "weekly-ranking-cleanup": {
        "task": "app.tasks.discovery.cleanup_stale_rankings",
        "schedule": crontab(hour=3, minute=0, day_of_week="monday"),
        "options": {"queue": "ingest"},
    },

    # ==================================================================
    # Data Ingestion — Daily 21:00 window (Requirement 8.1)
    # ==================================================================

    # ------------------------------------------------------------------
    # Daily benchmark index update — every day at 20:50
    # Fetches benchmark index data (沪深300/中证500/上证50) for
    # backtest comparison. Runs before NAV ingestion.
    # ------------------------------------------------------------------
    "daily-benchmark-nav": {
        "task": "app.tasks.ingest_benchmark.update_benchmark_nav",
        "schedule": crontab(hour=20, minute=50),
        "options": {"queue": "ingest"},
    },

    # ------------------------------------------------------------------
    # Daily NAV update — every day at 21:00 (Asia/Shanghai)
    # Requirement 8.1: system SHALL auto-execute NAV update at 21:00
    # ------------------------------------------------------------------
    "daily-nav-ingest": {
        "task": "app.tasks.ingest.update_daily_nav",
        "schedule": crontab(hour=21, minute=0),
        "options": {"queue": "ingest"},
    },
    # ------------------------------------------------------------------
    # Daily fund metadata refresh — every day at 21:05
    # Keeps fund status, purchase limits, etc. up to date
    # ------------------------------------------------------------------
    "daily-fund-meta": {
        "task": "app.tasks.ingest.update_fund_meta",
        "schedule": crontab(hour=21, minute=5),
        "options": {"queue": "ingest"},
    },
    # ------------------------------------------------------------------
    # Daily dividends update — every day at 21:10
    # Catches new dividend/split announcements promptly
    # ------------------------------------------------------------------
    "daily-dividends": {
        "task": "app.tasks.ingest.update_dividends",
        "schedule": crontab(hour=21, minute=10),
        "options": {"queue": "ingest"},
    },
    # ------------------------------------------------------------------
    # Daily announcements — every day at 21:30
    # ------------------------------------------------------------------
    "daily-announcements": {
        "task": "app.tasks.ingest.update_announcements",
        "schedule": crontab(hour=21, minute=30),
        "options": {"queue": "ingest"},
    },

    # ==================================================================
    # Strategy Signal Generation — Daily 22:00 (Requirement 8.2)
    # Runs after data ingestion completes (~21:30), giving a 30-min buffer
    # ==================================================================

    # ------------------------------------------------------------------
    # Daily strategy signal generation — every day at 22:00
    # Requirement 8.2: after data update, trigger signal generation
    # ------------------------------------------------------------------
    "daily-strategy-signals": {
        "task": "app.tasks.signals.generate_strategy_signals",
        "schedule": crontab(hour=22, minute=0),
        "options": {"queue": "backtest"},
    },

    # ==================================================================
    # Trading Advisor — Daily 22:30 (after signal generation)
    # Comprehensive analysis: technical + valuation + signals + prediction
    # ==================================================================

    # ------------------------------------------------------------------
    # Daily trading advice generation — every day at 22:30
    # Runs after signal generation, combines all dimensions to produce
    # actionable buy/sell advice with risk budget position sizing
    # ------------------------------------------------------------------
    "daily-trading-advice": {
        "task": "app.tasks.advisor.generate_daily_advice",
        "schedule": crontab(hour=22, minute=30),
        "options": {"queue": "backtest"},
    },

    # ------------------------------------------------------------------
    # Daily cross-sectional scoring — every day at 22:15
    # Pre-computes cross-sectional factor rankings for all fund types
    # Must run BEFORE daily-trading-advice (22:30) so scores are available
    # ------------------------------------------------------------------
    "daily-cross-sectional-scoring": {
        "task": "app.tasks.advisor.compute_cross_sectional_scores",
        "schedule": crontab(hour=22, minute=15),
        "options": {"queue": "backtest"},
    },
    # ------------------------------------------------------------------
    # Daily OOS validation cache refresh — every day at 21:40
    # Dispatches walk-forward refresh tasks so advisor can reuse recent
    # out-of-sample snapshots as a second anti-overfitting layer
    # ------------------------------------------------------------------
    "daily-oos-validation-refresh": {
        "task": "app.tasks.advisor.refresh_oos_validation_cache",
        "schedule": crontab(hour=21, minute=40),
        "kwargs": {
            "risk_level": "moderate",
            "lookback_days": None,
            "n_folds": 5,
            "rebalance_freq": 5,
            "max_funds": 50,
            "max_age_days": 1,
            "dispatch_every_n": 10,
            "dispatch_countdown_step": 30,
        },
        "options": {"queue": "backtest"},
    },

    # ==================================================================
    # Advisor Tracking — Daily 23:00 + Weekly Sunday 03:00
    # Tracks actual performance of past advice and validates engine health
    # ==================================================================

    # ------------------------------------------------------------------
    # Daily advice performance tracking — every day at 23:00
    # Computes actual returns after past advice and backfills results
    # ------------------------------------------------------------------
    "daily-advice-tracking": {
        "task": "app.tasks.advisor_tracking.track_advice_performance",
        "schedule": crontab(hour=23, minute=0),
        "options": {"queue": "backtest"},
    },
    # ------------------------------------------------------------------
    # Daily advisor reminders refresh — every day at 23:10
    # Recomputes reminder inbox items from history detail, execution
    # records and execution-plan status after nightly tracking finishes.
    # ------------------------------------------------------------------
    "daily-advisor-reminders-refresh": {
        "task": "app.tasks.advisor_tracking.refresh_advisor_reminders",
        "schedule": crontab(hour=23, minute=10),
        "options": {"queue": "notify"},
    },
    # ------------------------------------------------------------------
    # Daily Advisor reminder digest — disabled for personal-use deployment.
    # Keep the task/API code for future multi-device notification use, but do
    # not register it in Celery Beat so no active subscription/push is sent.
    # ------------------------------------------------------------------
    # "daily-advisor-reminder-digest": {
    #     "task": "app.tasks.advisor_tracking.send_advisor_reminder_digest",
    #     "schedule": crontab(hour=8, minute=30),
    #     "kwargs": {"days": None, "min_severity": None, "limit": 50},
    #     "options": {"queue": "notify"},
    # },
    # ------------------------------------------------------------------
    # Weekly engine health validation — every Sunday at 03:00
    # Computes rolling IC, detects degradation, sends alerts
    # ------------------------------------------------------------------
    "weekly-engine-validation": {
        "task": "app.tasks.advisor_tracking.validate_engine_health",
        "schedule": crontab(hour=3, minute=0, day_of_week="sunday"),
        "options": {"queue": "backtest"},
    },
    # ------------------------------------------------------------------
    # Weekly feedback learning — every Sunday at 04:00
    # Learns optimal weights and thresholds from tracked performance
    # ------------------------------------------------------------------
    "weekly-feedback-learning": {
        "task": "app.tasks.advisor_tracking.run_feedback_learning",
        "schedule": crontab(hour=4, minute=0, day_of_week="sunday"),
        "options": {"queue": "backtest"},
    },

    # ==================================================================
    # Task Chain Guard — Daily 06:00 (catch-up missed tasks)
    # Checks if yesterday's critical tasks completed; re-runs if not
    # ==================================================================

    # ------------------------------------------------------------------
    # Daily catch-up check — every day at 06:00
    # If worker was down yesterday, this will re-trigger missed tasks
    # ------------------------------------------------------------------
    "daily-catchup-check": {
        "task": "app.tasks.chain_guard.catchup_missed_tasks",
        "schedule": crontab(hour=6, minute=0),
        "options": {"queue": "backtest"},
    },

    # ==================================================================
    # Index Valuation Ingest — Daily 18:00 (after market close)
    # Fetches PE/PB/dividend yield for major indices
    # ==================================================================
    "daily-valuation-ingest": {
        "task": "app.tasks.valuation_ingest.ingest_index_valuation",
        "schedule": crontab(hour=18, minute=0),
        "options": {"queue": "ingest"},
    },

    # ==================================================================
    # Holdings Update — Quarterly (Requirement 1.3)
    # ==================================================================

    # ------------------------------------------------------------------
    # Quarterly holdings update — 15th-25th of Jan/Apr/Jul/Oct at 04:00
    # After quarterly reports are published, fetch holdings data
    # ------------------------------------------------------------------
    "quarterly-holdings": {
        "task": "app.tasks.ingest.update_holdings",
        "schedule": crontab(
            hour=4,
            minute=0,
            day_of_month="15-25",
            month_of_year="1,4,7,10",
        ),
        "options": {"queue": "ingest"},
    },

    # ==================================================================
    # Cross-Sectional IC Validation — Monthly 1st at 04:00
    # Monitors factor effectiveness, alerts if IC decays
    # ==================================================================
    "monthly-cs-ic-validation": {
        "task": "app.tasks.advisor.validate_cross_sectional_ic",
        "schedule": crontab(hour=4, minute=0, day_of_month="1"),
        "options": {"queue": "backtest"},
    },

    # ==================================================================
    # Database Backup — Weekly Sunday 02:00 (Requirement 2.10)
    # ==================================================================

    # ------------------------------------------------------------------
    # Weekly database backup — every Sunday at 02:00 (Asia/Shanghai)
    # Retention: keep last 8 weekly backups, purge older ones
    # ------------------------------------------------------------------
    "weekly-database-backup": {
        "task": "app.tasks.backup.run_database_backup",
        "schedule": crontab(hour=2, minute=0, day_of_week="sunday"),
        "options": {"queue": "ingest"},
    },
}


def get_beat_schedule(mode: ScheduleMode = "full") -> dict[str, dict[str, Any]]:
    """Return the Celery Beat schedule for a deployment mode.

    ``light`` is the personal-use default: keep only data freshness and backup
    tasks. ``research`` adds factor/strategy validation tasks. ``full`` returns
    the complete schedule for advanced research or production-like runs.
    """
    if mode == "full":
        return dict(BEAT_SCHEDULE)
    if mode == "research":
        allowed = RESEARCH_SCHEDULE_ENTRIES
    else:
        allowed = LIGHT_SCHEDULE_ENTRIES
    return {name: entry for name, entry in BEAT_SCHEDULE.items() if name in allowed}


__all__ = [
    "BEAT_SCHEDULE",
    "LIGHT_SCHEDULE_ENTRIES",
    "RESEARCH_SCHEDULE_ENTRIES",
    "get_beat_schedule",
]
