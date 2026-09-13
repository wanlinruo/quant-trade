"""Tushare provider 只规范化类型，不承载单位换算。"""

from datetime import date

import polars as pl

from qsys.data.providers.tushare import TushareProvider


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def call(self, api_name, params, fields=""):
        self.calls.append((api_name, params, fields))
        return self.rows


def test_daily_passes_compact_trade_date_and_preserves_values():
    client = FakeClient([{"ts_code": "000001.SZ", "vol": "582305.98",
                          "amount": "682946.78266"}])
    got = TushareProvider(client).daily(date(2026, 9, 11))
    assert client.calls == [("daily", {"trade_date": "20260911"}, "")]
    assert got.schema["vol"] == pl.Float64
    assert got["vol"].to_list() == [582305.98]
    assert got["amount"].to_list() == [682946.78266]


def test_daily_basic_calls_the_declared_interface():
    client = FakeClient([{"ts_code": "000001.SZ", "total_share": "1.5"}])
    got = TushareProvider(client).daily_basic(date(2026, 9, 11))
    assert client.calls == [("daily_basic", {"trade_date": "20260911"}, "")]
    assert got["total_share"].to_list() == [1.5]


def test_adj_factor_calls_the_declared_interface():
    client = FakeClient([{"ts_code": "000001.SZ", "adj_factor": "139.008"}])
    got = TushareProvider(client).adj_factor(date(2026, 9, 11))
    assert client.calls == [("adj_factor", {"trade_date": "20260911"}, "")]
    assert got["adj_factor"].to_list() == [139.008]


def test_empty_result_stays_empty():
    got = TushareProvider(FakeClient([])).daily(date(2026, 9, 11))
    assert got.is_empty()


def test_numeric_type_inference_scans_every_upstream_row():
    """早期历史里同一比率列会先出现整数、百行后才出现小数，不能采样推断。"""
    rows = [
        {"ts_code": f"{i:06d}.SZ", "pe": 1}
        for i in range(100)
    ]
    rows.append({"ts_code": "600000.SH", "pe": 1.204})
    got = TushareProvider(FakeClient(rows)).daily_basic(date(1995, 4, 20))
    assert got["pe"].tail(1).to_list() == [1.204]
