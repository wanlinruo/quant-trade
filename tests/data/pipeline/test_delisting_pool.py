"""退市整理期池：只派生自 namechange；覆盖范围单独记录，区分「没有」和「没建」。"""

from datetime import date

import pyarrow as pa

from qsys.data import schema
from qsys.data.pipeline.delisting import build_delisting_pool
from qsys.data.query import WarehouseQuery

D1, D2, D3 = date(2026, 3, 30), date(2026, 3, 31), date(2026, 4, 1)


def history(warehouse, rows):
    warehouse.write("name_history", pa.table({
        "symbol": [r[0] for r in rows], "name": [r[1] for r in rows],
        "start_date": [r[2] for r in rows], "end_date": [r[3] for r in rows],
        "ann_date": [r[4] for r in rows], "change_reason": [r[5] for r in rows],
    }, schema=schema.NAME_HISTORY))


def test_pool_rows_follow_the_namechange_period_only(warehouse, write_daily_bars):
    import polars as pl

    history(warehouse, [
        ("300344.SZ", "立方退", D2, None, D1, "退市整理期"),
        ("300344.SZ", "*ST立方", date(2025, 5, 1), D1, date(2025, 4, 30), "*ST"),
        ("600001.SH", "某某退", date(2026, 1, 1), D1, date(2025, 12, 30), "退市整理期"),
    ])
    write_daily_bars(warehouse, pl.DataFrame({"symbol": ["000001.SZ"] * 2, "dt": [D1, D2]}))
    rows = build_delisting_pool(warehouse, D1, D2, extra_sessions=[D3])
    pool = WarehouseQuery(warehouse).delisting_pool(D1, D3)
    assert rows == pool.height
    assert pool.sort("dt", "symbol").select("dt", "symbol").rows() == [
        (D1, "600001.SH"), (D2, "300344.SZ"), (D3, "300344.SZ")]
    assert WarehouseQuery(warehouse).delisting_pool_coverage(2026) == (D1, D3)


def test_rebuild_keeps_other_days_and_widens_coverage(warehouse):
    history(warehouse, [("300344.SZ", "立方退", D1, None, None, "退市整理期")])
    build_delisting_pool(warehouse, D1, D1, extra_sessions=[D1])
    build_delisting_pool(warehouse, D3, D3, extra_sessions=[D3])
    q = WarehouseQuery(warehouse)
    assert sorted(q.delisting_pool(D1, D3)["dt"].to_list()) == [D1, D3]
    assert q.delisting_pool_coverage(2026) == (D1, D3)


def test_no_sessions_writes_nothing(warehouse):
    assert build_delisting_pool(warehouse, D1, D2) == 0
    assert WarehouseQuery(warehouse).delisting_pool_coverage(2026) is None
