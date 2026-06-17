"""Unit tests for app.data.providers.snapshot.

Tests cover:
- save_raw / load_raw round-trip
- Path structure: {base_dir}/{provider}/{date}/{code}/{endpoint}.{ext}.gz
- Overwrite behaviour
- Extension validation
- exists() helper
- list_snapshots() filtering
- Parquet save/load round-trip
- SnapshotInfo metadata
- async_save_raw convenience wrapper
"""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pytest
import pytest_asyncio

from app.data.providers.snapshot import SnapshotArchive, SnapshotInfo, SnapshotVersion


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def archive(tmp_path: Path) -> SnapshotArchive:
    """SnapshotArchive backed by a temporary directory."""
    return SnapshotArchive(base_dir=tmp_path / "snapshots")


@pytest.fixture
def fixed_date() -> date:
    return date(2024, 5, 13)


@pytest.fixture
def archive_fixed(tmp_path: Path, fixed_date: date) -> SnapshotArchive:
    """SnapshotArchive with a fixed snapshot date for deterministic paths."""
    return SnapshotArchive(
        base_dir=tmp_path / "snapshots",
        snapshot_date=fixed_date,
    )


# ---------------------------------------------------------------------------
# save_raw / load_raw round-trip
# ---------------------------------------------------------------------------


class TestSaveLoadRaw:
    def test_round_trip_json(self, archive_fixed: SnapshotArchive) -> None:
        payload = b'{"nav": 1.234}'
        archive_fixed.save_raw("eastmoney", "000001", "nav_history", "json", payload)
        result = archive_fixed.load_raw("eastmoney", "000001", "nav_history", "json")
        assert result == payload

    def test_round_trip_html(self, archive_fixed: SnapshotArchive) -> None:
        payload = b"<html><body>fund info</body></html>"
        archive_fixed.save_raw("eastmoney", "000001", "fund_meta", "html", payload)
        result = archive_fixed.load_raw("eastmoney", "000001", "fund_meta", "html")
        assert result == payload

    def test_round_trip_binary_content(self, archive_fixed: SnapshotArchive) -> None:
        """Arbitrary bytes (e.g. binary-encoded JSON) survive the round-trip."""
        payload = bytes(range(256))
        archive_fixed.save_raw("akshare", "110022", "holdings", "json", payload)
        result = archive_fixed.load_raw("akshare", "110022", "holdings", "json")
        assert result == payload

    def test_empty_bytes_round_trip(self, archive_fixed: SnapshotArchive) -> None:
        archive_fixed.save_raw("eastmoney", "000001", "ping", "json", b"")
        result = archive_fixed.load_raw("eastmoney", "000001", "ping", "json")
        assert result == b""

    def test_large_payload_round_trip(self, archive_fixed: SnapshotArchive) -> None:
        """1 MB payload should compress and decompress correctly."""
        payload = b"x" * 1_000_000
        archive_fixed.save_raw("eastmoney", "000001", "bulk", "json", payload)
        result = archive_fixed.load_raw("eastmoney", "000001", "bulk", "json")
        assert result == payload


# ---------------------------------------------------------------------------
# Path structure
# ---------------------------------------------------------------------------


class TestPathStructure:
    def test_path_format(self, tmp_path: Path, fixed_date: date) -> None:
        """Verify the exact path: {base}/{provider}/{date}/{code}/{endpoint}.{ext}.gz"""
        base = tmp_path / "snapshots"
        archive = SnapshotArchive(base_dir=base, snapshot_date=fixed_date)
        returned_path = archive.save_raw(
            "eastmoney", "000001", "nav_history", "json", b"{}"
        )
        expected = base / "eastmoney" / "2024-05-13" / "000001" / "nav_history.json.gz"
        assert returned_path == expected

    def test_file_is_gzip_compressed(self, archive_fixed: SnapshotArchive, tmp_path: Path) -> None:
        """The stored file must be valid gzip."""
        path = archive_fixed.save_raw(
            "eastmoney", "000001", "nav_history", "json", b'{"test": 1}'
        )
        # gzip.open should not raise
        with gzip.open(path, "rb") as fh:
            content = fh.read()
        assert content == b'{"test": 1}'

    def test_directories_created_automatically(
        self, tmp_path: Path, fixed_date: date
    ) -> None:
        base = tmp_path / "deep" / "nested" / "snapshots"
        archive = SnapshotArchive(base_dir=base, snapshot_date=fixed_date)
        archive.save_raw("eastmoney", "000001", "nav_history", "json", b"{}")
        assert (base / "eastmoney" / "2024-05-13" / "000001").is_dir()

    def test_date_override_per_call(self, archive: SnapshotArchive) -> None:
        """snapshot_date kwarg on save_raw overrides the archive-level date."""
        d1 = date(2024, 1, 1)
        d2 = date(2024, 6, 15)
        archive.save_raw("p", "c", "ep", "json", b"a", snapshot_date=d1)
        archive.save_raw("p", "c", "ep", "json", b"b", snapshot_date=d2)
        assert archive.load_raw("p", "c", "ep", "json", snapshot_date=d1) == b"a"
        assert archive.load_raw("p", "c", "ep", "json", snapshot_date=d2) == b"b"


