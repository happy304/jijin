"""Database backup Celery task.

Performs a full PostgreSQL dump using ``pg_dump`` to the local cold
storage directory configured via ``BACKUP_DIR``. Backup files are named
with the execution date for easy identification and rotation.

Retention policy: keep the most recent 8 weekly backups; older files
are automatically removed after each successful backup run.

Requirements: 2.10
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

log = get_logger(__name__)

#: Number of weekly backups to retain. Older backups are purged.
RETENTION_WEEKS: int = 8


def _parse_db_url(url: str) -> dict[str, str]:
    """Extract host, port, user, password, dbname from a SQLAlchemy URL.

    Supports URLs like:
        postgresql+asyncpg://user:pass@host:port/dbname
        postgresql+psycopg://user:pass@host:port/dbname
        postgresql://user:pass@host:port/dbname
    """
    from urllib.parse import urlparse

    # Strip SQLAlchemy driver suffix for standard parsing
    # e.g. postgresql+asyncpg://... -> postgresql://...
    if "+" in url.split("://")[0]:
        url = url.split("+")[0] + "://" + url.split("://", 1)[1]

    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "fundquant",
        "password": parsed.password or "",
        "dbname": (parsed.path or "/fundquant").lstrip("/"),
    }


def _get_backup_dir() -> Path:
    """Resolve and ensure the backup directory exists."""
    settings = get_settings()
    backup_dir = Path(settings.backup_dir).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def _generate_backup_filename() -> str:
    """Generate a backup filename containing the current date.

    Format: fundquant_backup_YYYYMMDD_HHMMSS.sql.gz
    """
    now = datetime.now(tz=timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    return f"fundquant_backup_{timestamp}.sql.gz"


def _run_pg_dump(backup_path: Path) -> None:
    """Execute pg_dump and compress output with gzip.

    Uses the sync database URL from settings to extract connection
    parameters. The dump is piped through gzip for compression.

    Raises
    ------
    subprocess.CalledProcessError
        If pg_dump exits with a non-zero status.
    """
    settings = get_settings()
    db_params = _parse_db_url(settings.database_sync_url)

    # Build environment with PGPASSWORD to avoid interactive prompt
    env = os.environ.copy()
    env["PGPASSWORD"] = db_params["password"]

    # pg_dump command with custom format for efficient compression
    # Using --compress=6 with plain format piped to gzip
    cmd = [
        "pg_dump",
        "-h", db_params["host"],
        "-p", db_params["port"],
        "-U", db_params["user"],
        "-d", db_params["dbname"],
        "--no-owner",
        "--no-privileges",
        "-Fc",  # Custom format (includes compression)
    ]

    log.info(
        "backup.pg_dump.start",
        host=db_params["host"],
        port=db_params["port"],
        dbname=db_params["dbname"],
        output=str(backup_path),
    )

    # When using custom format (-Fc), pg_dump handles compression
    # internally, so we write directly to file
    # Change extension since custom format doesn't use .sql.gz
    backup_path = backup_path.with_suffix("")  # Remove .gz
    backup_path = backup_path.with_suffix(".dump")  # Use .dump for custom format

    with open(backup_path, "wb") as f:
        result = subprocess.run(
            cmd,
            stdout=f,
            stderr=subprocess.PIPE,
            env=env,
            timeout=3600,  # 1 hour timeout
            check=True,
        )

    file_size = backup_path.stat().st_size
    log.info(
        "backup.pg_dump.complete",
        output=str(backup_path),
        size_bytes=file_size,
        size_mb=round(file_size / (1024 * 1024), 2),
    )

    return backup_path


def _cleanup_old_backups(backup_dir: Path, keep: int = RETENTION_WEEKS) -> int:
    """Remove backups older than the retention limit.

    Keeps the most recent ``keep`` backup files (sorted by filename
    which embeds the timestamp). Returns the number of files removed.

    Parameters
    ----------
    backup_dir : Path
        Directory containing backup files.
    keep : int
        Number of most recent backups to retain.

    Returns
    -------
    int
        Number of backup files deleted.
    """
    # Match both .dump (custom format) and .sql.gz (legacy) patterns
    backup_files = sorted(
        [
            f
            for f in backup_dir.iterdir()
            if f.is_file() and f.name.startswith("fundquant_backup_")
        ],
        key=lambda f: f.name,
        reverse=True,  # Most recent first
    )

    files_to_remove = backup_files[keep:]
    removed_count = 0

    for old_file in files_to_remove:
        try:
            old_file.unlink()
            log.info("backup.cleanup.removed", file=str(old_file))
            removed_count += 1
        except OSError as e:
            log.warning(
                "backup.cleanup.failed",
                file=str(old_file),
                error=str(e),
            )

    if removed_count > 0:
        log.info(
            "backup.cleanup.summary",
            removed=removed_count,
            retained=len(backup_files) - removed_count,
        )

    return removed_count


@celery_app.task(
    name="app.tasks.backup.run_database_backup",
    queue="ingest",
    bind=True,
    max_retries=2,
    default_retry_delay=300,  # 5 minutes between retries
    time_limit=7200,  # 2 hour hard limit
    soft_time_limit=3600,  # 1 hour soft limit
)
def run_database_backup(self) -> dict:
    """Execute a full database backup with automatic old backup cleanup.

    This task:
    1. Creates a compressed pg_dump of the PostgreSQL database
    2. Saves it to the configured backup directory with a date-stamped name
    3. Removes backups older than 8 weeks (keeps most recent 8)

    Returns
    -------
    dict
        Summary of the backup operation including file path, size,
        and cleanup statistics.
    """
    log.info("backup.task.start")

    try:
        backup_dir = _get_backup_dir()
        filename = _generate_backup_filename()
        backup_path = backup_dir / filename

        # Step 1: Run pg_dump
        actual_path = _run_pg_dump(backup_path)

        # Step 2: Cleanup old backups (keep last 8 weeks)
        removed_count = _cleanup_old_backups(backup_dir, keep=RETENTION_WEEKS)

        result = {
            "status": "success",
            "backup_file": str(actual_path),
            "size_bytes": actual_path.stat().st_size,
            "removed_old_backups": removed_count,
            "retention_weeks": RETENTION_WEEKS,
        }

        log.info("backup.task.complete", **result)
        return result

    except subprocess.CalledProcessError as e:
        log.error(
            "backup.task.pg_dump_failed",
            returncode=e.returncode,
            stderr=e.stderr.decode("utf-8", errors="replace") if e.stderr else "",
        )
        raise self.retry(exc=e)

    except subprocess.TimeoutExpired as e:
        log.error("backup.task.timeout", timeout=e.timeout)
        raise self.retry(exc=e)

    except Exception as e:
        log.error("backup.task.error", error=str(e), error_type=type(e).__name__)
        raise self.retry(exc=e)
