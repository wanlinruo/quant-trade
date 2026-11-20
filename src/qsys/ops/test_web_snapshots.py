"""Immutable, read-only operational snapshots for the isolated test Web app.

This module deliberately excludes trading state and writable workbench data.  It
copies only the explicitly supplied ServiceRun, health, board and alert facts.
The caller is responsible for choosing approved production source paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


FORMAT = "qsys-test-web-ops-snapshot"
VERSION = 1
SNAPSHOT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ALERT_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\.events)?\.jsonl$")
HASH_BLOCK_BYTES = 8 << 20


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(HASH_BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _safe_name(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or len(path.parts) != 1 or value in {"", ".", ".."}:
        raise ValueError(f"unsafe source name: {value!r}")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value):
        raise ValueError(f"invalid source name: {value!r}")
    return value


def _reject_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise ValueError(f"source is a symlink: {root}")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"source contains a symlink: {path}")


def _source_directory(raw: Path, label: str) -> Path:
    if raw.is_symlink():
        raise ValueError(f"{label} source is a symlink: {raw}")
    root = raw.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"{label} source is not a directory: {root}")
    _reject_symlinks(root)
    return root


def _copy_bytes(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"source must be a regular file: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer, HASH_BLOCK_BYTES)


def _backup_sqlite(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"SQLite source must be a regular file: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    uri = f"file:{source.resolve(strict=True)}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise ValueError(f"SQLite backup failed integrity_check: {source}")


def _service_run_day(value: dict[str, Any], path: Path) -> date:
    raw = value.get("started_at")
    if not isinstance(raw, str):
        raise ValueError(f"ServiceRun lacks started_at: {path}")
    return datetime.fromisoformat(raw).date()


def _copy_service_runs(source: Path, target: Path, cutoff: date) -> int:
    source = _source_directory(source, "ServiceRun")
    copied = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix != ".json":
            raise ValueError(f"unexpected ServiceRun file: {path.relative_to(source)}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"ServiceRun is not an object: {path}")
        if _service_run_day(value, path) < cutoff:
            continue
        _copy_bytes(path, target / path.relative_to(source))
        copied += 1
    return copied


def _copy_alerts(
    sources: Iterable[tuple[str, Path]], target: Path, cutoff: date,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw_name, raw_root in sources:
        name = _safe_name(raw_name)
        root = _source_directory(raw_root, "alert")
        count = 0
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            match = ALERT_FILE.fullmatch(path.name)
            if match is None:
                raise ValueError(f"unexpected alert file: {path.relative_to(root)}")
            if date.fromisoformat(match.group(1)) < cutoff:
                continue
            # Reject truncated/invalid lines instead of publishing ambiguous evidence.
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    json.loads(line)
            _copy_bytes(path, target / name / path.relative_to(root))
            count += 1
        counts[name] = count
    return counts


def _copy_board(source: Path, target: Path) -> dict[str, bool]:
    source = _source_directory(source, "board")
    database = source / "board.sqlite3"
    _backup_sqlite(database, target / "board.sqlite3")
    status = source / "status.json"
    copied_status = False
    if status.is_file():
        value = json.loads(status.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("board status must be a JSON object")
        _copy_bytes(status, target / "status.json")
        copied_status = True
    # raw/*.sqlite3 is intentionally excluded: it is an unbounded intraday
    # full-market transport cache, not a required historical Web fact.
    return {"database": True, "status": copied_status, "raw_excluded": True}


def _inventory(root: Path) -> list[dict[str, object]]:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"snapshot contains a symlink: {path.relative_to(root)}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            files.append({"path": relative, "bytes": path.stat().st_size,
                          "sha256": _sha256(path)})
    return files


def _write_manifest(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def create_ops_snapshot(
    store_root: Path, *, service_runs: Path, health_state: Path,
    board_root: Path, alert_roots: Iterable[tuple[str, Path]],
    source_revision: str, retained_days: int = 35,
    captured_at: datetime | None = None, snapshot_id: str | None = None,
    publish_current: bool = False,
) -> dict[str, object]:
    """Build and optionally publish one complete operational snapshot."""
    if retained_days < 1 or retained_days > 366:
        raise ValueError("retained_days must be between 1 and 366")
    captured = captured_at or datetime.now(timezone.utc)
    if captured.tzinfo is None:
        raise ValueError("captured_at must include a timezone")
    captured = captured.astimezone(timezone.utc)
    cutoff = captured.date() - timedelta(days=retained_days - 1)
    store = Path(store_root)
    snapshots = store / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    stage = snapshots / f".building-{os.getpid()}-{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        counts = {
            "service_runs": _copy_service_runs(service_runs, stage / "service-runs", cutoff),
            "alerts": _copy_alerts(alert_roots, stage / "alerts", cutoff),
        }
        _backup_sqlite(health_state, stage / "service-health.sqlite3")
        board = _copy_board(board_root, stage / "limit-up-board")
        files = _inventory(stage)
        if not files:
            raise ValueError("snapshot contains no files")
        content_hash = _canonical_sha256(files)
        resolved_id = snapshot_id or f"{captured:%Y%m%dT%H%M%SZ}-{content_hash[:12]}"
        if not SNAPSHOT_ID.fullmatch(resolved_id) or resolved_id in {".", ".."}:
            raise ValueError(f"invalid snapshot_id: {resolved_id!r}")
        final = snapshots / resolved_id
        if final.exists() or final.is_symlink():
            raise FileExistsError(f"snapshot already exists: {resolved_id}")
        manifest: dict[str, object] = {
            "format": FORMAT, "version": VERSION, "snapshot_id": resolved_id,
            "captured_at": captured.isoformat(), "cutoff_date": cutoff.isoformat(),
            "retained_days": retained_days, "source_revision": source_revision,
            "content_sha256": content_hash, "file_count": len(files), "files": files,
            "counts": counts, "board": board,
            "excluded": ["live", "orders", "fills", "reconciliation", "paper", "board/raw"],
        }
        _write_manifest(stage / "manifest.json", manifest)
        failures = verify_ops_snapshot(stage, expected_snapshot_id=resolved_id)
        if failures:
            raise ValueError("new snapshot failed verification: " + "; ".join(failures[:3]))
        os.replace(stage, final)
        _make_read_only(final)
        if publish_current:
            publish_ops_snapshot(store, resolved_id)
        return manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _make_read_only(root: Path) -> None:
    for path in root.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def verify_ops_snapshot(
    root: Path, *, expected_snapshot_id: str | None = None,
) -> list[str]:
    try:
        resolved = Path(root).resolve(strict=True)
        manifest = json.loads((resolved / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"manifest unreadable: {exc}"]
    failures = []
    if manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
        failures.append("unsupported snapshot format or version")
    try:
        snapshot_id = str(manifest["snapshot_id"])
        expected_id = expected_snapshot_id or resolved.name
        if not SNAPSHOT_ID.fullmatch(snapshot_id) or snapshot_id != expected_id:
            raise ValueError("snapshot_id does not match the directory")
        captured = datetime.fromisoformat(str(manifest["captured_at"]))
        if captured.tzinfo is None:
            raise ValueError("captured_at lacks a timezone")
        date.fromisoformat(str(manifest["cutoff_date"]))
        retained_days = manifest["retained_days"]
        if not isinstance(retained_days, int) or not 1 <= retained_days <= 366:
            raise ValueError("retained_days is invalid")
        if not re.fullmatch(r"[0-9a-f]{40}", str(manifest["source_revision"])):
            raise ValueError("source_revision is not a full commit SHA")
        if manifest.get("excluded") != [
            "live", "orders", "fills", "reconciliation", "paper", "board/raw",
        ]:
            raise ValueError("excluded asset declaration changed")
    except (KeyError, TypeError, ValueError) as exc:
        failures.append(f"invalid snapshot metadata: {exc}")
    expected = manifest.get("files")
    if not isinstance(expected, list):
        return failures + ["files must be a list"]
    actual = _inventory(resolved)
    actual = [row for row in actual if row["path"] != "manifest.json"]
    if actual != expected:
        failures.append("file inventory mismatch")
    if _canonical_sha256(actual) != manifest.get("content_sha256"):
        failures.append("content_sha256 mismatch")
    if manifest.get("file_count") != len(expected):
        failures.append("file_count mismatch")
    return failures


def publish_ops_snapshot(store_root: Path, snapshot_id: str) -> None:
    if not SNAPSHOT_ID.fullmatch(snapshot_id) or snapshot_id in {".", ".."}:
        raise ValueError(f"invalid snapshot_id: {snapshot_id!r}")
    store = Path(store_root)
    target = store / "snapshots" / snapshot_id
    if target.is_symlink():
        raise ValueError("snapshot target must not be a symlink")
    resolved_store = (store / "snapshots").resolve(strict=True)
    resolved_target = target.resolve(strict=True)
    if resolved_target.parent != resolved_store:
        raise ValueError("snapshot target escapes the store")
    failures = verify_ops_snapshot(target)
    if failures:
        raise ValueError("cannot publish invalid snapshot: " + "; ".join(failures[:3]))
    temporary = store / f".current-{os.getpid()}-{uuid.uuid4().hex}"
    try:
        temporary.symlink_to(Path("snapshots") / snapshot_id, target_is_directory=True)
        os.replace(temporary, store / "current")
    finally:
        temporary.unlink(missing_ok=True)
