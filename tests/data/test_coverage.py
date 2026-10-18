"""数据覆盖查询：回答「我有哪些数据」。"""

from datetime import date, datetime

import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.coverage import coverage, symbol_coverage
from qsys.data.storage import Warehouse

D = [date(2024, 1, 2), date(2024, 1, 3)]


def seed(warehouse: Warehouse) -> None:
    warehouse.write(
        "bars_daily",
        pa.table(
            {
                "symbol": ["600036.SH", "600036.SH", "000001.SZ"],
                "dt": [D[0], D[1], D[0]],
                "open": [10.0] * 3,
                "high": [10.0] * 3,
                "low": [10.0] * 3,
                "close": [10.0] * 3,
                "volume": [1000] * 3,
                "amount": [0.0] * 3,
            },
            schema=schema.BARS_DAILY,
        ),
        year=2024,
    )


def test_reports_nothing_on_an_empty_warehouse(warehouse):
    assert coverage(warehouse) == []


def test_reports_row_and_symbol_counts(warehouse):
    seed(warehouse)
    bars = next(c for c in coverage(warehouse) if c.dataset == "bars_daily")
    assert bars.rows == 3
    assert bars.symbols == 2


def test_reports_the_date_range(warehouse):
    seed(warehouse)
    bars = next(c for c in coverage(warehouse) if c.dataset == "bars_daily")
    assert bars.first_dt == D[0]
    assert bars.last_dt == D[1]


def test_symbol_coverage_narrows_to_one_instrument(warehouse):
    seed(warehouse)
    c = symbol_coverage(warehouse, "600036.SH")
    assert c is not None
    assert c.rows == 2
    assert c.symbols == 1


def test_symbol_coverage_of_an_absent_symbol_is_none(warehouse):
    seed(warehouse)
    assert symbol_coverage(warehouse, "999999.SH") is None


def test_symbol_coverage_on_an_empty_warehouse_is_none(warehouse):
    assert symbol_coverage(warehouse, "600036.SH") is None


def test_datasets_without_a_date_column_are_still_reported(warehouse):
    warehouse.write(
        "instruments",
        pa.table(
            {
                "symbol": ["600036.SH"],
                "name": ["招商银行"],
                "market": ["SH"],
                "list_date": [None],
                "delist_date": [None],
                "board": ["main"],
                "as_of": [None],
            },
            schema=schema.INSTRUMENTS,
        ),
    )
    inst = next(c for c in coverage(warehouse) if c.dataset == "instruments")
    assert inst.rows == 1
    assert inst.first_dt is None


def test_derived_tables_are_reported_too(warehouse):
    """涨跌停、生态因子、估值都要能在「我有哪些数据」里看到。"""
    for dataset in ("daily_flags", "market_regime", "valuation_snapshot"):
        arrow_schema = schema.ALL[dataset]
        warehouse.write(
            dataset,
            pa.table(
                {f.name: pa.array([], type=f.type) for f in arrow_schema},
                schema=arrow_schema,
            ),
            year=2024,
        )
    reported = {c.dataset for c in coverage(warehouse)}
    assert {"daily_flags", "market_regime", "valuation_snapshot"} <= reported


def _minute_table(dataset, day, symbol="600036.SH"):
    arrow_schema = schema.ALL[dataset]
    values = {field.name: [None] for field in arrow_schema}
    values["symbol"] = [symbol]
    values["ts" if dataset == "bar_minute_1m" else "dt"] = [
        datetime.combine(day, datetime.min.time()).replace(hour=9, minute=31)
    ]
    return pa.table(values, schema=arrow_schema)


@pytest.mark.parametrize("dataset", ["minute_bars", "bar_minute_1m"])
def test_daily_minute_partitions_report_real_timestamp_bounds(warehouse, dataset):
    day = date(2026, 10, 16)
    warehouse.write_day(dataset, _minute_table(dataset, day), day=day)
    got = next(row for row in coverage(warehouse) if row.dataset == dataset)
    assert got.rows == 1 and got.symbols == 1
    assert got.first_dt == got.last_dt == datetime(2026, 10, 16, 9, 31)


def test_daily_partitions_supersede_same_year_annual_copy_but_keep_older_years(warehouse):
    dataset = "minute_bars"
    older = date(2025, 12, 31)
    day = date(2026, 10, 16)
    warehouse.write(dataset, _minute_table(dataset, older), year=2025)
    warehouse.write(dataset, _minute_table(dataset, day), year=2026)
    warehouse.write_day(dataset, _minute_table(dataset, day, "000001.SZ"), day=day)
    got = next(row for row in coverage(warehouse) if row.dataset == dataset)
    assert got.rows == 2 and got.symbols == 2
    assert got.first_dt == datetime(2025, 12, 31, 9, 31)
    assert got.last_dt == datetime(2026, 10, 16, 9, 31)


def test_coverage_accepts_a_warehouse_path_containing_a_quote(tmp_path):
    warehouse = Warehouse(tmp_path / "owner's warehouse")
    seed(warehouse)
    assert next(row for row in coverage(warehouse) if row.dataset == "bars_daily").rows == 3
