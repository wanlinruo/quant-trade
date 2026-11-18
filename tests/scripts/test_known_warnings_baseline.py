"""已知警告基线候选生成工具（#662 计划 04）：只读、只取非阻断未通过且非上游问题的结果。"""

from __future__ import annotations

import hashlib
from datetime import date, datetime

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.catalog.known_warnings import KnownWarning, sample_fingerprint
from scripts.known_warnings_baseline import main


def check(run, at, cid, metric, passed=False, severity="warning", known="", violations=3, sample=None):
    return {"run_id": run, "checked_at": at, "metric_id": metric, "check_id": cid, "category": "x",
            "scope_start": date(2026, 11, 1), "scope_end": date(2026, 11, 2), "passed": passed,
            "severity": severity, "known_issue": known, "violations": violations, "checked_rows": 100,
            "detail": "d", "sample_path": sample}


def setup(tmp_path):
    wh, samples = tmp_path / "wh", tmp_path / "samples"
    (wh / "metric_checks" / "year=2026").mkdir(parents=True)
    (samples / "r2").mkdir(parents=True)
    pl.DataFrame({"symbol": ["600000.SH"]}).write_parquet(samples / "r2" / "c.gap.parquet")
    rows = [check("r1", datetime(2026, 11, 1, 20), "c.gap", "a.x", violations=1),
            check("r2", datetime(2026, 11, 2, 20), "c.gap", "a.x", sample="artifacts/metric_checks/r2/c.gap.parquet"),
            check("r2", datetime(2026, 11, 2, 20), "c.ok", "a.y", passed=True, violations=0),
            check("r2", datetime(2026, 11, 2, 20), "c.block", "a.z", severity="blocking"),
            check("r2", datetime(2026, 11, 2, 20), "c.up", "a.w", known="上游数据自相矛盾")]
    pq.write_table(pa.Table.from_pylist(rows, schema=schema.METRIC_CHECKS), wh / "metric_checks" / "year=2026" / "part.parquet")
    return wh, samples


def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def test_baseline_tool_prints_entries_for_latest_run(tmp_path, capsys):
    wh, samples = setup(tmp_path)
    assert main(["--warehouse", str(wh), "--sample-root", str(samples), "--issue", "#594",
                 "--registered-on", "2026-11-18"]) == 0
    out = capsys.readouterr().out
    entries = eval(out, {"KnownWarning": KnownWarning, "date": date})  # 输出必须是可粘贴的字面量
    assert entries == (KnownWarning(
        check_id="c.gap", metric_id="a.x", issue="#594", baseline_violations=3,
        baseline_sample_sha256=sample_fingerprint(samples / "r2" / "c.gap.parquet"),
        registered_on=date(2026, 11, 18), note="r2 生成，待评审"),)


def test_baseline_tool_skips_upstream_and_blocking(tmp_path, capsys):
    wh, samples = setup(tmp_path)
    main(["--warehouse", str(wh), "--sample-root", str(samples), "--issue", "#594", "--run-id", "r1",
          "--registered-on", "2026-11-18"])
    entries = eval(capsys.readouterr().out, {"KnownWarning": KnownWarning, "date": date})
    assert [(e.check_id, e.baseline_violations, e.baseline_sample_sha256) for e in entries] == [("c.gap", 1, None)]


def test_baseline_tool_writes_nothing(tmp_path, capsys):
    wh, samples = setup(tmp_path)
    before = tree(tmp_path)
    main(["--warehouse", str(wh), "--sample-root", str(samples), "--issue", "#594", "--registered-on", "2026-11-18"])
    assert tree(tmp_path) == before
