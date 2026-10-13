import json
import os
from datetime import datetime

from scripts import storage_report as report


NOW = datetime(2026, 10, 13, 18, 20, 0)


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
