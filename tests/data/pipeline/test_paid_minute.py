import json
import zipfile
from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

from qsys.data.pipeline.paid_minute import (
    SCHEMA,
    ingest_year,
    publish_year,
    quarantine_unmatched_zero_trade_days,
    query_paid_minute,
    validate_year,
)


HEADER = "证券代码,交易日期,分钟时间,开盘价（元）,最高价（元）,最低价（元）,收盘价（元）,成交量,成交额,复权因子\n"


def make_zip(path: Path, *, volume: str = "100", factor: str = "2.5") -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("__MACOSX/2025/.DS_Store", "junk")
        archive.writestr("2025/5min/000001.SZ@来源.csv", HEADER)
        archive.writestr(
            "2025/1min/000001.SZ@来源.csv",
            HEADER
            + f"000001.SZ,20250102,09:30,10,10.2,9.9,10.1,{volume},1000,{factor}\n"
            + "000001.SZ,20250102,09:31,10.1,10.3,10,10.2,120,1224,2.5\n",
        )
        archive.writestr(
            "2025/1min/600000.SH@来源.csv",
            HEADER + "600000.SH,2025-01-02,09:30:00,8,8.1,7.9,8,0,0,1.2\n",
        )


def test_stage_is_resumable_and_publish_is_atomic(tmp_path):
    source = tmp_path / "2025.zip"
    make_zip(source)
    warehouse = tmp_path / "warehouse"

    first = ingest_year(source, warehouse, 2025, shard_members=1)
    assert (first.entries, first.shards, first.rows, first.symbols) == (2, 2, 3, 2)
    second = ingest_year(source, warehouse, 2025, shard_members=1)
    assert second == first

    staged = warehouse / "paid_minute_bars_staging/year=2025"
    manifest = json.loads((staged / "manifest.json").read_text())
    assert manifest["status"] == "STAGED"
    frame = pl.read_parquet(staged / "shard=0000/part.parquet")
    assert frame.columns == ["symbol", "dt", "open", "high", "low", "close", "volume", "amount", "factor"]
    assert frame["volume"].dtype == pl.Int64

    paid = pl.DataFrame({
        "symbol": ["000001.SZ", "600000.SH"],
        "dt": [date(2025, 1, 2), date(2025, 1, 2)],
        "open": [10.0, 8.0], "high": [10.3, 8.1], "low": [9.9, 7.9],
        "close": [10.2, 8.0], "volume": [2.2, 0.0], "amount": [2224.0, 0.0],
        "factor": [2.5, 1.2],
    })
    paid_dir = warehouse / "paid_daily/year=2025"
    paid_dir.mkdir(parents=True)
    paid.write_parquet(paid_dir / "part.parquet")
    summary = validate_year(warehouse, 2025)
    assert summary["structural"]["row_count"] == 3
    assert summary["daily_comparison"]["matched_paid_days"] == 2
    assert summary["daily_comparison"]["ohlc_mismatch_days"] == 0
    assert summary["daily_comparison"]["volume_mismatch_days"] == 0
    assert summary["daily_comparison"]["factor_mismatch_days"] == 0

    target = publish_year(warehouse, 2025)
    assert target == warehouse / "paid_minute_bars/year=2025"
    assert target.exists() and not staged.exists()
    queried = query_paid_minute(
        warehouse, ["000001.SZ"], datetime(2025, 1, 2, 9, 30), datetime(2025, 1, 2, 9, 31)
    )
    assert queried.height == 2
    assert queried["factor"].to_list() == [2.5, 2.5]
    with pytest.raises(ValueError, match="已发布"):
        publish_year(warehouse, 2025)


def test_fractional_source_volume_is_rejected(tmp_path):
    source = tmp_path / "2025.zip"
    make_zip(source, volume="100.5")
    with pytest.raises(ValueError, match="成交量不是源整数"):
        ingest_year(source, tmp_path / "warehouse", 2025)


def test_null_core_row_is_quarantined_without_filling(tmp_path):
    source = tmp_path / "2025.zip"
    make_zip(source, factor="")
    warehouse = tmp_path / "warehouse"
    result = ingest_year(source, warehouse, 2025)
    assert result.rows == 2
    evidence = pl.read_parquet(
        warehouse / "paid_minute_quarantine/year=2025/null-core/000001.SZ.parquet"
    )
    assert evidence.height == 1
    assert evidence["复权因子"].null_count() == 1
    assert evidence["reason"].to_list() == ["CORE_FIELD_NULL"]


