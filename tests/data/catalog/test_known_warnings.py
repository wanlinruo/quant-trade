"""已知警告受管清单与判定（#662 计划 04；设计文档 §7.2）。"""

from __future__ import annotations

import re
from datetime import date

import polars as pl
import pytest

from qsys.data.catalog import REGISTRY
from qsys.data.catalog.checks import CHECKS
from qsys.data.catalog.known_warnings import (
    KNOWN_WARNINGS, KnownWarning, judge, lookup, sample_fingerprint,
)

CHECK = next(iter(CHECKS.values()))
METRIC = CHECK.metrics[0]


def row(violations=3, passed=False, severity="warning", known=""):
    return {"metric_id": METRIC, "check_id": CHECK.id, "passed": passed, "severity": severity,
            "known_issue": known, "violations": violations}


def entry(baseline=3, sha="abc", metric=METRIC):
    return KnownWarning(check_id=CHECK.id, metric_id=metric, issue="#594", baseline_violations=baseline,
                        baseline_sample_sha256=sha, registered_on=date(2026, 11, 18), note="历史缺口")


def test_passed_and_blocking():
    assert judge(row(passed=True, violations=0), None).kind == "passed"
    assert judge(row(severity="blocking"), None).kind == "failed"


def test_blocking_never_known():
    v = judge(row(severity="blocking"), "abc", entries=(entry(),))
    assert v.kind == "failed" and v.issue is None


def test_upstream_self_contradiction():
    v = judge(row(known="上游数据自相矛盾"), None)
    assert (v.kind, v.issue) == ("upstream", "#653")


def test_unregistered_warning_is_new():
    v = judge(row(), "abc", entries=())
    assert v.kind == "new" and v.issue is None and v.over_baseline is None and "未登记" in v.reason


def test_within_baseline_same_sample_is_known():
    v = judge(row(violations=3), "abc", entries=(entry(),))
    assert (v.kind, v.issue, v.over_baseline) == ("known", "#594", 0)
    assert judge(row(violations=2), "abc", entries=(entry(),)).kind == "known"


def test_over_baseline_is_new_with_delta():
    v = judge(row(violations=4), "abc", entries=(entry(),))
    assert (v.kind, v.issue, v.over_baseline) == ("new", "#594", 1) and "比基线多 1" in v.reason


def test_same_count_new_sample_is_new():
    v = judge(row(violations=3), "zzz", entries=(entry(),))
    assert v.kind == "new" and "样本" in v.reason


def test_baseline_sample_but_current_missing_is_new():
    v = judge(row(violations=3), None, entries=(entry(),))
    assert v.kind == "new" and "无法证明样本未变" in v.reason


def test_baseline_without_fingerprint_compares_count_only():
    assert judge(row(violations=3), None, entries=(entry(sha=None),)).kind == "known"
    assert judge(row(violations=3), "other", entries=(entry(sha=None),)).kind == "known"
    assert judge(row(violations=5), None, entries=(entry(sha=None),)).over_baseline == 2


def test_fingerprint_ignores_row_and_column_order(tmp_path):
    a, b, c = tmp_path / "a.parquet", tmp_path / "b.parquet", tmp_path / "c.parquet"
    pl.DataFrame({"symbol": ["600000.SH", "000001.SZ"], "v": [1.0, None]}).write_parquet(a)
    pl.DataFrame({"v": [None, 1.0], "symbol": ["000001.SZ", "600000.SH"]}).write_parquet(b)
    pl.DataFrame({"symbol": ["600000.SH", "000002.SZ"], "v": [1.0, None]}).write_parquet(c)
    assert sample_fingerprint(a) == sample_fingerprint(b) and len(sample_fingerprint(a)) == 64
    assert sample_fingerprint(a) != sample_fingerprint(c)
    assert sample_fingerprint(None) is None and sample_fingerprint(tmp_path / "missing.parquet") is None


def test_lookup_exact_then_wildcard():
    exact, wild = entry(metric=METRIC), entry(metric=None, baseline=9)
    assert lookup(CHECK.id, METRIC, entries=(wild, exact)) is exact
    assert lookup(CHECK.id, "other.metric", entries=(wild, exact)) is wild
    assert lookup("nope", METRIC, entries=(wild, exact)) is None


def test_registry_entries_valid():
    for e in KNOWN_WARNINGS:
        assert e.check_id in CHECKS and (e.metric_id is None or e.metric_id in REGISTRY)
        assert re.fullmatch(r"#\d+", e.issue) and e.baseline_violations >= 0 and e.note
        assert e.baseline_sample_sha256 is None or re.fullmatch(r"[0-9a-f]{64}", e.baseline_sample_sha256)
    keys = [(e.check_id, e.metric_id) for e in KNOWN_WARNINGS]
    assert len(keys) == len(set(keys))
