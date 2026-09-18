"""每日指标验证：当日增量 + 当年至今全量，一次写入，摘要可读。"""

from datetime import date

import polars as pl

from qsys.data import schema
from qsys.data.catalog import checks as C
from qsys.data.catalog.verification import verify_session
from qsys.data.query import WarehouseQuery

DAY = date(2026, 9, 17)


def _daily(warehouse, rows):
    frame = pl.DataFrame(rows)
    frame = frame.with_columns([
        pl.lit(None, dtype=pl.Utf8 if n == "name" else pl.Float64).alias(n)
        for n in schema.TUSHARE_DAILY.names if n not in frame.columns
    ])
    warehouse.write("tushare_daily",
                    frame.select(schema.TUSHARE_DAILY.names).to_arrow()
                    .cast(schema.TUSHARE_DAILY), year=2026)


def test_verify_session_covers_both_scopes_in_one_run(warehouse, tmp_path):
    _daily(warehouse, [{"symbol": "600036.SH", "dt": DAY, "close": 10.0,
                        "pre_close": 10.0, "factor": 1.0}])
    summary = verify_session(warehouse, DAY, artifacts=tmp_path)

    written = WarehouseQuery(warehouse).metric_checks()
    assert written.height == summary.rows > 0
    assert written["run_id"].n_unique() == 1, "两段范围必须写成同一次运行"
    scopes = {check.id: check.scope for check in C.CHECKS.values()}
    ran = {scopes[cid] for cid in written["check_id"].unique()}
    assert ran == {C.Scope.INCREMENTAL, C.Scope.FULL}
    # 全量那段查当年至今，增量只查当天；两段的 scope_end 都是当天
    assert set(written["scope_end"].unique()) == {DAY}
    assert set(written["scope_start"].unique()) == {DAY, date(DAY.year, 1, 1)}
    assert summary.blocking == written.filter(
        ~pl.col("passed") & (pl.col("severity") == "blocking")).height
    assert summary.warnings == written.filter(
        ~pl.col("passed") & (pl.col("severity") == "warning")).height


def test_summary_names_the_rules_that_did_not_pass(warehouse, tmp_path):
    # 价格有 11% 缺口而因子不跳：A3.missed_exdiv（全量）必然报
    _daily(warehouse, [
        {"symbol": "300091.SZ", "dt": date(2026, 9, 16), "close": 3.16,
         "pre_close": 3.05, "factor": 12.201},
        {"symbol": "300091.SZ", "dt": DAY, "close": 3.1, "pre_close": 2.79,
         "factor": 12.201},
    ])
    summary = verify_session(warehouse, DAY, artifacts=tmp_path)
    assert "A3.missed_exdiv" in summary.failed_checks
