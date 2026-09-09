"""派生数据的 PIT 访问契约。"""

from datetime import date, datetime, time

import pyarrow as pa
import pytest

from qsys.core.calendar import AShareCalendar
from qsys.data import schema
from qsys.data.market_feed import MarketFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

D = [date(2024, 1, d) for d in (2, 3, 4)]
SESSIONS = list(D)


def seed(warehouse: Warehouse) -> None:
    n = len(D)
    warehouse.write(
        "bars_daily",
        pa.table(
            {
                "symbol": ["600001.SH"] * n,
                "dt": list(D),
                "open": [10.0, 11.0, 12.0],
                "high": [10.5, 11.5, 12.5],
                "low": [9.5, 10.5, 11.5],
                "close": [10.2, 11.2, 12.2],
                "volume": [1000, 2000, 3000],
                "amount": [0.0] * n,
            },
            schema=schema.BARS_DAILY,
        ),
        year=2024,
    )
    warehouse.write(
        "daily_flags",
        pa.table(
            {
                "dt": list(D),
                "symbol": ["600001.SH"] * n,
                "prev_close": [None, 10.2, 11.2],
                "limit_up": [None, 11.22, 12.32],
                "limit_down": [None, 9.18, 10.08],
                "is_limit_up": [False, False, True],
                "is_limit_down": [False] * n,
                "touched_limit_up": [False, False, True],
                "is_broken_board": [False] * n,
                "is_first_board": [False, False, True],
            },
            schema=schema.DAILY_FLAGS,
        ),
        year=2024,
    )
    warehouse.write(
        "market_regime",
        pa.table(
            {
                "dt": list(D),
                "limit_up_count": [1, 2, 3],
                "limit_down_count": [0] * n,
                "limit_up_down_ratio": [1.0] * n,
                "touched_limit_up_count": [1, 2, 3],
                "broken_board_count": [0] * n,
                "broken_board_rate": [0.0] * n,
                "first_board_count": [1, 1, 1],
                "market_advance_ratio": [0.5] * n,
                "market_median_return_1d": [0.01] * n,
                "market_total_money": [1e10] * n,
                "prior_limitup_nextday_return_mean": [0.02] * n,
                "prior_limitup_nextday_return_median": [0.02] * n,
                "valid_stock_count": [100] * n,
                "index_ma20_slope_5d": [0.001] * n,
            },
            schema=schema.MARKET_REGIME,
        ),
        year=2024,
    )
    warehouse.write(
        "valuation_snapshot",
        pa.table(
            {
                "dt": list(D),
                "symbol": ["600001.SH"] * n,
                "close": [10.2, 11.2, 12.2],
                "market_cap": [100.0, 110.0, 120.0],
                "circulating_market_cap": [80.0, 88.0, 96.0],
                "turnover_ratio": [1.0] * n,
            },
            schema=schema.VALUATION_SNAPSHOT,
        ),
        year=2024,
    )
    warehouse.write(
        "minute_bars",
        pa.table(
            {
                "symbol": ["600001.SH"] * 2,
                "dt": [datetime(2024, 1, 4, 9, 30), datetime(2024, 1, 4, 9, 31)],
                "open": [12.0, 12.0],
                "high": [12.0, 12.3],
                "low": [12.0, 12.0],
                "close": [12.0, 12.3],
                "volume": [50, 300],
                "amount": [0.0] * 2,
            },
            schema=schema.MINUTE_BARS,
        ),
        year=2024,
    )


@pytest.fixture
def market(warehouse):
    seed(warehouse)
    return MarketFeed(WarehouseQuery(warehouse), AShareCalendar(SESSIONS))


def at(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute))


def test_flags_are_visible_only_after_that_session_closes(market):
    bound = market.bind(at(D[2], 9, 30))
    assert bound.flags(["600001.SH"], D[0], D[2])["dt"].max() == D[1]
    after = market.bind(at(D[2], 15, 0))
    assert after.flags(["600001.SH"], D[0], D[2])["dt"].max() == D[2]


def test_regime_follows_the_same_visibility_rule(market):
    assert market.bind(at(D[2], 9, 30)).regime(D[0], D[2])["dt"].max() == D[1]


def test_valuation_follows_the_same_visibility_rule(market):
    bound = market.bind(at(D[2], 9, 30))
    assert bound.valuation(["600001.SH"], D[0], D[2])["dt"].max() == D[1]