# ---------------------------------------------------------------------------
# Overwrite behaviour
# ---------------------------------------------------------------------------


class TestOverwrite:
    def test_overwrite_true_replaces_file(self, archive_fixed: SnapshotArchive) -> None:
        archive_fixed.save_raw("p", "c", "ep", "json", b"original")
        archive_fixed.save_raw("p", "c", "ep", "json", b"updated", overwrite=True)
        assert archive_fixed.load_raw("p", "c", "ep", "json") == b"updated"

    def test_overwrite_false_keeps_original(self, archive_fixed: SnapshotArchive) -> None:
        archive_fixed.save_raw("p", "c", "ep", "json", b"original")
        archive_fixed.save_raw("p", "c", "ep", "json", b"updated", overwrite=False)
        assert archive_fixed.load_raw("p", "c", "ep", "json") == b"original"

    def test_overwrite_false_returns_existing_path(
        self, archive_fixed: SnapshotArchive
    ) -> None:
        p1 = archive_fixed.save_raw("p", "c", "ep", "json", b"original")
        p2 = archive_fixed.save_raw("p", "c", "ep", "json", b"new", overwrite=False)
        assert p1 == p2


# ---------------------------------------------------------------------------
# Extension validation
# ---------------------------------------------------------------------------


class TestExtensionValidation:
    @pytest.mark.parametrize("ext", ["json", "html", "js", "xml", "csv", "txt"])
    def test_valid_extensions_accepted(
        self, archive_fixed: SnapshotArchive, ext: str
    ) -> None:
        archive_fixed.save_raw("p", "c", "ep", ext, b"data")  # should not raise

    @pytest.mark.parametrize("ext", ["pdf", "zip", "exe", "py", ""])
    def test_invalid_extensions_rejected(
        self, archive_fixed: SnapshotArchive, ext: str
    ) -> None:
        with pytest.raises(ValueError, match="extension"):
            archive_fixed.save_raw("p", "c", "ep", ext, b"data")

    def test_load_invalid_extension_raises(self, archive_fixed: SnapshotArchive) -> None:
        with pytest.raises(ValueError):
            archive_fixed.load_raw("p", "c", "ep", "pdf")


# ---------------------------------------------------------------------------
# FileNotFoundError on missing snapshot
# ---------------------------------------------------------------------------


class TestLoadMissing:
    def test_load_missing_raises_file_not_found(
        self, archive_fixed: SnapshotArchive
    ) -> None:
        with pytest.raises(FileNotFoundError):
            archive_fixed.load_raw("eastmoney", "000001", "nav_history", "json")

    def test_load_parquet_missing_raises_file_not_found(
        self, archive_fixed: SnapshotArchive
    ) -> None:
        with pytest.raises(FileNotFoundError):
            archive_fixed.load_parquet("eastmoney", "000001", "nav_history")


# ---------------------------------------------------------------------------
# exists()
# ---------------------------------------------------------------------------


