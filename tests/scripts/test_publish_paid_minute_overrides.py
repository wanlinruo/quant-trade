import json
from datetime import date

import polars as pl
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.pipeline.minute_repairs import expected_minute_times
from scripts import publish_paid_minute_overrides as publish

DAY = date(2026, 3, 5)
STAMP = f"{DAY:%Y%m%d}"


def _minutes(symbols, *, price=10.0):
    times = expected_minute_times(DAY)
    return pl.concat([
        pl.DataFrame({
            "symbol": [symbol] * len(times), "dt": times,
            "open": [price] * len(times), "high": [price] * len(times),
            "low": [price] * len(times), "close": [price] * len(times),
            "volume": [100] * len(times), "amount": [100 * price] * len(times),
            "factor": [1.5] * len(times),
        })
        for symbol in symbols
    ])


def _write(path, frame):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)


def test_rejects_symbol_days_that_disagree_with_daily(tmp_path, warehouse, monkeypatch):
    symbols = ["000004.SZ", "300344.SZ"]
    root = warehouse.root
    _write(root / f"minute_bars/year=2026/date={STAMP}/part.parquet",
           _minutes(symbols, price=0.0).drop("factor"))
    staging = tmp_path / "staging"
    _write(staging / f"year=2026/date={STAMP}/part.parquet", _minutes(symbols))
    rows = []
    for symbol, lots in (("000004.SZ", 241.0 / 77), ("300344.SZ", 241.0)):
        row = {field.name: 1.0 for field in list(schema.TUSHARE_DAILY)[3:]}
        row.update({"dt": DAY, "symbol": symbol, "name": symbol, "open": 10.0,
                    "high": 10.0, "low": 10.0, "close": 10.0, "volume": lots,
                    "amount": 241 * 1000.0})
        rows.append(row)
    warehouse.write("tushare_daily", pl.DataFrame(rows).select(schema.TUSHARE_DAILY.names)
                    .to_arrow().cast(schema.TUSHARE_DAILY), year=2026)
    report = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", [
        "publish", "--warehouse", str(root), "--paid-staging", str(staging),
        "--backup-dir", str(tmp_path / "backup"), "--report", str(report),
        "--lock", str(tmp_path / "write.lock"), "--apply",
    ])

    assert publish.main() == 0

    target = root / f"paid_minute_bars/year=2026/date={STAMP}/part.parquet"
    assert pl.read_parquet(target)["symbol"].unique().to_list() == ["300344.SZ"]
    assert pq.read_schema(target).metadata[b"provider"] == b"yuhaha-baidupan"
    summary = json.loads(report.read_text())
    assert summary["rejected"] == {"daily_volume": 1}
    assert summary["accepted_rows"] == 241
