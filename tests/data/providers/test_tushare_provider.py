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


def test_daily_is_cached_per_provider_and_day():
    client = FakeClient([{"ts_code": "000001.SZ", "vol": "1.5"}])
    provider = TushareProvider(client)
    first = provider.daily(date(2026, 9, 11))
    first.clear()
    second = provider.daily(date(2026, 9, 11))
    assert second.height == 1
    assert len(client.calls) == 1


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


def test_reference_daily_interfaces_use_compact_dates():
    day = date(2026, 9, 11)
    client = FakeClient([])
    provider = TushareProvider(client)
    provider.stk_limit(day)
    provider.suspend_d(day)
    provider.stock_st(day)
    provider.limit_list_d(day, "U")
    assert client.calls == [
        ("stk_limit", {"trade_date": "20260911"}, ""),
        ("suspend_d", {"trade_date": "20260911"}, ""),
        ("stock_st", {"trade_date": "20260911"},
         "ts_code,name,trade_date,type,type_name"),
        ("limit_list_d", {"trade_date": "20260911", "limit_type": "U"}, ""),
    ]


def test_index_daily_passes_code_and_range():
    client = FakeClient([])
    TushareProvider(client).index_daily(
        "000300.SH", date(2005, 1, 1), date(2026, 9, 11)
    )
    assert client.calls == [(
        "index_daily",
        {"ts_code": "000300.SH", "start_date": "20050101", "end_date": "20260911"},
        "",
    )]


def test_static_reference_interfaces_request_declared_fields():
    client = FakeClient([])
    provider = TushareProvider(client)
    provider.stock_basic("D")
    provider.namechange()
    assert client.calls == [
        ("stock_basic", {"exchange": "", "list_status": "D"},
         "ts_code,symbol,name,area,industry,list_date,delist_date"),
        ("namechange", {"offset": 0, "limit": 10000},
         "ts_code,name,start_date,end_date,ann_date,change_reason"),
    ]


def test_trade_calendar_requests_closed_days_too():
    client = FakeClient([])
    TushareProvider(client).trade_cal(
        date(2026, 9, 1), date(2026, 9, 30), exchange="SSE"
    )
    assert client.calls == [(
        "trade_cal",
        {"exchange": "SSE", "start_date": "20260901", "end_date": "20260930"},
        "exchange,cal_date,is_open,pretrade_date",
    )]


def test_namechange_reads_past_the_ten_thousand_row_api_page():
    class PagedClient:
        def __init__(self):
            self.offsets = []

        def call(self, api_name, params, fields=""):
            assert api_name == "namechange"
            self.offsets.append(params["offset"])
            if params["offset"] == 0:
                return [{"ts_code": f"{i:06d}.SZ"} for i in range(10_000)]
            return [{"ts_code": "600000.SH"}]

    client = PagedClient()
    got = TushareProvider(client).namechange()
    assert got.height == 10_001
    assert client.offsets == [0, 10_000]


def test_first_board_provider_moneyflow_uses_explicit_fields():
    client = FakeClient([{
        "ts_code": "600001.SH", "trade_date": "20260911",
        "buy_sm_amount": 1, "sell_sm_amount": 2, "buy_md_amount": 3,
        "sell_md_amount": 4, "buy_lg_amount": 5, "sell_lg_amount": 6,
        "buy_elg_amount": 7, "sell_elg_amount": 8, "net_mf_amount": -4,
    }])
    got = TushareProvider(client).moneyflow(date(2026, 9, 11))
    assert got["buy_sm_amount"].dtype == pl.Float64
    assert client.calls[0][0] == "moneyflow"
