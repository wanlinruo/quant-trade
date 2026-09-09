"""分钟线 PIT 契约：策略在 09:31 看不到 14:50。"""

from datetime import date, datetime

import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.minute_feed import MinuteFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

DAY = date(2024, 1, 2)


def seed(warehouse: Warehouse, symbol="600036.SH") -> None:
    """09:31 ~ 09:35 与 14:57 ~ 15:00 各若干根，价格递增便于断言。"""
    times = [datetime(2024, 1, 2, 9, 30 + i) for i in range(1, 6)]
    times += [
        datetime(2024, 1, 2, 14, 57),
        datetime(2024, 1, 2, 14, 58),
        datetime(2024, 1, 2, 14, 59),
        datetime(2024, 1, 2, 15, 0),
    ]
    n = len(times)
    warehouse.write(
        "minute_bars",
        pa.table(
            {
                "symbol": [symbol] * n,
                "dt": times,
                "open": [float(i) for i in range(n)],
                "high": [float(i) for i in range(n)],
                "low": [float(i) for i in range(n)],
                "close": [float(i) for i in range(n)],
                "volume": [100 + i for i in range(n)],
                "amount": [0.0] * n,
            },
            schema=schema.MINUTE_BARS,
        ),
        year=2024,
    )


@pytest.fixture
def feed(warehouse):
    seed(warehouse)
    return MinuteFeed(WarehouseQuery(warehouse))


def test_returns_bars_up_to_as_of(feed):
    df = feed.get(
        ["600036.SH"],
        datetime(2024, 1, 2, 9, 0),
        datetime(2024, 1, 2, 15, 0),
        as_of=datetime(2024, 1, 2, 9, 33),
    )
    assert df["dt"].to_list() == [
        datetime(2024, 1, 2, 9, 31),
        datetime(2024, 1, 2, 9, 32),
        datetime(2024, 1, 2, 9, 33),
    ]


def test_a_bar_is_visible_exactly_at_its_close_time(feed):
    """09:30–09:31 这根记 09:31，在 09:31 整可见，09:30:59 不可见。"""
    at_close = feed.get(
        ["600036.SH"],
        datetime(2024, 1, 2, 9, 0),
        datetime(2024, 1, 2, 15, 0),
        as_of=datetime(2024, 1, 2, 9, 31),
    )
    assert at_close.height == 1
    before = feed.get(
        ["600036.SH"],
        datetime(2024, 1, 2, 9, 0),
        datetime(2024, 1, 2, 15, 0),
        as_of=datetime(2024, 1, 2, 9, 30, 59),
    )
    assert before.height == 0


def test_peeking_at_the_close_from_the_morning_returns_nothing(feed):
    """反向测试：早盘想看尾盘价，必须什么都拿不到。"""
    df = feed.get(
        ["600036.SH"],
        datetime(2024, 1, 2, 14, 50),
        datetime(2024, 1, 2, 15, 0),
        as_of=datetime(2024, 1, 2, 9, 35),
    )
    assert df.height == 0


def test_as_of_before_all_data_returns_nothing(feed):
    df = feed.get(
        ["600036.SH"],
        datetime(2024, 1, 2, 0, 0),
        datetime(2024, 1, 2, 23, 59),
        as_of=datetime(2024, 1, 1, 15, 0),
    )
    assert df.height == 0


def test_requested_end_still_caps_the_result(feed):
    """as_of 很晚也不该越过请求的 end。"""
    df = feed.get(
        ["600036.SH"],
        datetime(2024, 1, 2, 9, 0),
        datetime(2024, 1, 2, 9, 32),
        as_of=datetime(2024, 1, 3, 15, 0),
    )
    assert df["dt"].to_list() == [
        datetime(2024, 1, 2, 9, 31),
        datetime(2024, 1, 2, 9, 32),
    ]


def test_session_bars_covers_one_trading_day(feed):
    df = feed.session_bars(["600036.SH"], DAY, as_of=datetime(2024, 1, 2, 15, 0))
    assert df.height == 9


def test_session_bars_respects_as_of(feed):
    df = feed.session_bars(["600036.SH"], DAY, as_of=datetime(2024, 1, 2, 9, 33))
    assert df.height == 3


def test_first_bar_is_the_auction_bar(feed):
    """首根分钟线含集合竞价成交量——策略判高开/低开的依据。"""
    row = feed.first_bar(["600036.SH"], DAY, as_of=datetime(2024, 1, 2, 9, 35))
    assert row.height == 1
    assert row["dt"].to_list() == [datetime(2024, 1, 2, 9, 31)]


def test_first_bar_is_invisible_before_it_closes(feed):
    row = feed.first_bar(["600036.SH"], DAY, as_of=datetime(2024, 1, 2, 9, 30))
    assert row.height == 0


def test_first_bar_returns_one_row_per_symbol(warehouse, tmp_path):
    """多只票各取各的首根，不是整体第一根。"""
    import pyarrow.parquet as pq

    incoming = tmp_path / "in"
    incoming.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "symbol": ["600036.SH", "600036.SH", "000001.SZ", "000001.SZ"],
                "dt": [
                    datetime(2024, 1, 2, 9, 31),
                    datetime(2024, 1, 2, 9, 32),
                    datetime(2024, 1, 2, 9, 31),
                    datetime(2024, 1, 2, 9, 32),
                ],
                "open": [10.0, 10.1, 7.0, 7.1],
                "high": [10.0, 10.1, 7.0, 7.1],
                "low": [10.0, 10.1, 7.0, 7.1],
                "close": [10.0, 10.1, 7.0, 7.1],
                "volume": [10, 20, 30, 40],
                "amount": [0.0] * 4,
            },
            schema=schema.MINUTE_BARS,
        ),
        incoming / "a.parquet",
    )
    from qsys.data.pipeline.minutes import import_minute_bars

    import_minute_bars(warehouse, incoming)

    feed = MinuteFeed(WarehouseQuery(warehouse))
    rows = feed.first_bar(
        ["600036.SH", "000001.SZ"], DAY, as_of=datetime(2024, 1, 2, 15, 0)
    )
    assert rows.height == 2
    assert set(rows["symbol"].to_list()) == {"600036.SH", "000001.SZ"}
    assert sorted(rows["open"].to_list()) == [7.0, 10.0]


def test_empty_symbol_list_returns_nothing(feed):
    df = feed.get(
        [],
        datetime(2024, 1, 2, 9, 0),
        datetime(2024, 1, 2, 15, 0),
        as_of=datetime(2024, 1, 2, 15, 0),
    )
    assert df.height == 0
