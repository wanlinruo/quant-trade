from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from qsys.data.snapshots import (
    create_snapshot,
    current_snapshot,
    publish_current_snapshot,
    verify_snapshot,
)


CREATED = datetime(2026, 10, 14, 5, 30, tzinfo=timezone.utc)


def _warehouse(root: Path) -> Path:
    warehouse = root / "warehouse"
    path = warehouse / "bars" / "year=2026" / "part.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.table({"symbol": ["000001.SZ"], "close": [10.5]}), path)
    (warehouse / "catalog.json").write_text('{"version":1}\n', encoding="utf-8")
    return warehouse


def test_create_verify_and_publish_current(tmp_path: Path):
    warehouse = _warehouse(tmp_path)
    store = tmp_path / "snapshot-store"

    snapshot = create_snapshot(
        warehouse,
        store,
        complete_through=date(2026, 10, 13),
        schema_version="qsys-data-v1",
        snapshot_id="20261013-close",
        created_at=CREATED,
        source_revision="a" * 40,
        publish_current=True,
    )

    assert snapshot.snapshot_id == "20261013-close"
    assert snapshot.manifest["complete_through"] == "2026-10-13"
    assert snapshot.manifest["partition_count"] == 2
    assert len(snapshot.manifest["content_sha256"]) == 64
    assert verify_snapshot(snapshot.root) == []
    assert (store / "current").is_symlink()
    assert current_snapshot(store).snapshot_id == snapshot.snapshot_id
    assert os.stat(warehouse / "catalog.json").st_ino == os.stat(
        snapshot.warehouse / "catalog.json"
    ).st_ino


def test_atomic_source_replacement_does_not_change_snapshot(tmp_path: Path):
    warehouse = _warehouse(tmp_path)
    snapshot = create_snapshot(
        warehouse,
        tmp_path / "store",
        complete_through="2026-10-13",
        schema_version="v1",
        created_at=CREATED,
    )
    original = snapshot.manifest["content_sha256"]

    source = warehouse / "catalog.json"
    replacement = warehouse / ".catalog.json.new"
    replacement.write_text('{"version":2}\n', encoding="utf-8")
    os.replace(replacement, source)

    assert verify_snapshot(snapshot.root) == []
    assert snapshot.manifest["content_sha256"] == original
    assert json.loads((snapshot.warehouse / "catalog.json").read_text())["version"] == 1


def test_verification_detects_in_place_mutation_and_extra_file(tmp_path: Path):
    snapshot = create_snapshot(
        _warehouse(tmp_path),
        tmp_path / "store",
        complete_through="2026-10-13",
        schema_version="v1",
        created_at=CREATED,
    )
    (snapshot.warehouse / "catalog.json").write_text("changed\n", encoding="utf-8")
    (snapshot.warehouse / "unexpected.txt").write_text("extra\n", encoding="utf-8")

    failures = verify_snapshot(snapshot.root)
    assert any("catalog.json" in failure and "bytes" in failure for failure in failures)
    assert "unexpected.txt: unexpected file" in failures
    assert "content_sha256 mismatch" in failures


def test_failed_snapshot_does_not_move_current(tmp_path: Path):
    warehouse = _warehouse(tmp_path)
    store = tmp_path / "store"
    first = create_snapshot(
        warehouse,
        store,
        complete_through="2026-10-13",
        schema_version="v1",
        snapshot_id="good",
        created_at=CREATED,
        publish_current=True,
    )
    with pytest.raises(FileExistsError):
        create_snapshot(
            warehouse,
            store,
            complete_through="2026-10-13",
            schema_version="v1",
            snapshot_id="good",
            created_at=CREATED,
            publish_current=True,
        )
    assert current_snapshot(store).snapshot_id == first.snapshot_id


def test_publish_rejects_manifest_directory_mismatch(tmp_path: Path):
    store = tmp_path / "store"
    snapshot = create_snapshot(
        _warehouse(tmp_path),
        store,
        complete_through="2026-10-13",
        schema_version="v1",
        snapshot_id="one",
        created_at=CREATED,
    )
    manifest_path = snapshot.root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["snapshot_id"] = "other"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="directory and manifest"):
        publish_current_snapshot(store, "one")


def test_rejects_unsafe_ids_and_source_symlinks(tmp_path: Path):
    warehouse = _warehouse(tmp_path)
    with pytest.raises(ValueError, match="invalid snapshot_id"):
        create_snapshot(
            warehouse,
            tmp_path / "store",
            complete_through="2026-10-13",
            schema_version="v1",
            snapshot_id="../escape",
            created_at=CREATED,
        )

    (warehouse / "linked").symlink_to(warehouse / "catalog.json")
    with pytest.raises(ValueError, match="contains a symlink"):
        create_snapshot(
            warehouse,
            tmp_path / "other-store",
            complete_through="2026-10-13",
            schema_version="v1",
            created_at=CREATED,
        )


def test_rejects_empty_warehouse(tmp_path: Path):
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    with pytest.raises(ValueError, match="does not contain any files"):
        create_snapshot(
            warehouse,
            tmp_path / "store",
            complete_through="2026-10-13",
            schema_version="v1",
            created_at=CREATED,
            publish_current=True,
        )
    assert not (tmp_path / "store" / "current").exists()
