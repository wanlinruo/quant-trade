"""换源对账：差异逐键列出，不能只给总数。"""

from datetime import date

import polars as pl
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.quality.tushare_reconcile import reconcile
from qsys.data.query import WarehouseQuery

DAY = date(2026, 9, 11)


def _row(symbol, **over):
    row = {n: None for n in schema.PAID_DAILY.names}
    row |= {"dt": DAY, "symbol": symbol, "name": "平安银行",
            "open": 11.76, "high": 11.79, "low": 11.69, "close": 11.70,
            "pre_close": 11.70, "volume": 582305.98, "amount": 682946782.66,
            "factor": 139.008, "total_shares": 1.94e10}
    row |= over
    return row


def _write(warehouse, dataset, rows, arrow_schema):
    frame = pl.DataFrame(rows)
    path = warehouse.root / dataset / "year=2026" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(frame.to_arrow().cast(arrow_schema), path)


def test_identical_data_reports_zero_differences(warehouse):
    _write(warehouse, "paid_daily", [_row("000001.SZ")], schema.PAID_DAILY)
    _write(warehouse, "tushare_daily", [_row("000001.SZ")], schema.TUSHARE_DAILY)
    rep = reconcile(WarehouseQuery(warehouse), DAY, DAY)
    assert rep.summary["common_rows"] == 1
    assert rep.value_diffs.height == 0


def test_lists_every_differing_field_key_by_key(warehouse):
    _write(warehouse, "paid_daily", [_row("000001.SZ")], schema.PAID_DAILY)
    _write(warehouse, "tushare_daily",
           [_row("000001.SZ", total_shares=1.928e10, close=11.71)],
           schema.TUSHARE_DAILY)
    rep = reconcile(WarehouseQuery(warehouse), DAY, DAY)
    fields = sorted(rep.value_diffs["field"].to_list())
    assert fields == ["close", "total_shares"]


def test_rows_only_in_one_side_are_reported_separately(warehouse):
    """只在一边的标的要单列，不能混进数值差异淹没真问题。"""
    _write(warehouse, "paid_daily", [_row("000001.SZ")], schema.PAID_DAILY)
    _write(warehouse, "tushare_daily",
           [_row("000001.SZ"), _row("600000.SH")], schema.TUSHARE_DAILY)
    rep = reconcile(WarehouseQuery(warehouse), DAY, DAY)
    assert rep.only_in_tushare["symbol"].to_list() == ["600000.SH"]
    assert rep.only_in_paid.height == 0


def test_null_on_both_sides_is_not_a_difference(warehouse):
    """两边都没有这个数，不算差异——记 null 是规矩允许的结果。"""
    _write(warehouse, "paid_daily", [_row("000001.SZ", pe=None)], schema.PAID_DAILY)
    _write(warehouse, "tushare_daily", [_row("000001.SZ", pe=None)],
           schema.TUSHARE_DAILY)
    rep = reconcile(WarehouseQuery(warehouse), DAY, DAY)
    assert rep.value_diffs.filter(pl.col("field") == "pe").height == 0


def _empty_frame(arrow_schema) -> pl.DataFrame:
    return pl.DataFrame(schema={
        n: (pl.Date if n == "dt" else pl.Utf8 if n in ("symbol", "name") else pl.Float64)
        for n in arrow_schema.names
    })


class RecordingQuery:
    """记录每次读取的区间。**守的是实现约束，不是语义。**

    跨年汇总的语义测试在「一次性载入全历史」的实现上同样会通过，所以它们
    守不住「必须逐年」。而逐年正是为了避免 OOM 才改的（2026-09-13 生产节点
    实测 7.24 GiB RSS 被 OOM killer 杀），因此必须单独守一条。
    """

    def __init__(self):
        self.calls: list[tuple[str, object, object]] = []

    def tushare_daily(self, symbols, start, end):
        self.calls.append(("tushare_daily", start, end))
        return _empty_frame(schema.TUSHARE_DAILY)

    def paid_daily(self, symbols, start, end):
        self.calls.append(("paid_daily", start, end))
        return _empty_frame(schema.PAID_DAILY)