class TestExists:
    def test_exists_false_before_save(self, archive_fixed: SnapshotArchive) -> None:
        assert archive_fixed.exists("p", "c", "ep", "json") is False

    def test_exists_true_after_save(self, archive_fixed: SnapshotArchive) -> None:
        archive_fixed.save_raw("p", "c", "ep", "json", b"data")
        assert archive_fixed.exists("p", "c", "ep", "json") is True

    def test_exists_false_for_invalid_ext(self, archive_fixed: SnapshotArchive) -> None:
        assert archive_fixed.exists("p", "c", "ep", "pdf") is False

    def test_exists_respects_date(self, archive: SnapshotArchive) -> None:
        d1 = date(2024, 1, 1)
        d2 = date(2024, 6, 1)
        archive.save_raw("p", "c", "ep", "json", b"x", snapshot_date=d1)
        assert archive.exists("p", "c", "ep", "json", snapshot_date=d1) is True
        assert archive.exists("p", "c", "ep", "json", snapshot_date=d2) is False


# ---------------------------------------------------------------------------
# list_snapshots()
# ---------------------------------------------------------------------------


class TestListSnapshots:
    def _populate(self, archive: SnapshotArchive) -> None:
        d1 = date(2024, 1, 1)
        d2 = date(2024, 6, 1)
        archive.save_raw("eastmoney", "000001", "nav_history", "json", b"a", snapshot_date=d1)
        archive.save_raw("eastmoney", "000001", "fund_meta", "html", b"b", snapshot_date=d1)
        archive.save_raw("eastmoney", "110022", "nav_history", "json", b"c", snapshot_date=d2)
        archive.save_raw("akshare", "000001", "nav_history", "json", b"d", snapshot_date=d1)

    def test_list_all(self, archive: SnapshotArchive) -> None:
        self._populate(archive)
        infos = archive.list_snapshots()
        assert len(infos) == 4

    def test_filter_by_provider(self, archive: SnapshotArchive) -> None:
        self._populate(archive)
        infos = archive.list_snapshots(provider="akshare")
        assert len(infos) == 1
        assert all(i.provider == "akshare" for i in infos)

    def test_filter_by_fund_code(self, archive: SnapshotArchive) -> None:
        self._populate(archive)
        infos = archive.list_snapshots(fund_code="000001")
        assert len(infos) == 3
        assert all(i.fund_code == "000001" for i in infos)

    def test_filter_by_date(self, archive: SnapshotArchive) -> None:
        self._populate(archive)
        infos = archive.list_snapshots(snapshot_date=date(2024, 1, 1))
        assert len(infos) == 3

    def test_filter_combined(self, archive: SnapshotArchive) -> None:
        self._populate(archive)
        infos = archive.list_snapshots(
            provider="eastmoney",
            fund_code="000001",
            snapshot_date=date(2024, 1, 1),
        )
        assert len(infos) == 2

    def test_empty_archive_returns_empty_list(self, archive: SnapshotArchive) -> None:
        assert archive.list_snapshots() == []

    def test_snapshot_info_fields(self, archive: SnapshotArchive) -> None:
        d = date(2024, 5, 13)
        archive.save_raw("eastmoney", "000001", "nav_history", "json", b"data", snapshot_date=d)
        infos = archive.list_snapshots()
        assert len(infos) == 1
        info = infos[0]
        assert info.provider == "eastmoney"
        assert info.fund_code == "000001"
        assert info.endpoint == "nav_history"
        assert info.ext == "json"
        assert info.snapshot_date == d
        assert info.size_bytes > 0
        assert info.path.exists()


# ---------------------------------------------------------------------------
# Version index / point-in-time replay
# ---------------------------------------------------------------------------


