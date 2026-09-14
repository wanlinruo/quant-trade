from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from qsys.data import schema
from qsys.data.storage import Warehouse


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    return Warehouse(tmp_path / "warehouse")


@pytest.fixture
def write_daily_bars():
    """把日线夹具写进生产 ``tushare_daily`` 分区，供既有测试复用。

    paid_daily 永久只读、不经 Warehouse.write，所以这里直接落分区文件。
    未提供的 20 列一律补 **null**——补 0 会让下游把「没有数据」当成
    「数值为零」，正是规矩里禁止的填充。
    """

    def write(warehouse, frame):
        import polars as pl

        missing = [n for n in schema.PAID_DAILY.names if n not in frame.columns]
        frame = frame.with_columns(
            [pl.lit(None, dtype=pl.Utf8).alias(n) for n in missing if n == "name"]
            + [pl.lit(None, dtype=pl.Float64).alias(n) for n in missing if n != "name"]
        ).select(schema.PAID_DAILY.names)
        for year in sorted({d.year for d in frame["dt"].to_list()}):
            path = warehouse.root / "tushare_daily" / f"year={year}" / "part.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            mask = pl.col("dt").is_between(date(year, 1, 1), date(year, 12, 31))
            chunk = frame.filter(mask).to_arrow().cast(schema.TUSHARE_DAILY)
            pq.write_table(chunk, path)

    return write
