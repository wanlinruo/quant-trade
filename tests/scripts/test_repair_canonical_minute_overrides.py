from datetime import date, timedelta

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.pipeline.minute_repairs import expected_minute_times
from scripts import repair_canonical_minute_overrides as repair

DAY = date(2025, 7, 22)


def _bars(symbols, *, price=10.0, volume=100):
    times = expected_minute_times(DAY)
    return pl.concat([
        pl.DataFrame({
            "symbol": [s] * len(times), "dt": times,
            "open": [price] * len(times), "high": [price] * len(times),
            "low": [price] * len(times), "close": [price] * len(times),
            "volume": [volume] * len(times), "amount": [price * volume] * len(times),
        })
        for s in symbols
    ])


def _write(root, dataset, frame):
    target = root / dataset / f"year={DAY.year}" / f"date={DAY:%Y%m%d}"
    target.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(target / "part.parquet")


def _published(root, symbols):
    """已发布规范分区：付费源的 600000.SH。"""
    frame = _bars(symbols).rename({"dt": "ts"}).with_columns(
        (pl.col("volume") / 100).cast(pl.Float64),
        pl.lit("paid").alias("source"),
        (pl.col("ts") + timedelta(minutes=1)).alias("visible_at"),
    ).select(schema.BAR_MINUTE_1M.names)
    target = root / "bar_minute_1m" / f"year={DAY.year}" / f"date={DAY:%Y%m%d}"
    target.mkdir(parents=True, exist_ok=True)
    pl.from_arrow(frame.to_arrow().cast(schema.BAR_MINUTE_1M)).write_parquet(target / "part.parquet")
    return frame


def _seed(warehouse):
    root = warehouse.root
    paid = _bars(["600000.SH"], volume=1000).with_columns(pl.lit(1.0).alias("factor"))
    _write(root, "paid_minute_bars", paid)
    _write(root, "joinquant_minute_bars", _bars(["603227.SH"], price=20.0, volume=300))
    return _published(root, ["600000.SH"])


def test_plan_finds_only_the_missing_override_symbol_day(warehouse):
    _seed(warehouse)
    item = repair.plan_day(warehouse.root, DAY)
    assert item["add"] == ["603227.SH"]
    assert item["rows"]["source"].unique().to_list() == ["joinquant"]
    assert item["rows"].height == len(expected_minute_times(DAY))
    assert item["skipped"] == {}


def test_apply_appends_rows_and_keeps_existing_rows_untouched(warehouse, tmp_path):
    before = _seed(warehouse)
    item = repair.plan_day(warehouse.root, DAY)
    result = repair.apply_day(warehouse.root, tmp_path / "backup", item)

    after = pl.read_parquet(warehouse.root / f"bar_minute_1m/year=2025/date={DAY:%Y%m%d}/part.parquet")
    assert result["added_symbols"] == ["603227.SH"]
    assert result["rows_after"] == result["rows_before"] + result["added_rows"]
    kept = after.filter(pl.col("symbol") == "600000.SH").sort("ts")
    assert kept.select(before.columns).equals(
        pl.from_arrow(before.to_arrow().cast(schema.BAR_MINUTE_1M)).sort("ts"))
    added = after.filter(pl.col("symbol") == "603227.SH")
    assert added["volume"].unique().to_list() == [3.0]   # 股转手
    assert (tmp_path / "backup" / f"bar_minute_1m/year=2025/date={DAY:%Y%m%d}/part.parquet").exists()
    # 修复后再预演就没有缺口
    assert repair.plan_day(warehouse.root, DAY) is None


def test_apply_refuses_to_overwrite_an_existing_backup(warehouse, tmp_path):
    _seed(warehouse)
    item = repair.plan_day(warehouse.root, DAY)
    backup = tmp_path / "backup" / f"bar_minute_1m/year=2025/date={DAY:%Y%m%d}/part.parquet"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"x")
    with pytest.raises(FileExistsError):
        repair.apply_day(warehouse.root, tmp_path / "backup", item)
