import json
from datetime import date

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.pipeline.minute_repairs import expected_minute_times, write_paid_minute_file
from scripts import repair_paid_minute_overrides as repair

MIXED = date(2026, 3, 5)      # 一只正常、一只成交量放大，且是 large_string
ALL_BAD = date(2026, 3, 6)    # 唯一一只就是坏的
CAST_ONLY = date(2026, 3, 12)  # 数据正确，只是 large_string
CLEAN = date(2026, 3, 13)     # 已合规，应跳过


def _minutes(day, symbols, *, volume=100):
    times = expected_minute_times(day)
    return pl.concat([
        pl.DataFrame({
            "symbol": [symbol] * len(times), "dt": times,
            "open": [10.0] * len(times), "high": [10.0] * len(times),
            "low": [10.0] * len(times), "close": [10.0] * len(times),
            "volume": [volume] * len(times), "amount": [1000.0] * len(times),
            "factor": [1.5] * len(times),
        })
        for symbol in symbols
    ])


def _path(root, day):
    return root / f"paid_minute_bars/year={day.year}/date={day:%Y%m%d}/part.parquet"


def _polars_write(root, day, frame):
    path = _path(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)  # polars 默认 large_string、无元数据，复现早期脚本
    return path


def _daily(warehouse, days_symbols):
    rows = []
    for day, symbol in days_symbols:
        row = {field.name: 1.0 for field in list(schema.TUSHARE_DAILY)[3:]}
        row.update({"dt": day, "symbol": symbol, "name": symbol, "open": 10.0,
                    "high": 10.0, "low": 10.0, "close": 10.0, "volume": 241.0,
                    "amount": 241 * 1000.0})
        rows.append(row)
    warehouse.write("tushare_daily", pl.DataFrame(rows).select(schema.TUSHARE_DAILY.names)
                    .to_arrow().cast(schema.TUSHARE_DAILY), year=2026)


def _setup(warehouse):
    root = warehouse.root
    _polars_write(root, MIXED, pl.concat([
        _minutes(MIXED, ["300029.SZ"]), _minutes(MIXED, ["000004.SZ"], volume=7700),
    ]))
    _polars_write(root, ALL_BAD, _minutes(ALL_BAD, ["000004.SZ"], volume=7700))
    _polars_write(root, CAST_ONLY, _minutes(CAST_ONLY, ["300029.SZ"]))
    write_paid_minute_file(_minutes(CLEAN, ["300029.SZ"]), _path(root, CLEAN))
    _daily(warehouse, [(MIXED, "300029.SZ"), (MIXED, "000004.SZ"), (ALL_BAD, "000004.SZ"),
                       (CAST_ONLY, "300029.SZ"), (CLEAN, "300029.SZ")])
    return root


def _run(monkeypatch, tmp_path, root, *extra):
    report = tmp_path / f"report{len(extra)}.json"
    monkeypatch.setattr("sys.argv", [
        "repair", "--warehouse", str(root), "--backup-dir", str(tmp_path / "backup"),
        "--lock", str(tmp_path / "write.lock"), "--report", str(report), *extra,
    ])
    assert repair.main() == 0
    return json.loads(report.read_text())


def test_dry_run_plans_without_touching_files(tmp_path, warehouse, monkeypatch):
    root = _setup(warehouse)
    before = {p: p.read_bytes() for p in (root / "paid_minute_bars").rglob("*.parquet")}

    summary = _run(monkeypatch, tmp_path, root)

    assert summary["files"] == 3
    assert summary["cast_files"] == 3
    assert summary["quarantine_symbol_days"] == [
        "2026-03-05:000004.SZ:volume", "2026-03-06:000004.SZ:volume",
    ]
    assert {p: p.read_bytes() for p in before} == before
    assert not (root / "paid_minute_quarantine").exists()


def test_apply_quarantines_gross_days_and_normalizes_schema(tmp_path, warehouse, monkeypatch):
    root = _setup(warehouse)
    clean_bytes = _path(root, CLEAN).read_bytes()
    original_mixed = pl.read_parquet(_path(root, MIXED))

    summary = _run(monkeypatch, tmp_path, root, "--apply")

    mixed = pq.read_table(_path(root, MIXED))
    assert mixed.schema.field("symbol").type == pa.string()
    assert mixed.schema.metadata[b"provider"] == b"yuhaha-baidupan"
    assert pl.from_arrow(mixed)["symbol"].unique().to_list() == ["300029.SZ"]
    assert not _path(root, ALL_BAD).exists()
    cast_only = pq.read_table(_path(root, CAST_ONLY))
    assert cast_only.schema.field("symbol").type == pa.string()
    assert pl.from_arrow(cast_only).equals(_minutes(CAST_ONLY, ["300029.SZ"]))
    assert _path(root, CLEAN).read_bytes() == clean_bytes

    moved = pl.read_parquet(
        root / "paid_minute_quarantine/year=2026/daily-volume-mismatch/date=20260305/part.parquet"
    )
    assert moved.equals(original_mixed.filter(pl.col("symbol") == "000004.SZ").sort("dt"))
    assert pl.read_parquet(
        tmp_path / "backup/paid_minute_bars/year=2026/date=20260305/part.parquet"
    ).equals(original_mixed)
    assert [change["rows_after"] for change in summary["changes"]] == [241, 0, 241]


def test_second_apply_is_a_no_op(tmp_path, warehouse, monkeypatch):
    root = _setup(warehouse)
    _run(monkeypatch, tmp_path, root, "--apply")

    summary = _run(monkeypatch, tmp_path, root, "--apply", "--start", "2026-03-01")

    assert summary["files"] == 0
    assert summary["changes"] == []
