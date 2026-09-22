"""派生数据的 PIT 访问契约。"""

from datetime import date, datetime, time

import polars as pl
import pyarrow as pa
import pytest

from qsys.core.calendar import AShareCalendar
from qsys.data import schema
from qsys.data.market_feed import MarketFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.core.types import Symbol
from qsys.strategy.deps import MissingIntradayDataError, intraday_quotes

D = [date(2024, 1, d) for d in (2, 3, 4)]
SESSIONS = list(D)


def seed(warehouse: Warehouse, write_daily_bars) -> None:
    n = len(D)
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["600001.SH"] * n,
        "dt": list(D),
        "open": [10.0, 11.0, 12.0],
        "high": [10.5, 11.5, 12.5],
        "low": [9.5, 10.5, 11.5],
        "close": [10.2, 11.2, 12.2],
        "volume": [1000.0, 2000.0, 3000.0],
        "amount": [0.0] * n,
    }))
    warehouse.write(
        "daily_flags",
        pa.table(
            {
                "dt": list(D),
                "symbol": ["600001.SH"] * n,
                "prev_close": [None, 10.2, 11.2],
                "adj_prev_close": [None, 10.2, 11.2],
                "limit_up": [None, 11.22, 12.32],
                "limit_down": [None, 9.18, 10.08],
                "is_limit_up": [False, False, True],
                "is_limit_down": [False] * n,
                "touched_limit_up": [False, False, True],
                "is_broken_board": [False] * n,
                "is_first_board": [False, False, True],
                "limit_pct": [0.10] * n,
                "is_st": [False] * n,
                "st_source": ["paid"] * n,
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
def market(warehouse, write_daily_bars):
    seed(warehouse, write_daily_bars)
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


def _before_match(day):
    """09:25 撮合结果 09:25:05 才可见（研究文档第 5 节）。"""
    return datetime.combine(day, time(9, 25, 4))


def _after_match(day):
    return datetime.combine(day, time(9, 25, 5))


def test_session_open_is_invisible_before_the_auction_match(market):
    assert market.bind(_before_match(D[2])).session_open(["600001.SH"], D[2]) == {}


def test_session_open_is_visible_right_after_the_auction_match(market):
    assert market.bind(_after_match(D[2])).session_open(["600001.SH"], D[2]) == {"600001.SH": 12.0}


def test_session_open_never_leaks_the_rest_of_the_bar(market):
    """只给 open。当日 close 12.2、high 12.5 都不该出现在返回值里。"""
    opens = market.bind(at(D[2], 9, 30)).session_open(["600001.SH"], D[2])
    assert set(opens.values()) == {12.0}


def test_auction_volume_comes_from_the_0930_bar(market):
    assert market.bind(at(D[2], 9, 30)).auction_volume(["600001.SH"], D[2]) == {
        "600001.SH": 50
    }


def test_auction_volume_is_invisible_before_the_auction_match(market):
    assert market.bind(_before_match(D[2])).auction_volume(["600001.SH"], D[2]) == {}


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


def test_backtest_adapter_fails_when_tradable_holding_has_no_minutes(market):
    with pytest.raises(MissingIntradayDataError, match="可交易持仓缺少分钟行情"):
        intraday_quotes(market)(D[0], time(11, 25), [Symbol.parse("600001.SH")])


def test_backtest_adapter_rejects_qmt_rows_with_zero_trade_price(
    warehouse, write_daily_bars
):
    seed(warehouse, write_daily_bars)
    warehouse.write_day("minute_bars", pa.table({
        "symbol": ["600001.SH"], "dt": [datetime(2024, 1, 2, 11, 25)],
        "open": [0.0], "high": [0.0], "low": [0.0], "close": [0.0],
        "volume": [100], "amount": [0.0],
    }, schema=schema.MINUTE_BARS), day=D[0])
    broken = MarketFeed(WarehouseQuery(warehouse), AShareCalendar(SESSIONS))

    with pytest.raises(MissingIntradayDataError, match="可交易持仓缺少分钟行情"):
        intraday_quotes(broken)(D[0], time(11, 25), [Symbol.parse("600001.SH")])


def test_joinquant_override_replaces_invalid_qmt_symbol_day(warehouse):
    warehouse.write_day("minute_bars", pa.table({
        "symbol": ["300344.SZ"], "dt": [datetime(2024, 1, 2, 11, 25)],
        "open": [0.0], "high": [0.0], "low": [0.0], "close": [0.0],
        "volume": [100], "amount": [0.0],
    }, schema=schema.MINUTE_BARS), day=D[0])
    target = warehouse.root / "joinquant_minute_bars/year=2024/date=20240102"
    target.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["300344.SZ"], "dt": [datetime(2024, 1, 2, 11, 25)],
        "open": [1.8], "high": [1.9], "low": [1.8], "close": [1.9],
        "volume": [10_000], "amount": [19_000.0],
    }).write_parquet(target / "part.parquet")

    got = WarehouseQuery(warehouse).intraday_bars(["300344.SZ"], D[0])
    assert got.select("source", "close", "volume").row(0) == ("joinquant", 1.9, 100.0)


def test_published_paid_minutes_are_preferred_and_volume_is_normalized(
    warehouse, write_daily_bars
):
    seed(warehouse, write_daily_bars)
    target = warehouse.root / "paid_minute_bars/year=2024/date=20240104"
    target.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["600001.SH", "600001.SH"],
        "dt": [datetime(2024, 1, 4, 9, 30), datetime(2024, 1, 4, 9, 31)],
        "open": [20.0, 20.0], "high": [20.0, 20.5],
        "low": [20.0, 20.0], "close": [20.0, 20.5],
        "volume": [5_000, 30_000], "amount": [100_000.0, 615_000.0],
        "factor": [1.0, 1.0],
    }).write_parquet(target / "part.parquet")
    paid_market = MarketFeed(WarehouseQuery(warehouse), AShareCalendar(SESSIONS))

    got = paid_market.bind(at(D[2], 9, 32)).intraday(
        ["600001.SH"], D[2], time(9, 32)
    )["600001.SH"]
    assert got.price == 20.5  # 不是同年 QMT 的 12.3
    assert got.volume == 350  # 35,000 股统一成 350 手


def test_limit_up_price_is_visible_from_the_open(market):
    """涨停价由昨收算出，开盘前就已确定，不该锁到当日收盘。"""
    got = market.bind(at(D[2], 9, 30)).limit_up_price(["600001.SH"], D[2])
    assert got["600001.SH"] == 12.32


def test_limit_up_price_is_invisible_before_the_auction_match(market):
    assert market.bind(_before_match(D[2])).limit_up_price(["600001.SH"], D[2]) == {}


def test_limit_up_price_is_absent_without_a_prior_close(market):
    """首个 bar 没有前收，涨停价为 null——不能编一个出来。"""
    assert market.bind(at(D[0], 9, 30)).limit_up_price(["600001.SH"], D[0]) == {}
