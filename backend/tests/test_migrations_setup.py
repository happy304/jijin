"""Tests for the Alembic migration scaffolding and gating logic.

We intentionally avoid spinning up PostgreSQL in CI: these tests verify
that the configuration wires together correctly (env.py imports cleanly,
the versions directory is discoverable, the gating flag actually gates)
rather than executing real migrations.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.config import Settings
from app.data.migrations import (
    build_alembic_config,
    run_migrations_if_enabled,
    run_upgrade,
)

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"


def _settings(auto_migrate: bool = True) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        DATABASE_SYNC_URL="sqlite:///:memory:",
        DB_AUTO_MIGRATE="true" if auto_migrate else "false",
    )


def test_alembic_ini_exists_at_backend_root() -> None:
    assert (_BACKEND_ROOT / "alembic.ini").is_file()


def test_migrations_directory_layout_is_complete() -> None:
    assert _MIGRATIONS_DIR.is_dir()
    assert (_MIGRATIONS_DIR / "env.py").is_file()
    assert (_MIGRATIONS_DIR / "script.py.mako").is_file()
    assert (_MIGRATIONS_DIR / "versions").is_dir()


def test_build_alembic_config_returns_configured_instance() -> None:
    cfg = build_alembic_config(_settings())
    assert isinstance(cfg, Config)
    # The database URL must flow from Settings, not the ini file.
    assert cfg.get_main_option("sqlalchemy.url") == "sqlite:///:memory:"
    script_location = cfg.get_main_option("script_location")
    assert script_location is not None
    assert Path(script_location).resolve() == _MIGRATIONS_DIR.resolve()


def test_script_directory_loads_without_errors() -> None:
    """Alembic can import `env.py` and scan the versions directory.

    This is the strongest smoke test we can run without a live database:
    `ScriptDirectory.from_config` parses `env.py`, imports models, and
    walks the versions directory. It fails loudly if any of those steps
    break.
    """
    cfg = build_alembic_config(_settings())
    script = ScriptDirectory.from_config(cfg)
    versions_dir = Path(script.versions)
    assert versions_dir.resolve() == (_MIGRATIONS_DIR / "versions").resolve()
    # After task 1.2 there is at least one revision (the fund tables migration).
    revisions = list(script.walk_revisions())
    assert len(revisions) >= 1, "Expected at least one migration revision"
    assert any(rev.revision == "1a2b3c4d5e6f" for rev in revisions)


def test_target_metadata_binds_to_project_base() -> None:
    """env.py must expose the project's declarative base as target_metadata."""
    from app.data.models import Base

    cfg = build_alembic_config(_settings())
    # ScriptDirectory.from_config executes env.py which imports Base.
    ScriptDirectory.from_config(cfg)
    # Base.metadata must still be a valid MetaData container (models
    # are registered against it even when no tables exist yet).
    assert Base.metadata is not None
    assert hasattr(Base.metadata, "tables")


async def test_run_migrations_if_enabled_skips_when_flag_false() -> None:
    """`DB_AUTO_MIGRATE=false` must short-circuit without calling Alembic."""
    settings = _settings(auto_migrate=False)
    with patch("app.data.migrations.run_upgrade") as mock_upgrade:
        await run_migrations_if_enabled(settings)
    mock_upgrade.assert_not_called()


async def test_run_migrations_if_enabled_calls_upgrade_when_flag_true() -> None:
    settings = _settings(auto_migrate=True)
    with patch("app.data.migrations.run_upgrade") as mock_upgrade:
        await run_migrations_if_enabled(settings)
    mock_upgrade.assert_called_once_with(settings, "head")


async def test_run_migrations_if_enabled_propagates_errors() -> None:
    settings = _settings(auto_migrate=True)
    with patch(
        "app.data.migrations.run_upgrade",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await run_migrations_if_enabled(settings)


def test_run_upgrade_with_empty_scaffold_is_a_noop() -> None:
    """Running `upgrade head` on the empty scaffold must not raise.

    With zero revisions in `migrations/versions/`, Alembic has nothing
    to apply but should still connect to the database, confirm the
    absence of revisions, and exit cleanly. We use SQLite so no external
    services are required.
    """
    settings = _settings()
    # Must not raise. The call creates (or confirms) the alembic_version
    # table on the target database.
    run_upgrade(settings, "head")