class TestSnapshotVersionIndex:
    def test_save_raw_writes_immutable_version_index(self, archive: SnapshotArchive) -> None:
        captured_at = datetime(2024, 5, 13, 9, 30, tzinfo=timezone.utc)
        archive.save_raw(
            "eastmoney",
            "000001",
            "nav_history",
            "json",
            b'{"nav": 1.0}',
            snapshot_date=date(2024, 5, 13),
            captured_at=captured_at,
        )

        versions = archive.list_versions(
            provider="eastmoney",
            fund_code="000001",
            endpoint="nav_history",
            ext="json",
        )

        assert len(versions) == 1
        version = versions[0]
        assert isinstance(version, SnapshotVersion)
        assert version.version_id
        assert version.captured_at == captured_at
        assert version.sha256
        assert version.path.exists()
        assert "_versions" in version.path.parts
        assert archive.load_raw_version(version.version_id) == b'{"nav": 1.0}'

    def test_load_raw_as_of_returns_latest_visible_version(self, archive: SnapshotArchive) -> None:
        d = date(2024, 5, 13)
        archive.save_raw(
            "eastmoney",
            "000001",
            "nav_history",
            "json",
            b"old",
            snapshot_date=d,
            captured_at=datetime(2024, 5, 13, 9, 0, tzinfo=timezone.utc),
        )
        archive.save_raw(
            "eastmoney",
            "000001",
            "nav_history",
            "json",
            b"new",
            snapshot_date=d,
            captured_at=datetime(2024, 5, 13, 15, 0, tzinfo=timezone.utc),
        )

        morning = archive.load_raw_as_of(
            "eastmoney",
            "000001",
            "nav_history",
            "json",
            snapshot_date=d,
            as_of=datetime(2024, 5, 13, 10, 0, tzinfo=timezone.utc),
        )
        evening = archive.load_raw_as_of(
            "eastmoney",
            "000001",
            "nav_history",
            "json",
            snapshot_date=d,
            as_of=datetime(2024, 5, 13, 16, 0, tzinfo=timezone.utc),
        )

        assert morning == b"old"
        assert evening == b"new"

    def test_list_versions_filters_as_of_date_to_end_of_day(self, archive: SnapshotArchive) -> None:
        archive.save_raw(
            "eastmoney",
            "000001",
            "fund_meta",
            "html",
            b"day1",
            snapshot_date=date(2024, 5, 13),
            captured_at=datetime(2024, 5, 13, 23, 59, tzinfo=timezone.utc),
        )
        archive.save_raw(
            "eastmoney",
            "000001",
            "fund_meta",
            "html",
            b"day2",
            snapshot_date=date(2024, 5, 14),
            captured_at=datetime(2024, 5, 14, 0, 1, tzinfo=timezone.utc),
        )

        versions = archive.list_versions(
            provider="eastmoney",
            fund_code="000001",
            endpoint="fund_meta",
            as_of=date(2024, 5, 13),
        )

        assert len(versions) == 1
        assert archive.load_raw_version(versions[0].version_id) == b"day1"

    def test_overwrite_false_does_not_append_duplicate_index(self, archive: SnapshotArchive) -> None:
        d = date(2024, 5, 13)
        archive.save_raw("p", "c", "ep", "json", b"original", snapshot_date=d)
        archive.save_raw("p", "c", "ep", "json", b"new", snapshot_date=d, overwrite=False)

        versions = archive.list_versions(provider="p", fund_code="c", endpoint="ep")
        assert len(versions) == 1
        assert archive.load_raw_version(versions[0].version_id) == b"original"

    def test_index_can_be_disabled_for_legacy_only_writes(self, archive: SnapshotArchive) -> None:
        d = date(2024, 5, 13)
        archive.save_raw("p", "c", "ep", "json", b"legacy", snapshot_date=d, index=False)

        assert archive.list_versions(provider="p", fund_code="c", endpoint="ep") == []
        assert archive.load_raw_as_of(
            "p",
            "c",
            "ep",
            "json",
            snapshot_date=d,
            as_of=date(2024, 5, 13),
        ) == b"legacy"


# ---------------------------------------------------------------------------
# SnapshotInfo
# ---------------------------------------------------------------------------


class TestSnapshotInfo:
    def test_frozen_immutable(self, tmp_path: Path) -> None:
        info = SnapshotInfo(
            provider="p",
            snapshot_date=date(2024, 1, 1),
            fund_code="c",
            endpoint="ep",
            ext="json",
            path=tmp_path / "x.gz",
            size_bytes=100,
        )
        with pytest.raises((AttributeError, TypeError)):
            info.provider = "other"  # type: ignore[misc]

    def test_version_to_info_preserves_index_fields(self, tmp_path: Path) -> None:
        captured_at = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        version = SnapshotVersion(
            provider="p",
            snapshot_date=date(2024, 1, 1),
            fund_code="c",
            endpoint="ep",
            ext="json",
            path=tmp_path / "x.gz",
            size_bytes=100,
            version_id="v1",
            captured_at=captured_at,
            sha256="abc",
        )

        info = version.to_info()

        assert info.version_id == "v1"
        assert info.captured_at == captured_at
        assert info.sha256 == "abc"


