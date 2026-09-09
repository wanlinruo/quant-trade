"""mootdx 日线分页。

计划 1-1 的单次 800 根上限把历史截断在约 3.2 年。实测 `bars(symbol, frequency,
start, offset)` 的 `start` 参数生效（「距最新第几根」）、分页间无重叠、可回溯至
2018 年，据此改为按需翻页。
"""

from datetime import date

import pandas as pd

from qsys.data.providers.mootdx_provider import MootdxProvider


class PagedClient:
    """模拟 mootdx：start 为「距最新第几根」，返回该页。"""

    def __init__(self, total_days: int = 2000, page_size: int = 800):
        self.total = total_days
        self.page_size = page_size
        self.calls: list[dict] = []
        base = pd.Timestamp("2026-09-08")
        # 最新在前：index 0 是最新一天
        self._days = [base - pd.Timedelta(days=i) for i in range(total_days)]

    def bars(self, symbol, frequency, start=0, offset=800):
        self.calls.append({"symbol": symbol, "start": start, "offset": offset})
        window = self._days[start : start + offset]
        if not window:
            return pd.DataFrame()
        window = list(reversed(window))  # mootdx 返回按时间升序
        n = len(window)
        return pd.DataFrame(
            {
                "datetime": [d.strftime("%Y-%m-%d %H:%M") for d in window],
                "open": [10.0] * n,
                "high": [10.5] * n,
                "low": [9.5] * n,
                "close": [10.2] * n,
                "vol": [1000.0] * n,
                "amount": [10200.0] * n,
            }
        )


def test_single_page_when_range_is_short():
    client = PagedClient()
    p = MootdxProvider(client=client, page_size=800)
    p.fetch_daily_bars("600036.SH", date(2026, 9, 1), date(2026, 9, 8))
    assert len(client.calls) == 1


def test_pages_until_the_start_date_is_covered():
    client = PagedClient(total_days=2000)
    p = MootdxProvider(client=client, page_size=800)
    p.fetch_daily_bars("600036.SH", date(2023, 1, 1), date(2026, 9, 8))
    assert len(client.calls) >= 2
    assert [c["start"] for c in client.calls[:2]] == [0, 800]


def test_pagination_produces_no_duplicate_dates():
    client = PagedClient(total_days=2000)
    p = MootdxProvider(client=client, page_size=800)
    df = p.fetch_daily_bars("600036.SH", date(2023, 1, 1), date(2026, 9, 8))
    assert df["dt"].n_unique() == df.height


def test_result_is_sorted_ascending():
    client = PagedClient(total_days=2000)
    df = MootdxProvider(client=client).fetch_daily_bars(
        "600036.SH", date(2023, 1, 1), date(2026, 9, 8)
    )
    assert df["dt"].to_list() == sorted(df["dt"].to_list())


def test_result_is_clipped_to_the_requested_range():
    client = PagedClient(total_days=2000)
    df = MootdxProvider(client=client).fetch_daily_bars(
        "600036.SH", date(2025, 1, 1), date(2025, 6, 30)
    )
    assert df["dt"].min() >= date(2025, 1, 1)
    assert df["dt"].max() <= date(2025, 6, 30)


def test_stops_when_data_runs_out():
    """标的上市不足请求区间时，翻到空页即停，不空转。"""
    client = PagedClient(total_days=300)
    p = MootdxProvider(client=client, page_size=800, max_pages=20)
    p.fetch_daily_bars("600036.SH", date(2015, 1, 1), date(2026, 9, 8))
    assert len(client.calls) <= 2


def test_max_pages_caps_the_work_and_records_truncation():
    client = PagedClient(total_days=100_000)
    p = MootdxProvider(client=client, page_size=800, max_pages=2)
    p.fetch_daily_bars("600036.SH", date(1995, 1, 1), date(2026, 9, 8))
    assert len(client.calls) == 2
    assert p.truncated, "翻满上限仍未覆盖时必须记录截断"


def test_no_truncation_recorded_when_range_is_covered():
    client = PagedClient(total_days=2000)
    p = MootdxProvider(client=client, page_size=800)
    p.fetch_daily_bars("600036.SH", date(2023, 1, 1), date(2026, 9, 8))
    assert p.truncated == []


def test_empty_first_page_returns_empty_frame():
    class Empty:
        def bars(self, **kw):
            return pd.DataFrame()

    df = MootdxProvider(client=Empty()).fetch_daily_bars(
        "600036.SH", date(2024, 1, 1), date(2024, 6, 30)
    )
    assert df.height == 0
    assert df.columns == ["dt", "open", "high", "low", "close", "volume", "amount"]


def test_page_size_is_configurable():
    client = PagedClient(total_days=1000)
    MootdxProvider(client=client, page_size=200).fetch_daily_bars(
        "600036.SH", date(2023, 1, 1), date(2026, 9, 8)
    )
    assert all(c["offset"] == 200 for c in client.calls)
