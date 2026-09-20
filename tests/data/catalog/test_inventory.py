from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.catalog.inventory import (
    build_inventory,
    query_method_inventory,
    warehouse_stats,
)


PROJECT = Path(__file__).parents[3]


def test_warehouse_stats_reads_only_parquet_metadata(tmp_path):
    path = tmp_path / "bars_daily" / "year=2026" / "part.parquet"
    path.parent.mkdir(parents=True)
    table = pa.Table.from_pylist(
        [{
            "symbol": "000001.SZ", "dt": date(2026, 9, 18),
            "open": 1.0, "high": 2.0, "low": 1.0, "close": 2.0,
            "volume": 10, "amount": 20.0,
        }],
        schema=schema.BARS_DAILY,
    )
    pq.write_table(table, path)

    got = warehouse_stats(tmp_path, "bars_daily")

    assert got.files == 1
    assert got.rows == 1
    assert got.bytes == path.stat().st_size
    assert got.min_time == "2026-09-18"
    assert got.max_time == "2026-09-18"
    assert got.errors == ()


def test_every_public_query_method_has_dataset_ownership():
    refs = query_method_inventory(PROJECT / "src/qsys/data/query.py")
    assert refs
    assert all(ref.datasets for ref in refs)
    by_name = {ref.name: ref for ref in refs}
    assert by_name["daily_bars"].datasets == ("tushare_daily",)
    assert by_name["listing_dates"].datasets == ("tushare_daily",)
    assert by_name["instruments"].datasets == ("instruments",)
    assert by_name["dataset_bounds"].datasets == ("*",)


def test_inventory_coverage_equals_schema_and_read_only(tmp_path):
    report = build_inventory(PROJECT, tmp_path, test_result="1842 passed")
    expected = sorted(set(schema.ALL) | set(schema.READ_ONLY))
    assert report["coverage"]["registered_physical"] == expected
    assert [item["id"] for item in report["datasets"]] == expected
    assert report["coverage"]["registered_logical"] == [
        "bar_daily_realtime", "bar_minute_realtime", "daily_bars",
        "quote_realtime_latest", "strategy",
    ]
    assert report["source_references"]
    assert report["baseline"]["test_result"] == "1842 passed"
    assert any(
        item["kind"] == "query_call" and item["name"] == "daily_bars"
        for item in report["source_references"]
    )
