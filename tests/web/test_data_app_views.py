"""数据组的纯派生：质检结构、警告归类、血缘图、表样本（#662，设计文档 §8.13）。"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.catalog import DATASETS, DEPENDENCIES
from qsys.web.data_app_views import lineage_graph, quality_runs, table_sample, warning_sources


def mc(run, at, passed, severity="blocking", known="", metric="m1", check="c1", detail="d"):
    return {"run_id": run, "checked_at": at, "metric_id": metric, "check_id": check, "category": "x",
            "scope_start": None, "scope_end": None, "passed": passed, "severity": severity,
            "known_issue": known, "violations": 0 if passed else 1, "checked_rows": 10,
            "detail": detail, "sample_path": None}


def dc(run, status):
    return {"run_id": run, "checked_at": datetime(2026, 11, 1), "dataset_id": "d", "check_id": "T1",
            "dimension": "structure", "scope_start": None, "scope_end": None, "status": status,
            "evidence_at": None, "expires_at": None, "required": True, "violations": 0,
            "checked_rows": 0, "detail": "", "input_version": ""}


def frame(rows, sch):
    return pl.DataFrame(rows, schema=pl.from_arrow(sch.empty_table()).schema) if rows else pl.from_arrow(sch.empty_table())


T1, T2 = datetime(2026, 11, 1, 20), datetime(2026, 11, 2, 20)


def test_quality_runs_counts_and_order():
    m = frame([mc("r1", T1, True), mc("r1", T1, False), mc("r2", T2, True), mc("r2", T2, False, "warning"),
               mc("r2", T2, False, "blocking")], schema.METRIC_CHECKS)
    d = frame([dc("r2", "red"), dc("r2", "green"), dc("r2", "gray")], schema.DATASET_CHECKS)
    runs = quality_runs(m, d)
    assert [r["run_id"] for r in runs] == ["r2", "r1"]
    assert runs[0]["fields"] == {"total": 3, "passed": 1, "blocking": 1, "warning": 1}
    assert runs[0]["tables"] == {"red": 1, "yellow": 0, "green": 1, "gray": 1}
    assert runs[0]["checked_at"] == T2


def test_quality_runs_table_counts_null_when_absent():
    m = frame([mc("r1", T1, True)], schema.METRIC_CHECKS)
    assert quality_runs(m, frame([], schema.DATASET_CHECKS))[0]["tables"] is None


def test_quality_runs_limit_30():
    m = frame([mc(f"r{i:02d}", datetime(2026, 1, 1, 0, i), True) for i in range(40)], schema.METRIC_CHECKS)
    runs = quality_runs(m, frame([], schema.DATASET_CHECKS))
    assert len(runs) == 30 and runs[0]["run_id"] == "r39"


def test_quality_runs_empty():
    assert quality_runs(frame([], schema.METRIC_CHECKS), frame([], schema.DATASET_CHECKS)) == []


def test_warning_sources_classification():
    m = frame([
        mc("r1", T1, False, "warning", "上游数据自相矛盾"),
        mc("r2", T2, False, "warning", "上游数据自相矛盾", metric="a"),
        mc("r2", T2, False, "warning", "已裁决：停牌日无成交", metric="b"),
        mc("r2", T2, False, "warning", "", metric="c"),
        mc("r2", T2, False, "warning", "", metric="d"),
        mc("r2", T2, False, "blocking", "", metric="e"),
        mc("r2", T2, True, "warning", "", metric="f"),
    ], schema.METRIC_CHECKS)
    got = {g["key"]: g for g in warning_sources(m)}
    assert (got["upstream"]["count"], got["upstream"]["issue"]) == (1, "#653")
    assert (got["registered"]["count"], got["registered"]["issue"]) == (1, "#594")
    assert (got["unclassified"]["count"], got["unclassified"]["issue"]) == (2, None)
    assert [s["metric_id"] for s in got["unclassified"]["samples"]] == ["c", "d"]


def test_lineage_graph_has_sources_datasets_consumers_and_edges():
    g = lineage_graph()
    ids = [n["id"] for n in g["nodes"]]
    assert len(ids) == len(set(ids))
    for ds in DATASETS.values():
        assert f"dataset:{ds.id}" in ids
    edges = {(e["source"], e["target"]) for e in g["edges"]}
    for dep in DEPENDENCIES:
        assert (f"dataset:{dep.upstream}", f"dataset:{dep.downstream}") in edges
    kinds = {n["kind"] for n in g["nodes"]}
    assert "source" in kinds and "consumer" in kinds
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])


def _physical_dataset():
    return next(d for d in DATASETS.values() if d.physical)


def test_sample_reads_last_row_group_limit(tmp_path):
    ds = replace(_physical_dataset(), id="demo_table", partition_keys=("year",))
    for year in (2025, 2026):
        part = tmp_path / "demo_table" / f"year={year}"
        part.mkdir(parents=True)
        table = pa.table({"v": list(range(year * 100, year * 100 + 90)), "dt": [date(year, 1, 2)] * 90})
        pq.write_table(table, part / "part.parquet", row_group_size=30)
    s = table_sample(tmp_path, ds)
    assert s["available"] is True and s["columns"] == ["v", "dt"]
    assert s["file"].endswith("year=2026/part.parquet") and s["row_group"] == 2
    assert len(s["rows"]) == 20 and s["rows"][0] == {"v": 202660, "dt": "2026-01-02"}


def test_sample_keeps_nulls(tmp_path):
    ds = replace(_physical_dataset(), id="demo_flat", partition_keys=())
    (tmp_path / "demo_flat").mkdir()
    pq.write_table(pa.table({"a": [1, None], "b": [None, "x"]}), tmp_path / "demo_flat" / "part.parquet")
    s = table_sample(tmp_path, ds)
    assert s["rows"] == [{"a": 1, "b": None}, {"a": None, "b": "x"}]


def test_sample_unavailable_for_logical_or_missing(tmp_path):
    logical = next((d for d in DATASETS.values() if not d.physical), None)
    if logical is not None:
        s = table_sample(tmp_path, logical)
        assert s["available"] is False and s["message"]
    missing = table_sample(tmp_path, replace(_physical_dataset(), id="nope"))
    assert missing["available"] is False and "没有" in missing["message"]
