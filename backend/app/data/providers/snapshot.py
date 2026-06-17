"""Raw response snapshot archival.

Implements requirement 1.9: every successful provider response is
persisted to local cold storage in compressed form for post-hoc auditing
and as a fallback data source.

Storage layout
--------------
All snapshots live under a configurable ``base_dir`` (default:
``./local_data/snapshots``).  Within that root the path is:

    {base_dir}/{provider}/{date}/{code}/{endpoint}.{ext}.gz

Examples::

    snapshots/eastmoney/2024-05-13/000001/nav_history.json.gz
    snapshots/eastmoney/2024-05-13/000001/fund_meta.html.gz
    snapshots/akshare/2024-05-13/110022/holdings.json.gz

Design notes
------------
* Raw bytes are written as-is after gzip compression.  The caller is
  responsible for encoding strings to bytes before passing them in.
* ``save_raw`` is the primary write path and now also appends an immutable
  JSONL version index by default for point-in-time replay.
* ``load_raw`` decompresses and returns the original bytes.
* ``list_snapshots`` returns legacy path metadata; ``list_versions`` /
  ``load_raw_as_of`` use the version index for historical replay.
* All I/O is synchronous (``pathlib`` + ``gzip`` stdlib) because
  snapshot writes happen in background tasks and do not need to be
  awaited on the critical path.  An async wrapper is provided for
  convenience when called from async contexts.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_BASE_DIR = Path("./local_data/snapshots")

# Supported raw content extensions
_VALID_EXTENSIONS = frozenset({"json", "html", "js", "xml", "csv", "txt"})


# ---------------------------------------------------------------------------
# Snapshot metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotInfo:
    """Metadata about a stored snapshot file.

    Attributes:
        provider: Provider name (e.g. ``"eastmoney"``).
        snapshot_date: Date the snapshot was captured.
        fund_code: Fund code.
        endpoint: Endpoint identifier (e.g. ``"nav_history"``).
        ext: Original content extension (``"json"``, ``"html"``, …).
        path: Absolute path to the ``.gz`` file on disk.
        size_bytes: Compressed file size in bytes.
        version_id: Immutable content/addressing id when the snapshot is
            indexed.  Legacy path-only snapshots expose ``None``.
        captured_at: UTC capture timestamp when indexed.
        sha256: Raw payload SHA-256 when indexed.
    """

    provider: str
    snapshot_date: date
    fund_code: str
    endpoint: str
    ext: str
    path: Path
    size_bytes: int
    version_id: str | None = None
    captured_at: datetime | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class SnapshotVersion:
    """Indexed immutable snapshot version for audit/replay."""

    provider: str
    snapshot_date: date
    fund_code: str
    endpoint: str
    ext: str
    path: Path
    size_bytes: int
    version_id: str
    captured_at: datetime
    sha256: str

    def to_info(self) -> SnapshotInfo:
        return SnapshotInfo(
            provider=self.provider,
            snapshot_date=self.snapshot_date,
            fund_code=self.fund_code,
            endpoint=self.endpoint,
            ext=self.ext,
            path=self.path,
            size_bytes=self.size_bytes,
            version_id=self.version_id,
            captured_at=self.captured_at,
            sha256=self.sha256,
        )


# ---------------------------------------------------------------------------
# SnapshotArchive
# ---------------------------------------------------------------------------


_INDEX_FILENAME = "_snapshot_index.jsonl"
_VERSION_DIR = "_versions"


class SnapshotArchive:
    """Manages compressed raw-response snapshots on the local filesystem.

    Usage::

        archive = SnapshotArchive(base_dir=Path("./local_data/snapshots"))

        # Save a raw JSON response
        archive.save_raw(
            provider="eastmoney",
            fund_code="000001",
            endpoint="nav_history",
            ext="json",
            data=response_bytes,
        )

        # Load it back
        raw = archive.load_raw(
            provider="eastmoney",
            fund_code="000001",
            endpoint="nav_history",
            ext="json",
        )

    Args:
        base_dir: Root directory for all snapshots.  Created on first
            write if it does not exist.
        snapshot_date: Date to use for the directory hierarchy.  Defaults
            to today (UTC) if not provided.  Useful for testing.
    """

    def __init__(
        self,
        base_dir: Path | str | None = None,
        *,
        snapshot_date: date | None = None,
    ) -> None:
        self._base_dir = Path(base_dir) if base_dir is not None else _DEFAULT_BASE_DIR
        self._snapshot_date = snapshot_date  # None → use today at call time
        self._index_path = self._base_dir / _INDEX_FILENAME

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_date(self, snapshot_date: date | None) -> date:
        """Return the effective snapshot date."""
        if snapshot_date is not None:
            return snapshot_date
        if self._snapshot_date is not None:
            return self._snapshot_date
        return datetime.now(tz=timezone.utc).date()

    def _snapshot_path(
        self,
        provider: str,
        fund_code: str,
        endpoint: str,
        ext: str,
        snapshot_date: date,
    ) -> Path:
        """Build the full path for a snapshot file.

        Path format::

            {base_dir}/{provider}/{date}/{code}/{endpoint}.{ext}.gz
        """
        date_str = snapshot_date.isoformat()  # YYYY-MM-DD
        return (
            self._base_dir
            / provider
            / date_str
            / fund_code
            / f"{endpoint}.{ext}.gz"
        )

    @staticmethod
    def _validate_ext(ext: str) -> None:
        """Raise ``ValueError`` if the extension is not in the allowed set."""
        if ext not in _VALID_EXTENSIONS:
            raise ValueError(
                f"Unsupported snapshot extension {ext!r}. "
                f"Allowed: {sorted(_VALID_EXTENSIONS)}"
            )

    @staticmethod
    def _metadata_to_version(metadata: dict[str, Any]) -> SnapshotVersion | None:
        """Convert one JSONL index row to :class:`SnapshotVersion`."""
        try:
            path = Path(str(metadata["path"]))
            return SnapshotVersion(
                provider=str(metadata["provider"]),
                snapshot_date=date.fromisoformat(str(metadata["snapshot_date"])),
                fund_code=str(metadata["fund_code"]),
                endpoint=str(metadata["endpoint"]),
                ext=str(metadata["ext"]),
                path=path,
                size_bytes=int(metadata.get("size_bytes") or 0),
                version_id=str(metadata["version_id"]),
                captured_at=datetime.fromisoformat(str(metadata["captured_at"])),
                sha256=str(metadata["sha256"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _version_snapshot_path(
        self,
        provider: str,
        fund_code: str,
        endpoint: str,
        ext: str,
        snapshot_date: date,
        version_id: str,
    ) -> Path:
        """Build immutable content-addressed snapshot version path."""
        return (
            self._base_dir
            / _VERSION_DIR
            / provider
            / snapshot_date.isoformat()
            / fund_code
            / endpoint
            / f"{version_id}.{ext}.gz"
        )

    def _index_record(
        self,
        provider: str,
        fund_code: str,
        endpoint: str,
        ext: str,
        snapshot_date: date,
        path: Path,
        data: bytes,
        captured_at: datetime,
    ) -> SnapshotVersion:
        """Append immutable snapshot metadata to the local JSONL index."""
        digest = hashlib.sha256(data).hexdigest()
        version_seed = "|".join(
            [
                provider,
                fund_code,
                endpoint,
                ext,
                snapshot_date.isoformat(),
                captured_at.isoformat(),
                digest,
            ]
        )
        version_id = hashlib.sha256(version_seed.encode("utf-8")).hexdigest()[:16]
        version_path = self._version_snapshot_path(
            provider,
            fund_code,
            endpoint,
            ext,
            snapshot_date,
            version_id,
        )
        version_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, version_path)

        metadata: dict[str, Any] = {
            "provider": provider,
            "snapshot_date": snapshot_date.isoformat(),
            "fund_code": fund_code,
            "endpoint": endpoint,
            "ext": ext,
            "path": str(version_path),
            "legacy_path": str(path),
            "size_bytes": version_path.stat().st_size,
            "captured_at": captured_at.isoformat(),
            "sha256": digest,
            "version_id": version_id,
        }
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        with self._index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
            fh.write("\n")

        return self._metadata_to_version(metadata)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save_raw(
        self,
        provider: str,
        fund_code: str,
        endpoint: str,
        ext: str,
        data: bytes,
        *,
        snapshot_date: date | None = None,
        overwrite: bool = True,
        index: bool = True,
        captured_at: datetime | None = None,
    ) -> Path:
        """Compress and persist raw response bytes.

        Args:
            provider: Provider name (e.g. ``"eastmoney"``).
            fund_code: Fund code (e.g. ``"000001"``).
            endpoint: Logical endpoint name (e.g. ``"nav_history"``).
            ext: Content type extension — one of ``json``, ``html``,
                ``js``, ``xml``, ``csv``, ``txt``.
            data: Raw response bytes to compress and store.
            snapshot_date: Override the date component of the path.
                Defaults to today (UTC).
            overwrite: If ``False`` and the file already exists, skip
                writing and return the existing path.
            index: If ``True`` (default), also append immutable version
                metadata and copy the compressed payload to ``_versions``.
            captured_at: Optional UTC timestamp for deterministic tests or
                replay imports.  Defaults to current UTC time.

        Returns:
            The ``Path`` of the written ``.gz`` file.

        Raises:
            ValueError: If ``ext`` is not in the allowed set.
        """
        self._validate_ext(ext)
        effective_date = self._resolve_date(snapshot_date)
        path = self._snapshot_path(provider, fund_code, endpoint, ext, effective_date)

        if not overwrite and path.exists():
            logger.debug("Snapshot already exists, skipping: %s", path)
            return path

        path.parent.mkdir(parents=True, exist_ok=True)

        with gzip.open(path, "wb", compresslevel=6) as fh:
            fh.write(data)

        if index:
            self._index_record(
                provider=provider,
                fund_code=fund_code,
                endpoint=endpoint,
                ext=ext,
                snapshot_date=effective_date,
                path=path,
                data=data,
                captured_at=captured_at or datetime.now(tz=timezone.utc),
            )

        logger.debug(
            "Saved snapshot: provider=%s fund=%s endpoint=%s date=%s size=%d bytes",
            provider,
            fund_code,
            endpoint,
            effective_date,
            len(data),
        )
        return path

    async def async_save_raw(
        self,
        provider: str,
        fund_code: str,
        endpoint: str,
        ext: str,
        data: bytes,
        *,
        snapshot_date: date | None = None,
        overwrite: bool = True,
        index: bool = True,
        captured_at: datetime | None = None,
    ) -> Path:
        """Async wrapper around :meth:`save_raw`.

        Runs the synchronous I/O in the calling coroutine (acceptable
        for background tasks where blocking is tolerable).  For
        high-throughput scenarios, wrap with ``asyncio.to_thread``.
        """
        return self.save_raw(
            provider,
            fund_code,
            endpoint,
            ext,
            data,
            snapshot_date=snapshot_date,
            overwrite=overwrite,
            index=index,
            captured_at=captured_at,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load_raw(
        self,
        provider: str,
        fund_code: str,
        endpoint: str,
        ext: str,
        *,
        snapshot_date: date | None = None,
    ) -> bytes:
        """Decompress and return the raw bytes of a stored snapshot.

        Args:
            provider: Provider name.
            fund_code: Fund code.
            endpoint: Endpoint name.
            ext: Content extension (without ``.gz``).
            snapshot_date: Date component of the path.  Defaults to today.

        Returns:
            Decompressed raw bytes.

        Raises:
            FileNotFoundError: If no snapshot exists at the computed path.
            ValueError: If ``ext`` is not in the allowed set.
        """
        self._validate_ext(ext)
        effective_date = self._resolve_date(snapshot_date)
        path = self._snapshot_path(provider, fund_code, endpoint, ext, effective_date)

        if not path.exists():
            raise FileNotFoundError(
                f"Snapshot not found: {path}"
            )

        with gzip.open(path, "rb") as fh:
            return fh.read()

    def exists(
        self,
        provider: str,
        fund_code: str,
        endpoint: str,
        ext: str,
        *,
        snapshot_date: date | None = None,
    ) -> bool:
        """Return ``True`` if a snapshot file exists at the computed path.

        Args:
            provider: Provider name.
            fund_code: Fund code.
            endpoint: Endpoint name.
            ext: Content extension.
            snapshot_date: Date component of the path.  Defaults to today.
        """
        try:
            self._validate_ext(ext)
        except ValueError:
            return False
        effective_date = self._resolve_date(snapshot_date)
        path = self._snapshot_path(provider, fund_code, endpoint, ext, effective_date)
        return path.exists()

    def _iter_index_records(self) -> Iterator[SnapshotVersion]:
        """Yield indexed snapshot versions from the JSONL index."""
        if not self._index_path.exists():
            return
        with self._index_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed snapshot index row: %s", line[:120])
                    continue
                version = self._metadata_to_version(row)
                if version is not None:
                    yield version

    def list_versions(
        self,
        provider: str | None = None,
        fund_code: str | None = None,
        endpoint: str | None = None,
        ext: str | None = None,
        snapshot_date: date | None = None,
        as_of: date | datetime | None = None,
    ) -> list[SnapshotVersion]:
        """Return indexed immutable snapshot versions for audit/replay.

        ``endpoint`` and ``ext`` narrow the logical raw response identity.
        ``as_of`` filters by ``captured_at``.  A plain ``date`` means end of
        that UTC day, which mirrors Advisor historical replay semantics.
        """
        cutoff: datetime | None = None
        if isinstance(as_of, datetime):
            cutoff = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=timezone.utc)
        elif isinstance(as_of, date):
            cutoff = datetime.combine(as_of, datetime.max.time(), tzinfo=timezone.utc)

        versions: list[SnapshotVersion] = []
        for version in self._iter_index_records():
            captured_at = version.captured_at
            if captured_at.tzinfo is None:
                captured_at = captured_at.replace(tzinfo=timezone.utc)
            if provider is not None and version.provider != provider:
                continue
            if fund_code is not None and version.fund_code != fund_code:
                continue
            if endpoint is not None and version.endpoint != endpoint:
                continue
            if ext is not None and version.ext != ext:
                continue
            if snapshot_date is not None and version.snapshot_date != snapshot_date:
                continue
            if cutoff is not None and captured_at > cutoff:
                continue
            if not version.path.exists():
                continue
            versions.append(version)

        versions.sort(key=lambda item: (item.captured_at, item.snapshot_date, item.version_id))
        return versions

    def latest_version(
        self,
        provider: str,
        fund_code: str,
        endpoint: str,
        *,
        ext: str | None = None,
        snapshot_date: date | None = None,
        as_of: date | datetime | None = None,
    ) -> SnapshotVersion | None:
        """Return the latest indexed version visible at ``as_of``."""
        versions = self.list_versions(
            provider=provider,
            fund_code=fund_code,
            endpoint=endpoint,
            ext=ext,
            snapshot_date=snapshot_date,
            as_of=as_of,
        )
        if not versions:
            return None
        return versions[-1]

    def load_raw_version(self, version_id: str) -> bytes:
        """Load raw bytes for an immutable indexed version."""
        for version in self._iter_index_records():
            if version.version_id != version_id:
                continue
            if not version.path.exists():
                raise FileNotFoundError(f"Snapshot version payload not found: {version.path}")
            with gzip.open(version.path, "rb") as fh:
                return fh.read()
        raise FileNotFoundError(f"Snapshot version not found in index: {version_id}")

    def load_raw_as_of(
        self,
        provider: str,
        fund_code: str,
        endpoint: str,
        ext: str,
        *,
        as_of: date | datetime,
        snapshot_date: date | None = None,
    ) -> bytes:
        """Load the latest raw payload visible at ``as_of``.

        Falls back to the legacy date path when no index exists and a
        ``snapshot_date`` is supplied, preserving backwards compatibility.
        """
        self._validate_ext(ext)
        version = self.latest_version(
            provider=provider,
            fund_code=fund_code,
            endpoint=endpoint,
            ext=ext,
            snapshot_date=snapshot_date,
            as_of=as_of,
        )
        if version is not None:
            if version.ext != ext:
                raise FileNotFoundError(
                    f"Snapshot version extension mismatch for {provider}/{fund_code}/{endpoint}: "
                    f"expected {ext!r}, got {version.ext!r}"
                )
            return self.load_raw_version(version.version_id)
        if snapshot_date is not None:
            return self.load_raw(
                provider,
                fund_code,
                endpoint,
                ext,
                snapshot_date=snapshot_date,
            )
        raise FileNotFoundError(
            f"Snapshot not found for provider={provider!r} fund={fund_code!r} "
            f"endpoint={endpoint!r} as_of={as_of!r}"
        )

    # ------------------------------------------------------------------
    # List / iterate
    # ------------------------------------------------------------------

    def list_snapshots(
        self,
        provider: str | None = None,
        fund_code: str | None = None,
        snapshot_date: date | None = None,
    ) -> list[SnapshotInfo]:
        """Return metadata for all matching snapshot files.

        Args:
            provider: Filter by provider name.  ``None`` = all providers.
            fund_code: Filter by fund code.  ``None`` = all funds.
            snapshot_date: Filter by date.  ``None`` = all dates.

        Returns:
            List of :class:`SnapshotInfo` objects, sorted by path.
        """
        return list(
            self._iter_snapshots(
                provider=provider,
                fund_code=fund_code,
                snapshot_date=snapshot_date,
            )
        )

    def _iter_snapshots(
        self,
        provider: str | None,
        fund_code: str | None,
        snapshot_date: date | None,
    ) -> Iterator[SnapshotInfo]:
        """Yield ``SnapshotInfo`` for each matching ``.gz`` file."""
        if not self._base_dir.exists():
            return

        # Determine the glob pattern based on filters
        provider_glob = provider if provider else "*"
        date_glob = snapshot_date.isoformat() if snapshot_date else "*"
        code_glob = fund_code if fund_code else "*"

        pattern = f"{provider_glob}/{date_glob}/{code_glob}/*.gz"

        for gz_path in sorted(self._base_dir.glob(pattern)):
            info = self._parse_snapshot_path(gz_path)
            if info is not None:
                yield info

    def _parse_snapshot_path(self, gz_path: Path) -> SnapshotInfo | None:
        """Parse a ``.gz`` path back into a ``SnapshotInfo``.

        Expected structure relative to ``base_dir``::

            {provider}/{date}/{code}/{endpoint}.{ext}.gz

        Returns ``None`` if the path does not match the expected structure.
        """
        try:
            rel = gz_path.relative_to(self._base_dir)
            parts = rel.parts  # (provider, date_str, code, filename)
            if len(parts) != 4:
                return None

            provider_name, date_str, code, filename = parts

            # filename = "{endpoint}.{ext}.gz"
            if not filename.endswith(".gz"):
                return None
            stem = filename[:-3]  # strip .gz
            dot_idx = stem.rfind(".")
            if dot_idx == -1:
                return None
            endpoint = stem[:dot_idx]
            ext = stem[dot_idx + 1:]

            snap_date = date.fromisoformat(date_str)

            return SnapshotInfo(
                provider=provider_name,
                snapshot_date=snap_date,
                fund_code=code,
                endpoint=endpoint,
                ext=ext,
                path=gz_path,
                size_bytes=gz_path.stat().st_size,
            )
        except (ValueError, OSError):
            return None

    # ------------------------------------------------------------------
    # Parquet helpers
    # ------------------------------------------------------------------

    def save_parquet(
        self,
        provider: str,
        fund_code: str,
        endpoint: str,
        table: pa.Table,
        *,
        snapshot_date: date | None = None,
        overwrite: bool = True,
    ) -> Path:
        """Persist a PyArrow Table as a Parquet file (uncompressed path).

        The file is stored alongside the raw snapshots using the same
        directory hierarchy but with a ``.parquet`` extension (no ``.gz``
        wrapper — Parquet has built-in compression via ``snappy``).

        Path format::

            {base_dir}/{provider}/{date}/{code}/{endpoint}.parquet

        Args:
            provider: Provider name.
            fund_code: Fund code.
            endpoint: Endpoint name.
            table: PyArrow Table to serialise.
            snapshot_date: Date component of the path.  Defaults to today.
            overwrite: If ``False`` and the file already exists, skip.

        Returns:
            The ``Path`` of the written ``.parquet`` file.
        """
        effective_date = self._resolve_date(snapshot_date)
        date_str = effective_date.isoformat()
        path = (
            self._base_dir
            / provider
            / date_str
            / fund_code
            / f"{endpoint}.parquet"
        )

        if not overwrite and path.exists():
            logger.debug("Parquet snapshot already exists, skipping: %s", path)
            return path

        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path, compression="snappy")

        logger.debug(
            "Saved Parquet snapshot: provider=%s fund=%s endpoint=%s date=%s rows=%d",
            provider,
            fund_code,
            endpoint,
            effective_date,
            table.num_rows,
        )
        return path

    def load_parquet(
        self,
        provider: str,
        fund_code: str,
        endpoint: str,
        *,
        snapshot_date: date | None = None,
    ) -> pa.Table:
        """Load a previously saved Parquet snapshot.

        Args:
            provider: Provider name.
            fund_code: Fund code.
            endpoint: Endpoint name.
            snapshot_date: Date component of the path.  Defaults to today.

        Returns:
            A PyArrow Table.

        Raises:
            FileNotFoundError: If no Parquet file exists at the path.
        """
        effective_date = self._resolve_date(snapshot_date)
        date_str = effective_date.isoformat()
        path = (
            self._base_dir
            / provider
            / date_str
            / fund_code
            / f"{endpoint}.parquet"
        )

        if not path.exists():
            raise FileNotFoundError(f"Parquet snapshot not found: {path}")

        return pq.read_table(path)

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"SnapshotArchive(base_dir={str(self._base_dir)!r})"
