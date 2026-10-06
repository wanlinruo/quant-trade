from datetime import date, datetime

import polars as pl

from qsys.data.catalog import checks as C
from qsys.data.query import WarehouseQuery

DAY = date(2025, 7, 22)
CHECK = "B2.canonical_minute_override_inclusion"
PADDING_CHECK = "B2.canonical_qmt_leading_padding"


def _write(warehouse, dataset, rows, day=DAY):
    target = warehouse.root / dataset / f"year={day.year}" / f"date={day:%Y%m%d}"
    target.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(target / "part.parquet")


def _override(warehouse, dataset, symbols, day=DAY):
    _write(warehouse, dataset, [
        {"symbol": s, "dt": datetime.combine(day, datetime.min.time()), "close": 1.0}
        for s in symbols
    ], day)


def _canonical(warehouse, pairs, day=DAY):
    _write(warehouse, "bar_minute_1m", [
        {"symbol": s, "ts": datetime.combine(day, datetime.min.time()), "source": src}
        for s, src in pairs
    ], day)


def _run(warehouse):
    ctx = C.CheckContext(WarehouseQuery(warehouse), warehouse, date(2026, 1, 1), date(2026, 9, 18))
    return C.run_checks(
        ctx, "canonical-minute-test", datetime(2026, 9, 22),
        only=[C.CHECKS[CHECK]], artifacts=warehouse.root / "artifacts",
    ).row(0, named=True)


def _run_padding(warehouse, day=DAY):
    ctx = C.CheckContext(WarehouseQuery(warehouse), warehouse, day, day)
    return C.run_checks(
        ctx, "canonical-padding-test", datetime(2026, 10, 6),
        only=[C.CHECKS[PADDING_CHECK]], artifacts=warehouse.root / "artifacts",
    ).row(0, named=True)


def _canonical_bars(warehouse, source, volumes, day=DAY):
    rows = []
    for index, volume in enumerate(volumes):
        ts = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=30 + index)
        rows.append({
            "symbol": "600000.SH", "ts": ts,
            "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
            "volume": float(volume), "amount": float(volume) * 10,
            "source": source, "visible_at": ts,
        })
    _write(warehouse, "bar_minute_1m", rows, day)


def test_override_symbol_days_in_canonical_pass(warehouse):
    _override(warehouse, "joinquant_minute_bars", ["603227.SH", "600001.SH"])
    # 聚宽补的标的日被更高优先级的付费源占了也算合格
    _canonical(warehouse, [("603227.SH", "joinquant"), ("600001.SH", "paid"), ("000001.SZ", "paid")])
    row = _run(warehouse)
    assert row["passed"] is True
    assert row["checked_rows"] == 2


def test_missing_override_symbol_day_blocks_even_outside_the_current_year(warehouse):
    _override(warehouse, "joinquant_minute_bars", ["603227.SH", "603223.SH"])
    _canonical(warehouse, [("603223.SH", "joinquant"), ("000001.SZ", "paid")])
    row = _run(warehouse)
    assert row["passed"] is False
    assert row["severity"] == "blocking"
    assert row["violations"] == 1
    assert "joinquant_minute_bars 1 个，2025-07-22～2025-07-22" in row["detail"]


def test_override_that_fell_back_to_qmt_blocks(warehouse):
    _override(warehouse, "tushare_minute_bars", ["600002.SH"])
    _canonical(warehouse, [("600002.SH", "qmt")])
    row = _run(warehouse)
    assert row["passed"] is False
    assert row["violations"] == 1


def test_missing_canonical_partition_counts_every_override(warehouse):
    _override(warehouse, "joinquant_minute_bars", ["603227.SH", "603223.SH"], day=date(2025, 7, 23))
    row = _run(warehouse)
    assert row["violations"] == 2


def test_no_overrides_is_not_applicable(warehouse):
    row = _run(warehouse)
    assert row["checked_rows"] == 0


def test_qmt_multiple_leading_zeros_block(warehouse):
    _canonical_bars(warehouse, "qmt", [0, 0, 10])
    row = _run_padding(warehouse)
    assert row["passed"] is False
    assert row["severity"] == "blocking"
    assert row["violations"] == 1


def test_single_or_paid_leading_zeros_do_not_create_noise(warehouse):
    _canonical_bars(warehouse, "qmt", [0, 10])
    assert _run_padding(warehouse)["passed"] is True
    _canonical_bars(warehouse, "paid", [0, 0, 10])
    assert _run_padding(warehouse)["passed"] is True


def test_padding_check_without_a_canonical_partition_is_not_applicable(warehouse):
    row = _run_padding(warehouse)
    assert row["passed"] is True
    assert row["checked_rows"] == 0
