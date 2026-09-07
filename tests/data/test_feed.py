from datetime import date, datetime

import pyarrow as pa
import pytest

from qsys.core.calendar import AShareCalendar
from qsys.data import schema
from qsys.data.feed import Adjustment, HistoricalFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

SESSIONS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
CAL = AShareCalendar(SESSIONS)


@pytest.fixture
def feed(warehouse: Warehouse) -> HistoricalFeed:
    """600036 在 2024-01-04 除权 10 送 10：不复权收盘 19.5 / 20.0 / 10.0 / 10.2。"""
    warehouse.write(
        "bars_daily",
        pa.table(
            {
                "symbol": ["600036.SH"] * 4,
                "dt": SESSIONS,
                "open": [19.0, 19.5, 10.0, 10.1],
                "high": [20.0, 20.5, 10.5, 10.6],
                "low": [18.5, 19.0, 9.8, 9.9],
                "close": [19.5, 20.0, 10.0, 10.2],
                "volume": [1000, 1000, 2000, 2000],
                "amount": [19500.0, 20000.0, 20000.0, 20400.0],
            },
            schema=schema.BARS_DAILY,
        ),
        year=2024,
    )
    warehouse.write(
        "adj_factors",
        pa.table(
            {
                "symbol": ["600036.SH"] * 4,
                "dt": SESSIONS,
                "cum_factor": [1.0, 1.0, 2.0, 2.0],
            },
            schema=schema.ADJ_FACTORS,
        ),
    )
    return HistoricalFeed(WarehouseQuery(warehouse), CAL)


def test_as_of_after_close_includes_that_day(feed):
    df = feed.get(["600036.SH"], SESSIONS[0], SESSIONS[-1], as_of=datetime(2024, 1, 3, 15, 0))
    assert df["dt"].to_list() == [date(2024, 1, 2), date(2024, 1, 3)]


def test_as_of_before_close_excludes_that_day(feed):
    """PIT 反向测试：盘中不得看到当日日线。"""
    df = feed.get(["600036.SH"], SESSIONS[0], SESSIONS[-1], as_of=datetime(2024, 1, 3, 9, 30))
    assert df["dt"].to_list() == [date(2024, 1, 2)]


def test_as_of_cannot_see_the_future(feed):
    """偷看未来的策略必须拿不到数据。"""
    df = feed.get(["600036.SH"], SESSIONS[0], SESSIONS[-1], as_of=datetime(2024, 1, 2, 15, 0))
    assert date(2024, 1, 3) not in df["dt"].to_list()
    assert df.height == 1


def test_as_of_before_all_data_returns_empty(feed):
    df = feed.get(["600036.SH"], SESSIONS[0], SESSIONS[-1], as_of=datetime(2024, 1, 1, 15, 0))
    assert df.height == 0
    assert "close" in df.columns


def test_end_beyond_as_of_is_clipped_not_an_error(feed):
    df = feed.get(["600036.SH"], SESSIONS[0], date(2030, 1, 1), as_of=datetime(2024, 1, 3, 15, 0))
    assert df["dt"].to_list()[-1] == date(2024, 1, 3)


def test_no_adjustment_returns_raw_prices(feed):
    df = feed.get(["600036.SH"], SESSIONS[0], SESSIONS[-1], as_of=datetime(2024, 1, 5, 15, 0))
    assert df["close"].to_list() == [19.5, 20.0, 10.0, 10.2]


def test_backward_adjustment_is_continuous_across_ex_date(feed):
    df = feed.get(
        ["600036.SH"], SESSIONS[0], SESSIONS[-1],
        as_of=datetime(2024, 1, 5, 15, 0), adjustment=Adjustment.BACKWARD,
    )
    closes = df["close"].to_list()
    assert closes[1] == pytest.approx(20.0)
    assert closes[2] == pytest.approx(20.0)


def test_forward_adjustment_anchors_on_last_visible_bar(feed):
    """前复权以 as_of 时可见的最后一根 bar 为基准，其价格保持不变。"""
    df = feed.get(
        ["600036.SH"], SESSIONS[0], SESSIONS[-1],
        as_of=datetime(2024, 1, 5, 15, 0), adjustment=Adjustment.FORWARD,
    )
    closes = df["close"].to_list()
    assert closes[-1] == pytest.approx(10.2)
    assert closes[1] == pytest.approx(10.0)


def test_adjustment_scales_ohlc_but_not_volume(feed):
    df = feed.get(
        ["600036.SH"], SESSIONS[0], SESSIONS[-1],
        as_of=datetime(2024, 1, 5, 15, 0), adjustment=Adjustment.BACKWARD,
    )
    assert df["open"].to_list()[0] == pytest.approx(19.0)
    assert df["volume"].to_list() == [1000, 1000, 2000, 2000]


def test_get_requires_as_of_keyword(feed):
    with pytest.raises(TypeError):
        feed.get(["600036.SH"], SESSIONS[0], SESSIONS[-1])


def test_unknown_symbol_returns_empty_frame(feed):
    df = feed.get(["999999.SH"], SESSIONS[0], SESSIONS[-1], as_of=datetime(2024, 1, 5, 15, 0))
    assert df.height == 0
