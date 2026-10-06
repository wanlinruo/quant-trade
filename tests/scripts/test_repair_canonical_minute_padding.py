from datetime import date, datetime
from pathlib import Path

import polars as pl

from qsys.data import schema
from qsys.data.quality.minute_padding import leading_zero_cases
from scripts.repair_canonical_minute_padding import apply_qmt_day, plan_qmt_day

DAY = date(2026, 1, 2)


def _qmt(symbol: str, minute: int, volume: int) -> dict:
    price = 10.0
    return {
        "symbol": symbol, "dt": datetime(2026, 1, 2, 9, minute),
        "open": price, "high": price, "low": price, "close": price,
        "volume": volume, "amount": price * volume,
    }


def _canonical(row: dict) -> dict:
    ts = row["dt"]
    return {
        "symbol": row["symbol"], "ts": ts,
        "open": row["open"], "high": row["high"], "low": row["low"],
        "close": row["close"], "volume": float(row["volume"]),
        "amount": row["amount"], "source": "qmt", "visible_at": ts,
    }


def _seed(warehouse) -> pl.DataFrame:
    # 当前源表已经清除了两根伪填充；成交后的零量分钟仍须保留。
    current = [_qmt("600000.SH", 32, 100), _qmt("600000.SH", 33, 0)]
    warehouse.write_day(
        "minute_bars", pl.DataFrame(current).to_arrow().cast(schema.MINUTE_BARS), day=DAY
    )
    stale = [_qmt("600000.SH", 30, 0), _qmt("600000.SH", 31, 0), *current]
    old = pl.DataFrame([_canonical(row) for row in stale]).select(schema.BAR_MINUTE_1M.names)
    warehouse.write_day(
        "bar_minute_1m", old.to_arrow().cast(schema.BAR_MINUTE_1M), day=DAY
    )
    return old


def test_plan_requires_exact_equality_with_the_current_qmt_source(warehouse):
    old = _seed(warehouse)
    cases = leading_zero_cases(old)

    item = plan_qmt_day(warehouse.root, DAY, cases)

    assert item["symbols"] == ["600000.SH"]
    assert item["removed_rows"] == 2
    assert item["rows_before"] == 4
    assert item["rows_after"] == 2
    assert item["frame"]["volume"].to_list() == [100.0, 0.0]


def test_apply_backs_up_then_reconciles_every_written_row(warehouse, tmp_path):
    old = _seed(warehouse)
    item = plan_qmt_day(warehouse.root, DAY, leading_zero_cases(old))

    result = apply_qmt_day(warehouse.root, tmp_path / "backup", item)

    backup = Path(result["backup"])
    assert backup.exists()
    assert pl.read_parquet(backup).height == 4
    target = warehouse.day_path("bar_minute_1m", DAY)
    assert pl.read_parquet(target).height == 2
    assert result["after_sha256"] != result["before_sha256"]


def test_paid_or_single_prefixes_are_never_planned(warehouse):
    old = _seed(warehouse).with_columns(pl.lit("paid").alias("source"))
    cases = leading_zero_cases(old).with_columns(pl.lit(1).alias("leading_rows"))

    try:
        plan_qmt_day(warehouse.root, DAY, cases)
    except ValueError as exc:
        assert "没有具备证据" in str(exc)
    else:
        raise AssertionError("paid 或单根前缀不得进入 QMT 修复计划")
