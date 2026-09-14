"""QMT 日线补采：只填洞，不覆盖已有数据。"""

from datetime import date

import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.pipeline.daily_backfill import backfill_daily_bars
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

D = [date(2024, 1, d) for d in (2, 3, 4)]


def seed(warehouse: Warehouse, rows) -> None:
    """rows: [(symbol, dt, close)]"""
    n = len(rows)
    warehouse.write(
        "bars_daily",
        pa.table(
            {
                "symbol": [r[0] for r in rows], "dt": [r[1] for r in rows],
                "open": [r[2] for r in rows], "high": [r[2] for r in rows],
                "low": [r[2] for r in rows], "close": [r[2] for r in rows],
                "volume": [1000] * n, "amount": [1e6] * n,
            },
            schema=schema.BARS_DAILY,
        ),
        year=2024,
    )


def incoming(tmp_path, rows, name="daily_00000.parquet"):
    """rows: [(symbol, dt, close, suspend_flag)]"""
    d = tmp_path / "in"
    d.mkdir(exist_ok=True)
    n = len(rows)
    pl.DataFrame({
        "symbol": [r[0] for r in rows], "dt": [r[1] for r in rows],
        "open": [r[2] for r in rows], "high": [r[2] for r in rows],
        "low": [r[2] for r in rows], "close": [r[2] for r in rows],
        "volume": [2000] * n, "amount": [2e6] * n,
        "suspend_flag": [r[3] for r in rows],
    }, schema={"symbol": pl.Utf8, "dt": pl.Date, "open": pl.Float64,
               "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
               "volume": pl.Int64, "amount": pl.Float64,
               "suspend_flag": pl.Int64}).write_parquet(d / name)
    return d


def test_a_hole_is_filled(warehouse, tmp_path):
    seed(warehouse, [("600001.SH", D[0], 10.0), ("600001.SH", D[2], 12.0)])
    src = incoming(tmp_path, [("600001.SH", D[1], 11.0, 0)])

    result = backfill_daily_bars(warehouse, src)

    assert result.filled_rows == 1
    got = WarehouseQuery(warehouse).legacy_daily_bars(["600001.SH"], D[0], D[2])
    assert got["close"].to_list() == [10.0, 11.0, 12.0]


def test_an_existing_day_is_never_overwritten(warehouse, tmp_path):
    """mootdx 的数据过了跨源校验，QMT 只补它漏掉的洞。"""
    seed(warehouse, [("600001.SH", D[0], 10.0)])
    src = incoming(tmp_path, [("600001.SH", D[0], 99.0, 0)])

    result = backfill_daily_bars(warehouse, src)

    assert result.filled_rows == 0
    got = WarehouseQuery(warehouse).legacy_daily_bars(["600001.SH"], D[0], D[0])
    assert got["close"].to_list() == [10.0]


def test_a_suspended_day_is_skipped(warehouse, tmp_path):
    """真停牌本来就该没有 bar，补进去反而是错的。"""
    seed(warehouse, [("600001.SH", D[0], 10.0), ("600001.SH", D[2], 12.0)])
    src = incoming(tmp_path, [("600001.SH", D[1], 11.0, 1)])

    result = backfill_daily_bars(warehouse, src)

    assert result.filled_rows == 0
    assert result.suspended_rows == 1


def test_only_restricts_what_gets_filled(warehouse, tmp_path):
    seed(warehouse, [("600001.SH", D[0], 10.0)])
    src = incoming(tmp_path, [("600001.SH", D[1], 11.0, 0),
                              ("600001.SH", D[2], 12.0, 0)])
    only = pl.DataFrame({"symbol": ["600001.SH"], "dt": [D[1]]},
                        schema={"symbol": pl.Utf8, "dt": pl.Date})

    result = backfill_daily_bars(warehouse, src, only=only)

    assert result.filled_rows == 1
    assert WarehouseQuery(warehouse).legacy_daily_bars(
        ["600001.SH"], D[0], D[2])["dt"].to_list() == [D[0], D[1]]


def test_a_bad_file_does_not_sink_the_batch(warehouse, tmp_path):
    seed(warehouse, [("600001.SH", D[0], 10.0)])
    src = incoming(tmp_path, [("600001.SH", D[1], 11.0, 0)])
    (src / "daily_00001.parquet").write_bytes(b"not a parquet")

    result = backfill_daily_bars(warehouse, src)

    assert result.filled_rows == 1
    assert result.errors == 1


def test_files_are_archived_when_asked(warehouse, tmp_path):
    seed(warehouse, [("600001.SH", D[0], 10.0)])
    src = incoming(tmp_path, [("600001.SH", D[1], 11.0, 0)])
    archive = tmp_path / "done"

    backfill_daily_bars(warehouse, src, archive_dir=archive)

    assert list(src.glob("*.parquet")) == []
    assert len(list(archive.glob("*.parquet"))) == 1


def test_a_missing_directory_is_not_an_error(warehouse, tmp_path):
    assert backfill_daily_bars(warehouse, tmp_path / "nope").filled_rows == 0