def test_fully_null_member_is_quarantined_and_year_continues(tmp_path):
    source = tmp_path / "2025.zip"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "2025/1min/920116.BJ@来源.csv",
            HEADER + "920116.BJ,20250102,09:30,0,0,0,0,0,0,\n",
        )
        archive.writestr(
            "2025/1min/600000.SH@来源.csv",
            HEADER + "600000.SH,20250102,09:30,8,8,8,8,0,0,1.2\n",
        )
    warehouse = tmp_path / "warehouse"
    result = ingest_year(source, warehouse, 2025)
    assert (result.entries, result.symbols, result.rows) == (2, 1, 1)
    evidence = pl.read_parquet(
        warehouse / "paid_minute_quarantine/year=2025/null-core/920116.BJ.parquet"
    )
    assert evidence.height == 1
    assert evidence["reason"].to_list() == ["CORE_FIELD_NULL"]


def test_negative_source_volume_is_quarantined_without_repair(tmp_path):
    source = tmp_path / "2025.zip"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "2025/1min/920000.BJ@来源.csv",
            HEADER
            + "920000.BJ,20250102,15:29,8,8,8,8,10,80,1.2\n"
            + "920000.BJ,20250102,15:30,8,8,8,8,-10,-80,1.2\n",
        )
    warehouse = tmp_path / "warehouse"
    result = ingest_year(source, warehouse, 2025)
    assert result.rows == 1
    fact = pl.read_parquet(
        warehouse / "paid_minute_bars_staging/year=2025/shard=0000/part.parquet"
    )
    assert fact["volume"].to_list() == [10]
    evidence = pl.read_parquet(
        warehouse / "paid_minute_quarantine/year=2025/invalid-values/920000.BJ.parquet"
    )
    assert evidence["成交量"].to_list() == ["-10"]
    assert evidence["reason"].to_list() == ["VALUE_CONSTRAINT"]


def test_unmatched_zero_trade_flat_day_is_quarantined_without_repair(tmp_path):
    source = tmp_path / "2025.zip"
    make_zip(source)
    warehouse = tmp_path / "warehouse"
    ingest_year(source, warehouse, 2025, shard_members=1)
    paid_dir = warehouse / "paid_daily/year=2025"
    paid_dir.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["000001.SZ"], "dt": [date(2025, 1, 2)],
        "open": [10.0], "high": [10.3], "low": [9.9], "close": [10.2],
        "volume": [2.2], "amount": [2224.0], "factor": [2.5],
    }).write_parquet(paid_dir / "part.parquet")
    validate_year(warehouse, 2025)

    # 测试样本只有一根；改为 241 个直接源分钟，满足真实占位日的严格条件。
    shard = warehouse / "paid_minute_bars_staging/year=2025/shard=0001/part.parquet"
    base = pl.read_parquet(shard).row(0, named=True)
    for field in ["open", "high", "low", "close"]:
        base[field] = 8.0
    times = pl.concat([
        pl.datetime_range(datetime(2025, 1, 2, 9, 30), datetime(2025, 1, 2, 11, 30), "1m", eager=True),
        pl.datetime_range(datetime(2025, 1, 2, 13, 1), datetime(2025, 1, 2, 15, 0), "1m", eager=True),
    ])
    placeholder = pl.DataFrame({**{name: [base[name]] * 241 for name in SCHEMA.names if name != "dt"}, "dt": times})
    placeholder.select(SCHEMA.names).write_parquet(shard)
    receipt_path = shard.parent / "manifest.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["rows"] = 241
    receipt_path.write_text(json.dumps(receipt))
    root_manifest = warehouse / "paid_minute_bars_staging/year=2025/manifest.json"
    root = json.loads(root_manifest.read_text())
    root["rows"] = 243
    root_manifest.write_text(json.dumps(root))
    validate_year(warehouse, 2025)

    result = quarantine_unmatched_zero_trade_days(warehouse, 2025)
    assert result == {
        "year": 2025, "days": 1, "rows": 241, "shards": 1,
        "retained_unmatched_days": 0,
    }
    evidence = pl.read_parquet(
        warehouse / "paid_minute_quarantine/year=2025/zero-trade-flat-no-daily/shard=0001/part.parquet"
    )
    assert evidence.height == 241
    assert evidence["reason"].unique().to_list() == ["ZERO_TRADE_FLAT_NO_DAILY_RECORD"]
    assert pl.read_parquet(shard).height == 0
