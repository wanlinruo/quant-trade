"""规则 runner 的失败留痕、分母和降级纪律。"""

from datetime import date, datetime

import polars as pl
import pytest

from qsys.data.catalog import checks as C
from qsys.data.query import WarehouseQuery

D0, D1 = date(2026, 9, 1), date(2026, 9, 15)
NOW = datetime(2026, 9, 15, 19, 30)


@pytest.fixture
def ctx(warehouse):
    return C.CheckContext(WarehouseQuery(warehouse), warehouse, D0, D1)


def _check(cid, fn, *, metrics=("tushare_daily.close",), known=()):
    return C.Check(cid, C.Category.TRUTHFULNESS, C.Scope.INCREMENTAL,
                   metrics, known, "测试用", fn)


def test_passing_check_emits_one_row_per_metric(ctx):
    check = _check("X.ok", lambda _: C.CheckResult(0, 100),
                   metrics=("tushare_daily.close", "tushare_daily.open"))
    out = C.run_checks(ctx, "r1", NOW, only=[check])
    assert out.height == 2
    assert set(out["metric_id"]) == {"tushare_daily.close", "tushare_daily.open"}
    assert out["passed"].all()


def test_unknown_failure_is_blocking(ctx):
    out = C.run_checks(ctx, "r1", NOW, only=[
        _check("X.bad", lambda _: C.CheckResult(7, 100))
    ])
    assert out["severity"][0] == "blocking"
    assert out["known_issue"][0] is None


def test_registered_defect_is_downgraded(ctx):
    check = _check("X.d1", lambda _: C.CheckResult(688, 1000),
                   metrics=("adj_factors.cum_factor",), known=("D-1",))
    out = C.run_checks(ctx, "r1", NOW, only=[check])
    assert out["severity"][0] == "warning"
    assert out["known_issue"][0] == "D-1"


def test_unregistered_metric_defect_does_not_downgrade(ctx):
    check = _check("X.other", lambda _: C.CheckResult(1, 10), known=("D-1",))
    assert C.run_checks(ctx, "r1", NOW, only=[check])["severity"][0] == "blocking"


def test_crashing_check_writes_failure(ctx):
    def boom(_):
        raise RuntimeError("上游炸了")

    out = C.run_checks(ctx, "r1", NOW, only=[_check("X.boom", boom)])
    assert out["passed"][0] is False
    assert out["checked_rows"][0] == 0
    assert "runner error" in out["detail"][0] and "上游炸了" in out["detail"][0]


def test_zero_denominator_is_not_a_pass(ctx):
    out = C.run_checks(ctx, "r1", NOW, only=[
        _check("X.empty", lambda _: C.CheckResult(0, 0))
    ])
    assert out["passed"][0] is False
    assert "没有可检查的行" in out["detail"][0]


def test_explicitly_not_applicable_check_is_not_a_failure(ctx):
    out = C.run_checks(ctx, "r1", NOW, only=[
        _check("X.na", lambda _: C.CheckResult(0, 0, "当前没有该类事件", applicable=False))
    ])
    assert out["passed"][0] is True
    assert out["checked_rows"][0] == 0
    assert "本次范围不适用" in out["detail"][0]


def test_samples_are_written_outside_result_table(ctx, tmp_path):
    sample = pl.DataFrame({"symbol": ["000430.SZ"], "dt": [D1]})
    check = _check("X.sample", lambda _: C.CheckResult(1, 10, sample=sample))
    out = C.run_checks(ctx, "r1", NOW, only=[check], artifacts=tmp_path)
    assert pl.read_parquet(out["sample_path"][0]).height == 1


def test_results_append_through_warehouse(ctx, warehouse):
    first = C.run_checks(ctx, "r1", NOW, only=[_check("X.ok", lambda _: C.CheckResult(0, 5))])
    second = C.run_checks(ctx, "r2", datetime(2026, 9, 16),
                          only=[_check("X.ok", lambda _: C.CheckResult(1, 5))])
    assert C.write_results(warehouse, first) == 1
    assert C.write_results(warehouse, second) == 1
    assert WarehouseQuery(warehouse).metric_checks().height == 2


def test_register_rejects_duplicate_id():
    cid = "test.duplicate.runner"
    C.CHECKS.pop(cid, None)
    C.register(_check(cid, lambda _: C.CheckResult(0, 1)))
    with pytest.raises(ValueError, match="重复"):
        C.register(_check(cid, lambda _: C.CheckResult(0, 1)))
    C.CHECKS.pop(cid)


def test_register_rejects_unknown_metric():
    with pytest.raises(ValueError, match="未登记"):
        C.register(_check("test.unknown.metric", lambda _: C.CheckResult(0, 1),
                          metrics=("nope.nope",)))
