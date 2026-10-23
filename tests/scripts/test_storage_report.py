import json
import os
from datetime import datetime
from pathlib import Path

from qsys.ops import storage_report_cli as report


NOW = datetime(2026, 10, 13, 18, 20, 0)


def test_packaged_cli_and_legacy_wrapper_share_the_same_entrypoint(tmp_path):
    args = report._parser().parse_args([
        "--base", str(tmp_path / "data"),
        "--scratch", str(tmp_path / "scratch"),
        "--history-root", str(tmp_path / "history"),
    ])
    wrapper = Path("scripts/storage_report.py").read_text(encoding="utf-8")

    assert args.history_root == tmp_path / "history"
    assert "from qsys.ops.storage_report_cli import main" in wrapper
    assert "build_payload" not in wrapper


def _write(path, size=4096):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_classified_usage_deduplicates_hardlinks_and_prefers_warehouse(tmp_path):
    base = tmp_path / "qsys"
    source = _write(base / "warehouse/minute_bars/part.parquet", 64 * 1024)
    snapshot = base / "test-env/warehouse-snapshots/d1/part.parquet"
    snapshot.parent.mkdir(parents=True)
    os.link(source, snapshot)
    _write(base / "test-env/var/state.sqlite3", 8 * 1024)

    usage = {item["category"]: item for item in report.classified_usage(base)}

    assert usage["生产仓库"]["bytes"] >= 64 * 1024
    assert usage["测试环境"]["bytes"] < usage["生产仓库"]["bytes"]
    assert sum(item["unique_inodes"] for item in usage.values()) == 8


def test_scratch_retention_does_not_mix_another_disk_into_categories(tmp_path):
    base = tmp_path / "qsys"
    scratch = tmp_path / "scratch"
    _write(base / "warehouse/bars_daily/part.parquet", 4096)
    _write(scratch / "large-temporary.bin", 128 * 1024)

    payload = report.build_payload(base, scratch, NOW)

    assert "临时工作区" not in {item["category"] for item in payload["categories"]}
    assert str(scratch / "large-temporary.bin") in {
        item["path"] for item in payload["items"]
    }


def test_history_snapshot_is_atomic_and_partitioned_by_day(tmp_path, monkeypatch):
    target = report.history_path(tmp_path / "history", NOW)
    payload = {"schema_version": 1, "generated_at": NOW.isoformat()}

    report.write_json_atomic(target, payload)

    assert json.loads(target.read_text()) == payload
    assert target.parent == tmp_path / "history/2026/10/13"
    assert not list(target.parent.glob("*.tmp"))


def test_payload_exposes_capacity_thresholds_and_categories(tmp_path, monkeypatch):
    base = tmp_path / "qsys"
    _write(base / "warehouse/bars_daily/part.parquet")
    fake = report.disk_status(base)
    monkeypatch.setattr(
        report,
        "disk_status",
        lambda _path: type(fake)(100 * report.GIB, 86 * report.GIB, 14 * report.GIB),
    )

    payload = report.build_payload(base, None, NOW)

    assert payload["disk"]["expansion_review_required"] is True
    assert payload["thresholds"]["expansion_review_free_bytes"] == 15 * report.GIB
    assert payload["categories"][0]["category"] == "生产仓库"


def test_snapshot_separates_system_disk_and_preserves_old_data_fields(tmp_path, monkeypatch):
    from qsys.ops.storage import DiskStatus
    base = tmp_path / "data"
    base.mkdir()
    def probe(path):
        free = 8 if path == Path("/") else 30
        return DiskStatus(120 * report.GIB, (120 - free) * report.GIB, free * report.GIB)
    monkeypatch.setattr(report, "disk_status", probe)
    payload = report.build_payload(base, None, NOW)
    assert payload["schema_version"] == 1
    assert payload["disk"]["free"] == 30 * report.GIB
    assert payload["disk"]["level"] == "ok"
    assert payload["system_disk"]["free"] == 8 * report.GIB
    assert payload["system_disk"]["level"] == "fail"
    assert payload["system_disk"]["expansion_review_required"] is True
    def root_unavailable(path):
        if path == Path("/"):
            raise OSError("unavailable")
        return probe(path)
    monkeypatch.setattr(report, "disk_status", root_unavailable)
    payload = report.build_payload(base, None, NOW)
    assert payload["system_disk"] is None
    assert payload["disk"]["free"] == 30 * report.GIB
