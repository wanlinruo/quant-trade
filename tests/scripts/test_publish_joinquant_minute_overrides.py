import json
import sys
import types
from datetime import date

import pandas as pd
import polars as pl

from qsys.data import schema
from qsys.data.pipeline.minute_repairs import expected_joinquant_times, expected_minute_times
from scripts import publish_joinquant_minute_overrides as publish

DAY = date(2026, 3, 5)
OTHER_DAY = date(2026, 3, 12)


def _qmt_zero_prices(root, day, symbols):
    times = expected_minute_times(day)
    frame = pl.concat([
        pl.DataFrame({
            "symbol": [symbol] * len(times), "dt": times,
            "open": [0.0] * len(times), "high": [0.0] * len(times),
            "low": [0.0] * len(times), "close": [0.0] * len(times),
            "volume": [1] * len(times), "amount": [0.0] * len(times),
        })
        for symbol in symbols
    ])
    path = root / f"minute_bars/year={day.year}/date={day:%Y%m%d}/part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)


def _fake_jq(requests):
    """按聚宽 get_price(panel=False) 的形状返回每只 240 根、每根 100 股 10 元。"""
    module = types.ModuleType("jqdatasdk")
    module.auth = lambda *_args: None
    module.get_query_count = lambda field: 1000

    def get_price(codes, start_date, **_kwargs):
        day = date.fromisoformat(start_date[:10])
        requests.append((day, sorted(codes)))
        times = expected_joinquant_times(day)
        return pd.DataFrame([
            {"time": moment, "code": code, "open": 10.0, "high": 10.0, "low": 10.0,
             "close": 10.0, "volume": 100.0, "money": 1000.0}
            for code in codes for moment in times
        ])

    module.get_price = get_price
    return module


def test_publishes_only_requested_days_that_agree_with_daily(tmp_path, warehouse, monkeypatch):
    root = warehouse.root
    symbols = ["000004.SZ", "300029.SZ"]
    _qmt_zero_prices(root, DAY, symbols)
    _qmt_zero_prices(root, OTHER_DAY, symbols)
    rows = []
    for symbol, lots in (("000004.SZ", 240.0 / 77), ("300029.SZ", 240.0)):
        row = {field.name: 1.0 for field in list(schema.TUSHARE_DAILY)[3:]}
        row.update({"dt": DAY, "symbol": symbol, "name": symbol, "open": 10.0,
                    "high": 10.0, "low": 10.0, "close": 10.0, "volume": lots,
                    "amount": 240 * 1000.0})
        rows.append(row)
    warehouse.write("tushare_daily", pl.DataFrame(rows).select(schema.TUSHARE_DAILY.names)
                    .to_arrow().cast(schema.TUSHARE_DAILY), year=2026)
    requests: list = []
    monkeypatch.setitem(sys.modules, "jqdatasdk", _fake_jq(requests))
    config = tmp_path / "deploy.toml"
    config.write_text('[joinquant]\nusername = "u"\npassword = "p"\n')
    report = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", [
        "publish", "--warehouse", str(root), "--config", str(config),
        "--backup-dir", str(tmp_path / "backup"), "--report", str(report),
        "--lock", str(tmp_path / "write.lock"),
        "--start", str(DAY), "--end", str(DAY), "--apply",
    ])

    assert publish.main() == 0

    assert [day for day, _codes in requests] == [DAY]
    target = root / f"joinquant_minute_bars/year=2026/date={DAY:%Y%m%d}/part.parquet"
    assert pl.read_parquet(target)["symbol"].unique().to_list() == ["300029.SZ"]
    assert not (root / f"joinquant_minute_bars/year=2026/date={OTHER_DAY:%Y%m%d}").exists()
    summary = json.loads(report.read_text())
    assert summary["rejected"] == {"daily_volume": 1}
    assert summary["accepted_rows"] == 240
