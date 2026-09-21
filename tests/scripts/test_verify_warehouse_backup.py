from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq

from scripts.verify_warehouse_backup import create_manifest, verify_manifest


def _write(path, value=1.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"dt": [date(2026, 9, 18)], "value": [value]}), path)


def test_manifest_verifies_an_isolated_copy(tmp_path):
    source = tmp_path / "source"
    restored = tmp_path / "restored"
    relative = "bars/year=2026/part.parquet"
    _write(source / relative)
    _write(restored / relative)

    manifest = create_manifest(source)

    assert manifest["files"][0]["rows"] == 1
    assert verify_manifest(restored, manifest) == []


def test_manifest_detects_changed_bytes(tmp_path):
    source = tmp_path / "source"
    restored = tmp_path / "restored"
    relative = "bars/year=2026/part.parquet"
    _write(source / relative)
    manifest = create_manifest(source)
    _write(restored / relative, 2.0)

    failures = verify_manifest(restored, manifest)

    assert failures == [f"{relative}: sha256 mismatch"]


def test_manifest_rejects_path_traversal(tmp_path):
    manifest = {
        "format": "qsys-warehouse-backup-manifest",
        "version": 1,
        "files": [{"path": "../secret", "bytes": 1}],
    }
    assert "unsafe manifest path" in verify_manifest(tmp_path, manifest)[0]


def test_manifest_detects_unexpected_parquet(tmp_path):
    root = tmp_path / "restored"
    root.mkdir()
    _write(root / "extra.parquet")
    manifest = {
        "format": "qsys-warehouse-backup-manifest",
        "version": 1,
        "files": [],
    }
    assert verify_manifest(root, manifest) == ["extra.parquet: unexpected file"]


def test_manifest_includes_non_parquet_metadata(tmp_path):
    source = tmp_path / "source"
    restored = tmp_path / "restored"
    source.mkdir()
    restored.mkdir()
    (source / "manifest.json").write_text('{"complete": true}\n')
    (restored / "manifest.json").write_text('{"complete": true}\n')

    manifest = create_manifest(source)

    assert manifest["files"][0]["path"] == "manifest.json"
    assert "rows" not in manifest["files"][0]
    assert verify_manifest(restored, manifest) == []
