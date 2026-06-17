"""Tests for :mod:`app.tasks.backup`.

Verifies:
- Backup filename generation includes date
- Database URL parsing extracts correct connection params
- Old backup cleanup retains only the configured number of files
- Beat schedule includes the weekly backup entry on Sunday 02:00
- The Celery task is registered and callable

Requirements: 2.10
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.tasks.backup import (
    RETENTION_WEEKS,
    _cleanup_old_backups,
    _generate_backup_filename,
    _get_backup_dir,
    _parse_db_url,
)
from app.tasks.schedule import BEAT_SCHEDULE


class TestParseDbUrl:
    """Tests for _parse_db_url helper."""

    def test_asyncpg_url(self) -> None:
        url = "postgresql+asyncpg://myuser:mypass@dbhost:5433/mydb"
        result = _parse_db_url(url)
        assert result["host"] == "dbhost"
        assert result["port"] == "5433"
        assert result["user"] == "myuser"
        assert result["password"] == "mypass"
        assert result["dbname"] == "mydb"

    def test_psycopg_url(self) -> None:
        url = "postgresql+psycopg://fundquant:fundquant@localhost:5432/fundquant"
        result = _parse_db_url(url)
        assert result["host"] == "localhost"
        assert result["port"] == "5432"
        assert result["user"] == "fundquant"
        assert result["password"] == "fundquant"
        assert result["dbname"] == "fundquant"

    def test_plain_postgresql_url(self) -> None:
        url = "postgresql://admin:secret@pg.example.com:5432/production"
        result = _parse_db_url(url)
        assert result["host"] == "pg.example.com"
        assert result["port"] == "5432"
        assert result["user"] == "admin"
        assert result["password"] == "secret"
        assert result["dbname"] == "production"

    def test_default_port(self) -> None:
        url = "postgresql://user:pass@host/db"
        result = _parse_db_url(url)
        assert result["port"] == "5432"


class TestGenerateBackupFilename:
    """Tests for _generate_backup_filename helper."""

    def test_filename_contains_date(self) -> None:
        filename = _generate_backup_filename()
        assert filename.startswith("fundquant_backup_")
        assert filename.endswith(".sql.gz")
        # Should contain a date-like pattern YYYYMMDD
        today = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
        assert today in filename

    def test_filename_format(self) -> None:
        filename = _generate_backup_filename()
        # Pattern: fundquant_backup_YYYYMMDD_HHMMSS.sql.gz
        parts = filename.replace("fundquant_backup_", "").replace(".sql.gz", "")
        date_part, time_part = parts.split("_")
        assert len(date_part) == 8  # YYYYMMDD
        assert len(time_part) == 6  # HHMMSS


class TestCleanupOldBackups:
    """Tests for _cleanup_old_backups helper."""

    def test_removes_oldest_files_beyond_retention(self, tmp_path: Path) -> None:
        """Create 10 backup files, keep=8 should remove 2 oldest."""
        files = []
        for i in range(10):
            f = tmp_path / f"fundquant_backup_2024010{i}_020000.dump"
            f.write_text("fake backup data")
            files.append(f)

        removed = _cleanup_old_backups(tmp_path, keep=8)
        assert removed == 2

        remaining = list(tmp_path.iterdir())
        assert len(remaining) == 8

        # The two oldest (00, 01) should be gone
        assert not (tmp_path / "fundquant_backup_20240100_020000.dump").exists()
        assert not (tmp_path / "fundquant_backup_20240101_020000.dump").exists()
        # The newest should remain
        assert (tmp_path / "fundquant_backup_20240109_020000.dump").exists()

    def test_no_removal_when_under_limit(self, tmp_path: Path) -> None:
        """If fewer files than retention limit, nothing is removed."""
        for i in range(3):
            f = tmp_path / f"fundquant_backup_2024010{i}_020000.dump"
            f.write_text("fake backup data")

        removed = _cleanup_old_backups(tmp_path, keep=8)
        assert removed == 0
        assert len(list(tmp_path.iterdir())) == 3

    def test_ignores_non_backup_files(self, tmp_path: Path) -> None:
        """Files not matching the backup prefix are left alone."""
        # Create some backup files
        for i in range(10):
            f = tmp_path / f"fundquant_backup_2024010{i}_020000.dump"
            f.write_text("fake backup data")

        # Create a non-backup file
        other = tmp_path / "readme.txt"
        other.write_text("not a backup")

        removed = _cleanup_old_backups(tmp_path, keep=8)
        assert removed == 2
        # Non-backup file should still exist
        assert other.exists()

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Empty directory should not raise."""
        removed = _cleanup_old_backups(tmp_path, keep=8)
        assert removed == 0

    def test_exact_retention_count(self, tmp_path: Path) -> None:
        """Exactly 8 files should result in 0 removals."""
        for i in range(8):
            f = tmp_path / f"fundquant_backup_2024010{i}_020000.dump"
            f.write_text("fake backup data")

        removed = _cleanup_old_backups(tmp_path, keep=8)
        assert removed == 0


class TestBeatScheduleBackup:
    """Verify the backup task is correctly scheduled."""

    def test_weekly_backup_entry_exists(self) -> None:
        assert "weekly-database-backup" in BEAT_SCHEDULE

    def test_weekly_backup_task_name(self) -> None:
        entry = BEAT_SCHEDULE["weekly-database-backup"]
        assert entry["task"] == "app.tasks.backup.run_database_backup"

    def test_weekly_backup_runs_sunday_2am(self) -> None:
        entry = BEAT_SCHEDULE["weekly-database-backup"]
        schedule = entry["schedule"]
        # Celery crontab stores day_of_week as a set of ints (0=Sunday in some configs)
        # Check the crontab attributes
        assert schedule.hour == {2}
        assert schedule.minute == {0}
        # Sunday is represented as 0 in celery's crontab
        assert 0 in schedule.day_of_week

    def test_weekly_backup_queue(self) -> None:
        entry = BEAT_SCHEDULE["weekly-database-backup"]
        assert entry["options"]["queue"] == "ingest"


class TestRetentionConfig:
    """Verify retention configuration."""

    def test_retention_weeks_is_8(self) -> None:
        assert RETENTION_WEEKS == 8


class TestBackupTaskRegistration:
    """Verify the task is registered in Celery."""

    def test_task_is_registered(self) -> None:
        from app.tasks.celery_app import celery_app

        assert "app.tasks.backup.run_database_backup" in celery_app.tasks


class TestGetBackupDir:
    """Tests for _get_backup_dir helper."""

    @patch("app.tasks.backup.get_settings")
    def test_creates_directory_if_not_exists(
        self, mock_settings: MagicMock, tmp_path: Path
    ) -> None:
        backup_path = tmp_path / "new_backup_dir"
        mock_settings.return_value = MagicMock(backup_dir=str(backup_path))

        result = _get_backup_dir()
        assert result.exists()
        assert result.is_dir()