def test_session_open_is_visible_from_the_open(market):
    """open 在 09:30 就已确定——这正是策略判低开/高开的依据。"""
    assert market.bind(at(D[2], 9, 30)).session_open(["600001.SH"], D[2]) == {
        "600001.SH": 12.0
    }


def test_session_open_is_invisible_before_the_open(market):
    assert market.bind(at(D[2], 9, 29)).session_open(["600001.SH"], D[2]) == {}


def test_session_open_never_leaks_the_rest_of_the_bar(market):
    """只给 open。当日 close 12.2、high 12.5 都不该出现在返回值里。"""
    opens = market.bind(at(D[2], 9, 30)).session_open(["600001.SH"], D[2])
    assert set(opens.values()) == {12.0}


def test_auction_volume_comes_from_the_0930_bar(market):
    assert market.bind(at(D[2], 9, 30)).auction_volume(["600001.SH"], D[2]) == {
        "600001.SH": 50
    }


def test_auction_volume_is_invisible_before_the_open(market):
    assert market.bind(at(D[2], 9, 29)).auction_volume(["600001.SH"], D[2]) == {}


def test_auction_volume_is_empty_when_there_is_no_minute_data(market):
    """分钟线只覆盖最近 242 天，早期没有——返回空而不是 0。

    返回 0 会让「竞价量/昨日量 >= 2%」判成 False 而静默剔除候选；
    返回空则让调用方能显式区分「没数据」与「量不够」。
    """
    assert market.bind(at(D[0], 9, 30)).auction_volume(["600001.SH"], D[0]) == {}


def test_nothing_is_visible_before_any_session_closed(market):
    bound = market.bind(at(D[0], 9, 30))
    assert bound.flags(["600001.SH"], D[0], D[2]).height == 0
    assert bound.regime(D[0], D[2]).height == 0


def test_bound_market_does_not_take_another_as_of(market):
    bound = market.bind(at(D[2], 9, 30))
    with pytest.raises(TypeError):
        bound.flags(["600001.SH"], D[0], D[2], as_of=at(D[2], 15, 0))


# ---------------- 日内报价与涨停判定 ----------------


def test_intraday_returns_the_last_bar_at_or_before_the_moment(market):
    got = market.bind(at(D[2], 9, 31)).intraday(["600001.SH"], D[2], time(9, 31))
    assert got["600001.SH"].price == 12.3


def test_intraday_does_not_look_past_the_moment(market):
    """问 09:30 就只能拿到 09:30 那根，不能把 09:31 的价格给出去。"""
    got = market.bind(at(D[2], 15, 0)).intraday(["600001.SH"], D[2], time(9, 30))
    assert got["600001.SH"].price == 12.0


def test_intraday_beyond_as_of_is_refused(market):
    """as_of 是 09:31 时问 15:00 的价——必须拿不到。"""
    got = market.bind(at(D[2], 9, 31)).intraday(["600001.SH"], D[2], time(15, 0))
    assert got == {}


def test_intraday_accumulates_volume_up_to_the_moment(market):
    """成交量是截至该时刻的累计量，不是全日量——用于成交量上限。"""
    got = market.bind(at(D[2], 9, 31)).intraday(["600001.SH"], D[2], time(9, 31))
    assert got["600001.SH"].volume == 50 + 300


def test_intraday_without_minute_data_is_empty(market):
    assert market.bind(at(D[0], 15, 0)).intraday(["600001.SH"], D[0], time(11, 25)) == {}


def test_limit_up_price_is_visible_from_the_open(market):
    """涨停价由昨收算出，开盘前就已确定，不该锁到当日收盘。"""
    got = market.bind(at(D[2], 9, 30)).limit_up_price(["600001.SH"], D[2])
    assert got["600001.SH"] == 12.32


def test_limit_up_price_is_invisible_before_the_open(market):
    assert market.bind(at(D[2], 9, 29)).limit_up_price(["600001.SH"], D[2]) == {}


def test_limit_up_price_is_absent_without_a_prior_close(market):
    """首个 bar 没有前收，涨停价为 null——不能编一个出来。"""
    assert market.bind(at(D[0], 9, 30)).limit_up_price(["600001.SH"], D[0]) == {}
