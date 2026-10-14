"""Immutable warehouse snapshots and their atomic ``current`` pointer.

The publisher must run while the warehouse write lock is held. Snapshot files
are hard-linked on the same filesystem: normal warehouse publication replaces
whole files atomically, so an older inode remains stable without duplicating
its data blocks. Consumers still receive a read-only mount; the manifest hash
detects accidental in-place mutation by a privileged writer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import pyarrow.parquet as pq


SNAPSHOT_FORMAT = "qsys-warehouse-snapshot"
SNAPSHOT_VERSION = 1
SNAPSHOT_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
HASH_BLOCK_BYTES = 8 << 20


@dataclass(frozen=True, slots=True)
class Snapshot:
    root: Path
    manifest: Mapping[str, Any]

    @property
    def snapshot_id(self) -> str:
        return str(self.manifest["snapshot_id"])

    @property
    def warehouse(self) -> Path:
        return self.root / "warehouse"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(HASH_BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _safe_relative(raw: str) -> Path:
    value = PurePosixPath(raw)
    if value.is_absolute() or not value.parts or ".." in value.parts:
        raise ValueError(f"unsafe snapshot path: {raw!r}")
    return Path(*value.parts)


def _validate_snapshot_id(snapshot_id: str) -> str:
    if not SNAPSHOT_ID.fullmatch(snapshot_id) or snapshot_id in {".", ".."}:
        raise ValueError(f"invalid snapshot_id: {snapshot_id!r}")
    return snapshot_id


def _normalise_date(value: date | str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(value).isoformat()


def _normalise_created_at(value: datetime | None) -> str:
    created = value or datetime.now(timezone.utc)
    if created.tzinfo is None:
        raise ValueError("created_at must include a timezone")
    return created.astimezone(timezone.utc).isoformat()


def _source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"warehouse contains a symlink: {path.relative_to(root)}")
        if path.is_file():
            files.append(path)
    return files


def _partition_facts(path: Path, relative: Path) -> dict[str, object]:
    facts: dict[str, object] = {
        "path": relative.as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    if path.suffix == ".parquet":
        parquet = pq.ParquetFile(path)
        schema = parquet.schema_arrow.to_string()
        facts.update(
            rows=parquet.metadata.num_rows,
            parquet_schema_sha256=hashlib.sha256(schema.encode("utf-8")).hexdigest(),
        )
    return facts


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def create_snapshot(
    source_root: Path,
    store_root: Path,
    *,
    complete_through: date | str,
    schema_version: str,
    snapshot_id: str | None = None,
    created_at: datetime | None = None,
    source_revision: str = "",
    publish_current: bool = False,
) -> Snapshot:
    """Create and optionally publish one immutable warehouse snapshot.

    ``source_root`` and ``store_root`` must be on the same filesystem. The
    caller is responsible for holding the production warehouse write lock for
    the duration of this call.
    """
    source = Path(source_root).resolve(strict=True)
    if not source.is_dir():
        raise ValueError(f"warehouse root is not a directory: {source}")
    if not schema_version.strip():
        raise ValueError("schema_version must not be empty")

    store = Path(store_root)
    snapshots = store / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    stage = snapshots / f".building-{os.getpid()}-{uuid.uuid4().hex}"
    stage_warehouse = stage / "warehouse"
    stage_warehouse.mkdir(parents=True)
    try:
        source_files = _source_files(source)
        if not source_files:
            raise ValueError("warehouse does not contain any files")
        partitions: list[dict[str, object]] = []
        for original in source_files:
            relative = original.relative_to(source)
            target = stage_warehouse / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(original, target)
            except OSError as exc:
                raise OSError(
                    f"cannot hard-link {relative}; warehouse and snapshot store "
                    "must share a filesystem"
                ) from exc
            partitions.append(_partition_facts(target, relative))

        content_sha256 = _canonical_sha256(partitions)
        stamp = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        resolved_id = snapshot_id or f"{stamp:%Y%m%dT%H%M%SZ}-{content_sha256[:12]}"
        _validate_snapshot_id(resolved_id)
        final = snapshots / resolved_id
        if final.exists() or final.is_symlink():
            raise FileExistsError(f"snapshot already exists: {resolved_id}")

        manifest: dict[str, Any] = {
            "format": SNAPSHOT_FORMAT,
            "version": SNAPSHOT_VERSION,
            "snapshot_id": resolved_id,
            "created_at": _normalise_created_at(created_at),
            "complete_through": _normalise_date(complete_through),
            "schema_version": schema_version,
            "source_revision": source_revision,
            "partition_count": len(partitions),
            "content_sha256": content_sha256,
            "partitions": partitions,
        }
        _write_json(stage / "manifest.json", manifest)
        failures = verify_snapshot(stage)
        if failures:
            raise RuntimeError("new snapshot failed verification: " + "; ".join(failures[:3]))
        os.replace(stage, final)
        published = Snapshot(final, manifest)
        if publish_current:
            publish_current_snapshot(store, resolved_id)
        return published
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def load_snapshot(snapshot_root: Path) -> Snapshot:
    root = Path(snapshot_root).resolve(strict=True)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return Snapshot(root, manifest)


def verify_snapshot(snapshot_root: Path) -> list[str]:
    """Return every manifest or byte-integrity failure for one snapshot."""
    root = Path(snapshot_root).resolve(strict=True)
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return [f"manifest unreadable: {exc}"]

    failures: list[str] = []
    if manifest.get("format") != SNAPSHOT_FORMAT or manifest.get("version") != SNAPSHOT_VERSION:
        failures.append("unsupported snapshot manifest format or version")
    try:
        _validate_snapshot_id(str(manifest["snapshot_id"]))
        _normalise_date(manifest["complete_through"])
        created_at = datetime.fromisoformat(manifest["created_at"])
        if created_at.tzinfo is None:
            raise ValueError("created_at must include a timezone")
    except (KeyError, TypeError, ValueError) as exc:
        failures.append(f"invalid snapshot metadata: {exc}")
    if not str(manifest.get("schema_version", "")).strip():
        failures.append("schema_version is missing")

    warehouse = root / "warehouse"
    if not warehouse.is_dir() or warehouse.is_symlink():
        failures.append("warehouse directory is missing or is a symlink")
        return failures

    expected_paths: set[str] = set()
    verified_partitions: list[dict[str, object]] = []
    raw_partitions = manifest.get("partitions")
    if not isinstance(raw_partitions, list):
        failures.append("partitions must be a list")
        return failures
    for expected in raw_partitions:
        if not isinstance(expected, dict):
            failures.append("partition entry must be an object")
            continue
        try:
            relative = _safe_relative(expected["path"])
        except (KeyError, TypeError, ValueError) as exc:
            failures.append(str(exc))
            continue
        relative_name = relative.as_posix()
        if relative_name in expected_paths:
            failures.append(f"{relative_name}: duplicate manifest path")
            continue
        expected_paths.add(relative_name)
        path = warehouse / relative
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            failures.append(f"{relative_name}: missing")
            continue
        if path.is_symlink() or not resolved.is_relative_to(warehouse.resolve()):
            failures.append(f"{relative_name}: escapes warehouse or is a symlink")
            continue
        if not path.is_file():
            failures.append(f"{relative_name}: missing")
            continue
        try:
            actual = _partition_facts(path, relative)
        except Exception as exc:
            failures.append(f"{relative_name}: unreadable: {exc}")
            continue
        verified_partitions.append(actual)
        for field in ("bytes", "sha256", "rows", "parquet_schema_sha256"):
            if field in expected and actual.get(field) != expected.get(field):
                failures.append(
                    f"{relative_name}: {field} {actual.get(field)!r} != {expected.get(field)!r}"
                )

    actual_paths = {
        path.relative_to(warehouse).as_posix()
        for path in warehouse.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    for extra in sorted(actual_paths - expected_paths):
        failures.append(f"{extra}: unexpected file")
    if manifest.get("partition_count") != len(raw_partitions):
        failures.append("partition_count does not match partitions")
    if len(verified_partitions) == len(raw_partitions):
        actual_content_hash = _canonical_sha256(verified_partitions)
        if actual_content_hash != manifest.get("content_sha256"):
            failures.append("content_sha256 mismatch")
    return failures


def publish_current_snapshot(store_root: Path, snapshot_id: str) -> Snapshot:
    """Atomically point ``current`` at an already verified snapshot."""
    resolved_id = _validate_snapshot_id(snapshot_id)
    store = Path(store_root)
    snapshot_root = store / "snapshots" / resolved_id
    failures = verify_snapshot(snapshot_root)
    if failures:
        raise ValueError("cannot publish invalid snapshot: " + "; ".join(failures[:3]))
    manifest = load_snapshot(snapshot_root).manifest
    if manifest.get("snapshot_id") != resolved_id:
        raise ValueError("snapshot directory and manifest snapshot_id differ")

    store.mkdir(parents=True, exist_ok=True)
    temporary = store / f".current-{os.getpid()}-{uuid.uuid4().hex}"
    try:
        temporary.symlink_to(Path("snapshots") / resolved_id, target_is_directory=True)
        os.replace(temporary, store / "current")
    finally:
        temporary.unlink(missing_ok=True)
    return Snapshot(snapshot_root.resolve(strict=True), manifest)


def current_snapshot(store_root: Path) -> Snapshot:
    """Resolve ``current`` without allowing it to escape the snapshot store."""
    store = Path(store_root).resolve(strict=True)
    pointer = store / "current"
    if not pointer.is_symlink():
        raise FileNotFoundError(f"snapshot current pointer is missing: {pointer}")
    resolved = pointer.resolve(strict=True)
    snapshots = (store / "snapshots").resolve(strict=True)
    if resolved.parent != snapshots:
        raise ValueError("snapshot current pointer escapes the snapshot store")
    failures = verify_snapshot(resolved)
    if failures:
        raise ValueError("current snapshot is invalid: " + "; ".join(failures[:3]))
    return load_snapshot(resolved)