# ---------------------------------------------------------------------------
# Parquet save/load
# ---------------------------------------------------------------------------


class TestParquet:
    def _make_table(self) -> pa.Table:
        return pa.table(
            {
                "fund_code": ["000001", "000001"],
                "trade_date": ["2024-01-01", "2024-01-02"],
                "unit_nav": [1.234, 1.240],
            }
        )

    def test_round_trip(self, archive_fixed: SnapshotArchive) -> None:
        table = self._make_table()
        archive_fixed.save_parquet("eastmoney", "000001", "nav_history", table)
        loaded = archive_fixed.load_parquet("eastmoney", "000001", "nav_history")
        assert loaded.num_rows == 2
        assert loaded.column_names == ["fund_code", "trade_date", "unit_nav"]

    def test_parquet_path_format(
        self, tmp_path: Path, fixed_date: date
    ) -> None:
        base = tmp_path / "snapshots"
        archive = SnapshotArchive(base_dir=base, snapshot_date=fixed_date)
        path = archive.save_parquet(
            "eastmoney", "000001", "nav_history", self._make_table()
        )
        expected = base / "eastmoney" / "2024-05-13" / "000001" / "nav_history.parquet"
        assert path == expected

    def test_overwrite_false_keeps_original(self, archive_fixed: SnapshotArchive) -> None:
        t1 = pa.table({"a": [1]})
        t2 = pa.table({"a": [2]})
        archive_fixed.save_parquet("p", "c", "ep", t1)
        archive_fixed.save_parquet("p", "c", "ep", t2, overwrite=False)
        loaded = archive_fixed.load_parquet("p", "c", "ep")
        assert loaded["a"][0].as_py() == 1

    def test_overwrite_true_replaces(self, archive_fixed: SnapshotArchive) -> None:
        t1 = pa.table({"a": [1]})
        t2 = pa.table({"a": [2]})
        archive_fixed.save_parquet("p", "c", "ep", t1)
        archive_fixed.save_parquet("p", "c", "ep", t2, overwrite=True)
        loaded = archive_fixed.load_parquet("p", "c", "ep")
        assert loaded["a"][0].as_py() == 2

    def test_date_override(self, archive: SnapshotArchive) -> None:
        d1 = date(2024, 1, 1)
        d2 = date(2024, 6, 1)
        t1 = pa.table({"v": [1]})
        t2 = pa.table({"v": [2]})
        archive.save_parquet("p", "c", "ep", t1, snapshot_date=d1)
        archive.save_parquet("p", "c", "ep", t2, snapshot_date=d2)
        assert archive.load_parquet("p", "c", "ep", snapshot_date=d1)["v"][0].as_py() == 1
        assert archive.load_parquet("p", "c", "ep", snapshot_date=d2)["v"][0].as_py() == 2


# ---------------------------------------------------------------------------
# async_save_raw
# ---------------------------------------------------------------------------


class TestAsyncSaveRaw:
    @pytest.mark.asyncio
    async def test_async_save_round_trip(self, archive_fixed: SnapshotArchive) -> None:
        payload = b'{"async": true}'
        await archive_fixed.async_save_raw(
            "eastmoney", "000001", "nav_history", "json", payload
        )
        result = archive_fixed.load_raw("eastmoney", "000001", "nav_history", "json")
        assert result == payload

    @pytest.mark.asyncio
    async def test_async_save_returns_path(self, archive_fixed: SnapshotArchive) -> None:
        path = await archive_fixed.async_save_raw(
            "eastmoney", "000001", "nav_history", "json", b"{}"
        )
        assert path.exists()
        assert path.suffix == ".gz"


# ---------------------------------------------------------------------------
# repr
# ---------------------------------------------------------------------------


class TestRepr:
    def test_repr_contains_base_dir(self, tmp_path: Path) -> None:
        base = tmp_path / "snapshots"
        archive = SnapshotArchive(base_dir=base)
        assert "snapshots" in repr(archive)
