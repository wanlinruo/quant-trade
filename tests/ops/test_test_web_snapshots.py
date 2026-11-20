from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from qsys.ops.test_web_snapshots import (
    create_ops_snapshot,
    publish_ops_snapshot,
    verify_ops_snapshot,
)


CAPTURED = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


def _sqlite(path: Path, table: str = "facts") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, value TEXT)")
        con.execute(f"INSERT INTO {table} (value) VALUES ('observed')")


def _sources(tmp_path: Path) -> dict[str, object]:
    runs = tmp_path / "source" / "service-runs"
    recent = runs / "service-checks" / "2026" / "11" / "20" / "recent.json"
    recent.parent.mkdir(parents=True)
    recent.write_text(json.dumps({
        "run_id": "recent", "service": "service-checks",
        "started_at": "2026-11-20T08:00:00+08:00", "steps": [],
    }), encoding="utf-8")
    old = runs / "2026" / "09" / "01" / "old.json"
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps({
        "run_id": "old", "service": "service-checks",
        "started_at": "2026-09-01T08:00:00+08:00", "steps": [],
    }), encoding="utf-8")

    health = tmp_path / "source" / "health" / "service-health.sqlite3"
    _sqlite(health, "health_observations")
    board = tmp_path / "source" / "board"
    _sqlite(board / "board.sqlite3", "day_meta")
    (board / "status.json").write_text('{"state":"closed"}\n', encoding="utf-8")
    _sqlite(board / "raw" / "20261120.sqlite3", "raw")

    alerts = tmp_path / "source" / "alerts"
    alerts.mkdir()
    (alerts / "2026-11-20.events.jsonl").write_text(
        '{"schema_version":1,"event_id":"e1"}\n', encoding="utf-8",
    )
    (alerts / "2026-09-01.events.jsonl").write_text(
        '{"schema_version":1,"event_id":"old"}\n', encoding="utf-8",
    )
    return {"service_runs": runs, "health_state": health, "board_root": board,
            "alert_roots": [("production", alerts)]}


def _create(tmp_path: Path, **overrides):
    args = _sources(tmp_path)
    args.update(overrides)
    return create_ops_snapshot(
        tmp_path / "store", **args, source_revision="a" * 40,
        captured_at=CAPTURED, snapshot_id="snapshot-one", publish_current=True,
    )


def test_create_filters_history_backs_up_sqlite_and_publishes(tmp_path: Path):
    manifest = _create(tmp_path)
    root = tmp_path / "store" / "snapshots" / "snapshot-one"

    assert (tmp_path / "store" / "current").resolve() == root
    assert manifest["counts"] == {"service_runs": 1, "alerts": {"production": 1}}
    assert manifest["excluded"] == [
        "live", "orders", "fills", "reconciliation", "paper", "board/raw",
    ]
    assert (root / "service-runs/service-checks/2026/11/20/recent.json").is_file()
    assert not (root / "service-runs/2026/09/01/old.json").exists()
    assert not (root / "limit-up-board/raw").exists()
    assert verify_ops_snapshot(root) == []
    with sqlite3.connect(root / "service-health.sqlite3") as con:
        assert con.execute("SELECT value FROM health_observations").fetchone() == ("observed",)
    with sqlite3.connect(root / "limit-up-board/board.sqlite3") as con:
        assert con.execute("SELECT value FROM day_meta").fetchone() == ("observed",)
    assert os.stat(root / "manifest.json").st_mode & 0o222 == 0


def test_failed_build_keeps_published_current(tmp_path: Path):
    _create(tmp_path)
    current = (tmp_path / "store" / "current").resolve()
    broken = _sources(tmp_path / "broken")
    Path(broken["health_state"]).unlink()

    with pytest.raises(ValueError, match="SQLite source"):
        create_ops_snapshot(
            tmp_path / "store", **broken, source_revision="b" * 40,
            captured_at=CAPTURED, snapshot_id="snapshot-two", publish_current=True,
        )
    assert (tmp_path / "store" / "current").resolve() == current


def test_verifier_detects_mutation_and_publish_rejects_it(tmp_path: Path):
    _create(tmp_path)
    root = tmp_path / "store" / "snapshots" / "snapshot-one"
    path = root / "alerts/production/2026-11-20.events.jsonl"
    path.chmod(0o644)
    path.write_text('{"changed":true}\n', encoding="utf-8")

    assert "file inventory mismatch" in verify_ops_snapshot(root)
    with pytest.raises(ValueError, match="cannot publish invalid snapshot"):
        publish_ops_snapshot(tmp_path / "store", "snapshot-one")


def test_verifier_rejects_manifest_identity_or_exclusion_tampering(tmp_path: Path):
    _create(tmp_path)
    root = tmp_path / "store" / "snapshots" / "snapshot-one"
    manifest_path = root / "manifest.json"
    manifest_path.chmod(0o644)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["snapshot_id"] = "another"
    manifest["excluded"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    failures = verify_ops_snapshot(root)
    assert any("snapshot_id does not match" in failure for failure in failures)


def test_rejects_source_symlink_and_invalid_alert_line(tmp_path: Path):
    sources = _sources(tmp_path)
    runs = Path(sources["service_runs"])
    (runs / "escape.json").symlink_to(runs / "2026/09/01/old.json")
    with pytest.raises(ValueError, match="contains a symlink"):
        create_ops_snapshot(
            tmp_path / "store", **sources, source_revision="a" * 40,
            captured_at=CAPTURED,
        )

    sources = _sources(tmp_path / "invalid")
    alerts = sources["alert_roots"][0][1]
    (alerts / "2026-11-20.events.jsonl").write_text("{truncated\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        create_ops_snapshot(
            tmp_path / "other-store", **sources, source_revision="a" * 40,
            captured_at=CAPTURED,
        )


def test_rejects_unexpected_service_run_file_and_unsafe_alert_name(tmp_path: Path):
    sources = _sources(tmp_path)
    (Path(sources["service_runs"]) / "note.txt").write_text("not managed", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected ServiceRun file"):
        create_ops_snapshot(
            tmp_path / "store", **sources, source_revision="a" * 40,
            captured_at=CAPTURED,
        )

    sources = _sources(tmp_path / "unsafe")
    sources["alert_roots"] = [("../production", sources["alert_roots"][0][1])]
    with pytest.raises(ValueError, match="unsafe source name"):
        create_ops_snapshot(
            tmp_path / "other-store", **sources, source_revision="a" * 40,
            captured_at=CAPTURED,
        )


def test_rejects_symlinked_source_root_and_publish_target(tmp_path: Path):
    sources = _sources(tmp_path)
    linked = tmp_path / "linked-runs"
    linked.symlink_to(sources["service_runs"], target_is_directory=True)
    sources["service_runs"] = linked
    with pytest.raises(ValueError, match="source is a symlink"):
        create_ops_snapshot(
            tmp_path / "store", **sources, source_revision="a" * 40,
            captured_at=CAPTURED,
        )

    sources = _sources(tmp_path / "valid")
    create_ops_snapshot(
        tmp_path / "store", **sources, source_revision="a" * 40,
        captured_at=CAPTURED, snapshot_id="real",
    )
    (tmp_path / "store/snapshots/alias").symlink_to("real", target_is_directory=True)
    with pytest.raises(ValueError, match="must not be a symlink"):
        publish_ops_snapshot(tmp_path / "store", "alias")
