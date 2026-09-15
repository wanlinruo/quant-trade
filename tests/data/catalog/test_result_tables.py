"""验证结果表。自动结果可重跑，人工证据永久保留。"""

from datetime import date, datetime

import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

D = date(2026, 9, 15)
T = datetime(2026, 9, 15, 19, 30)


def _checks_table(rows: list[dict]) -> pa.Table:
    return pl.DataFrame(rows).to_arrow().cast(schema.METRIC_CHECKS)


def _row(**over) -> dict:
    base = dict(
        run_id="r1", checked_at=T, metric_id="tushare_daily.amount",
        check_id="A1.dimension.amount_vs_volume_close", category="truthfulness",
        scope_start=D, scope_end=D, passed=True, severity="warning",
        known_issue=None, violations=0, checked_rows=5550,
        detail="", sample_path=None,
    )
    base.update(over)
    return base


def test_both_tables_are_registered_as_partitioned():
    assert "metric_checks" in schema.PARTITIONED
    assert "metric_evidence" in schema.PARTITIONED


def test_checked_rows_round_trips(warehouse: Warehouse):
    warehouse.write("metric_checks", _checks_table([_row()]), year=2026)
    got = WarehouseQuery(warehouse).metric_checks()
    assert got["checked_rows"][0] == 5550


def test_reads_back_by_both_ids(warehouse: Warehouse):
    warehouse.write("metric_checks", _checks_table([
        _row(),
        _row(metric_id="daily_flags.is_st", check_id="A2.recompute.is_st"),
    ]), year=2026)
    query = WarehouseQuery(warehouse)
    assert query.metric_checks(metric_id="daily_flags.is_st").height == 1
    assert query.metric_checks(check_id="A2.recompute.is_st").height == 1


def test_latest_keeps_only_the_newest_row_per_pair(warehouse: Warehouse):
    old = _row(checked_at=datetime(2026, 9, 14, 19), violations=7, passed=False)
    new = _row(run_id="r2", violations=0, passed=True)
    warehouse.write("metric_checks", _checks_table([old, new]), year=2026)
    latest = WarehouseQuery(warehouse).latest_metric_checks()
    assert latest.height == 1
    assert latest["run_id"][0] == "r2"


def test_empty_warehouse_returns_empty_frames(warehouse: Warehouse):
    query = WarehouseQuery(warehouse)
    assert query.metric_checks().schema == pl.from_arrow(schema.METRIC_CHECKS.empty_table()).schema
    assert query.metric_evidence().schema == pl.from_arrow(schema.METRIC_EVIDENCE.empty_table()).schema


def test_evidence_keeps_values_as_text(warehouse: Warehouse):
    table = pl.DataFrame([{
        "metric_id": "adj_factors.cum_factor", "symbol": "001331.SZ", "dt": D,
        "our_value": "3.4848", "reference_value": "2.4011",
        "reference_source": "同花顺", "verdict": "mismatch",
        "checked_by": "wanlinruo", "checked_at": T, "note": "D-1 抽查",
    }]).to_arrow().cast(schema.METRIC_EVIDENCE)
    warehouse.write("metric_evidence", table, year=2026)
    got = WarehouseQuery(warehouse).metric_evidence("adj_factors.cum_factor")
    assert got["our_value"][0] == "3.4848"
    assert got["verdict"][0] == "mismatch"


def test_evidence_and_checks_are_separate_tables():
    assert schema.METRIC_CHECKS.names != schema.METRIC_EVIDENCE.names
