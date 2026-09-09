from datetime import date

import pandas as pd
import polars as pl
import pytest

from qsys.data.providers.mootdx_provider import MootdxProvider


class FakeClient:
    """模拟 mootdx Quotes 客户端。列名以 Task 5 探针录得的真实结构为准。

    只有第一页有数据，后续页为空——模拟一个历史很短的标的。分页由
    tests/data/test_mootdx_pagination.py 单独覆盖。
    """

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame
        self.calls: list[dict] = []

    def bars(self, symbol, frequency, start=0, offset=800):
        self.calls.append(
            {"symbol": symbol, "frequency": frequency, "start": start, "offset": offset}
        )
        return self._frame if start == 0 else pd.DataFrame()


@pytest.fixture
def frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [30.0, 30.5, 31.0],
            "close": [30.5, 31.0, 30.6],
            "high": [31.0, 31.5, 31.2],
            "low": [29.5, 30.0, 30.4],
            "vol": [1000.0, 1100.0, 900.0],
            "amount": [30500.0, 34100.0, 27540.0],
            "datetime": ["2024-01-02 15:00", "2024-01-03 15:00", "2024-01-04 15:00"],
            "volume": [1000.0, 1100.0, 900.0],
        }
    )


def test_returns_normalized_columns(frame):
    provider = MootdxProvider(client=FakeClient(frame))
    df = provider.fetch_daily_bars("600036.SH", date(2024, 1, 1), date(2024, 1, 31))
    assert df.columns == ["dt", "open", "high", "low", "close", "volume", "amount"]


def test_parses_dates_and_casts_volume_to_int(frame):
    provider = MootdxProvider(client=FakeClient(frame))
    df = provider.fetch_daily_bars("600036.SH", date(2024, 1, 1), date(2024, 1, 31))
    assert df["dt"].to_list()[0] == date(2024, 1, 2)
    assert df["volume"].dtype == pl.Int64
    assert df["volume"].to_list() == [1000, 1100, 900]


def test_filters_to_requested_range(frame):
    provider = MootdxProvider(client=FakeClient(frame))
    df = provider.fetch_daily_bars("600036.SH", date(2024, 1, 3), date(2024, 1, 3))
    assert df["dt"].to_list() == [date(2024, 1, 3)]


def test_strips_market_suffix_when_calling_client(frame):
    client = FakeClient(frame)
    MootdxProvider(client=client).fetch_daily_bars(
        "600036.SH", date(2024, 1, 1), date(2024, 1, 31)
    )
    assert client.calls[0]["symbol"] == "600036"
    assert client.calls[0]["frequency"] == 9


def test_empty_response_returns_empty_frame_with_columns():
    provider = MootdxProvider(client=FakeClient(pd.DataFrame()))
    df = provider.fetch_daily_bars("600036.SH", date(2024, 1, 1), date(2024, 1, 31))
    assert df.height == 0
    assert df.columns == ["dt", "open", "high", "low", "close", "volume", "amount"]


def test_none_response_returns_empty_frame():
    class NoneClient:
        def bars(self, **kwargs):
            return None

    provider = MootdxProvider(client=NoneClient())
    df = provider.fetch_daily_bars("600036.SH", date(2024, 1, 1), date(2024, 1, 31))
    assert df.height == 0


def test_provider_name():
    assert MootdxProvider(client=FakeClient(pd.DataFrame())).name == "mootdx"


def test_data_running_out_is_not_truncation(frame):
    """标的上市晚于请求起点不是截断——翻到空页即停，无告警。"""
    provider = MootdxProvider(client=FakeClient(frame))
    provider.fetch_daily_bars("600036.SH", date(2020, 1, 1), date(2024, 1, 31))
    assert provider.truncated == []


def test_truncated_history_is_reported(frame):
    """翻满分页上限仍未覆盖请求起点时必须显式告警，不能静默截断。"""

    class NeverEndingClient:
        def bars(self, symbol, frequency, start=0, offset=800):
            return frame

    provider = MootdxProvider(client=NeverEndingClient(), max_pages=3)
    provider.fetch_daily_bars("600036.SH", date(2020, 1, 1), date(2024, 1, 31))
    assert provider.truncated == [("600036.SH", date(2020, 1, 1), date(2024, 1, 2))]


def test_no_truncation_reported_when_history_reaches_start(frame):
    provider = MootdxProvider(client=FakeClient(frame))
    provider.fetch_daily_bars("600036.SH", date(2024, 1, 2), date(2024, 1, 31))
    assert provider.truncated == []