def test_every_read_is_confined_to_a_single_year():
    """任何一次读取都不得跨年——跨年就意味着可能一次载入全历史。"""
    import datetime as _dt

    q = RecordingQuery()
    reconcile(q, _dt.date(2025, 3, 4), _dt.date(2026, 9, 11))

    assert q.calls, "reconcile 没有发生任何读取"
    for name, lo, hi in q.calls:
        assert lo.year == hi.year, (
            f"{name} 一次读了 {lo} ~ {hi}，跨了年。"
            f"全历史一次性载入约 8.2 GiB，生产节点只有 7.5 GiB。"
        )


def test_one_read_per_table_per_year():
    """2025-2026 两年，每张表各读两次，不多不少。

    多读说明有重复 IO，少读说明漏了年份。
    """
    import datetime as _dt

    q = RecordingQuery()
    reconcile(q, _dt.date(2025, 3, 4), _dt.date(2026, 9, 11))

    assert [c[0] for c in q.calls].count("tushare_daily") == 2
    assert [c[0] for c in q.calls].count("paid_daily") == 2


def test_year_windows_are_clipped_to_the_requested_range():
    """年窗口要被请求区间裁剪，不能读到区间之外去。"""
    import datetime as _dt

    q = RecordingQuery()
    reconcile(q, _dt.date(2025, 3, 4), _dt.date(2026, 9, 11))

    windows = sorted({(lo, hi) for _, lo, hi in q.calls})
    assert windows == [
        (_dt.date(2025, 3, 4), _dt.date(2025, 12, 31)),
        (_dt.date(2026, 1, 1), _dt.date(2026, 9, 11)),
    ]


def test_summary_aggregates_across_years(warehouse):
    """逐年处理不能改变跨年汇总的结果。

    全历史两张表同时载入约 8.2 GiB，生产节点只有 7.5 GiB——2026-09-13 实测
    被 OOM killer 杀掉，因此改为逐年。这条测试守的就是「改了实现、没改语义」。
    """
    import datetime as _dt

    day_2025 = _dt.date(2025, 3, 4)
    rows_2025 = [_row("000001.SZ") | {"dt": day_2025}]
    rows_2026 = [_row("000001.SZ"), _row("600000.SH")]

    for dataset, arrow_schema in (("paid_daily", schema.PAID_DAILY),
                                  ("tushare_daily", schema.TUSHARE_DAILY)):
        for year, rows in ((2025, rows_2025), (2026, rows_2026)):
            frame = pl.DataFrame(rows)
            path = warehouse.root / dataset / f"year={year}" / "part.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(frame.to_arrow().cast(arrow_schema), path)

    rep = reconcile(WarehouseQuery(warehouse), day_2025, DAY)
    assert rep.summary["common_rows"] == 3
    assert rep.summary["only_in_tushare"] == 0
    assert rep.summary["only_in_paid"] == 0


def test_a_difference_in_an_earlier_year_is_not_lost(warehouse):
    """早年的差异必须出现在最终清单里，不能被后一年覆盖掉。"""
    import datetime as _dt

    day_2025 = _dt.date(2025, 3, 4)
    for dataset, arrow_schema, close in (("paid_daily", schema.PAID_DAILY, 11.70),
                                         ("tushare_daily", schema.TUSHARE_DAILY, 11.71)):
        frame = pl.DataFrame([_row("000001.SZ") | {"dt": day_2025, "close": close}])
        path = warehouse.root / dataset / "year=2025" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(frame.to_arrow().cast(arrow_schema), path)
        frame26 = pl.DataFrame([_row("000001.SZ")])
        path26 = warehouse.root / dataset / "year=2026" / "part.parquet"
        path26.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(frame26.to_arrow().cast(arrow_schema), path26)

    rep = reconcile(WarehouseQuery(warehouse), day_2025, DAY)
    hit = rep.value_diffs.filter(pl.col("field") == "close")
    assert hit.height == 1
    assert hit["dt"].to_list() == [day_2025]


def test_null_on_one_side_only_is_a_difference(warehouse):
    """一边有值一边没有，必须报出来——这正是要查的东西。"""
    _write(warehouse, "paid_daily", [_row("000001.SZ", pe=5.39)], schema.PAID_DAILY)
    _write(warehouse, "tushare_daily", [_row("000001.SZ", pe=None)],
           schema.TUSHARE_DAILY)
    rep = reconcile(WarehouseQuery(warehouse), DAY, DAY)
    assert rep.value_diffs.filter(pl.col("field") == "pe").height == 1
