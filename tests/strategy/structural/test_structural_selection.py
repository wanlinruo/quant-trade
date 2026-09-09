"""选股组装：路由顺序、竞价量两版、Gate 串联。"""

from datetime import date, datetime, time, timedelta

import pyarrow as pa
import pytest

from qsys.core.calendar import AShareCalendar
from qsys.data import schema
from qsys.data.feed import HistoricalFeed
from qsys.data.market_feed import MarketFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.strategy.structural.selection import StructuralSelection

# 需要 101 根历史，构造 120 个连续交易日
START = date(2024, 1, 2)
DAYS = [START + timedelta(days=i) for i in range(120)]
YESTERDAY, TODAY = DAYS[-2], DAYS[-1]


def build(warehouse: Warehouse, symbol, closes, highs, lows, amounts, volumes, opens,
          first_board_on=None, broken_on=None):
    n = len(DAYS)
    warehouse.write("bars_daily", pa.table({
        "symbol": [symbol] * n, "dt": list(DAYS),
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": volumes, "amount": amounts,
    }, schema=schema.BARS_DAILY), year=2024)
    warehouse.write("daily_flags", pa.table({
        "dt": list(DAYS), "symbol": [symbol] * n,
        "prev_close": [10.0] * n, "limit_up": [11.0] * n, "limit_down": [9.0] * n,
        "is_limit_up": [d == first_board_on for d in DAYS],
        "is_limit_down": [False] * n,
        "touched_limit_up": [d in (first_board_on, broken_on) for d in DAYS],
        "is_broken_board": [d == broken_on for d in DAYS],
        "is_first_board": [d == first_board_on for d in DAYS],
    }, schema=schema.DAILY_FLAGS), year=2024)


def write_regime(warehouse, *, slope=-0.5, heat=-0.5, breadth=0.5,
                 touched=200.0, advance=0.9):
    n = len(DAYS)
    warehouse.write("market_regime", pa.table({
        "dt": list(DAYS), "limit_up_count": [1] * n, "limit_down_count": [1] * n,
        "limit_up_down_ratio": [1.0] * n,
        "touched_limit_up_count": [int(touched)] * n,
        "broken_board_count": [0] * n, "broken_board_rate": [0.0] * n,
        "first_board_count": [1] * n, "market_advance_ratio": [advance] * n,
        "market_median_return_1d": [breadth] * n, "market_total_money": [1e10] * n,
        "prior_limitup_nextday_return_mean": [heat] * n,
        "prior_limitup_nextday_return_median": [heat] * n,
        "valid_stock_count": [100] * n, "index_ma20_slope_5d": [slope] * n,
    }, schema=schema.MARKET_REGIME), year=2024)


def write_valuation(warehouse, symbol, market_cap=100.0, circulating=300.0):
    n = len(DAYS)
    warehouse.write("valuation_snapshot", pa.table({
        "dt": list(DAYS), "symbol": [symbol] * n, "close": [10.0] * n,
        "market_cap": [market_cap] * n,
        "circulating_market_cap": [circulating] * n,
        "turnover_ratio": [1.0] * n,
    }, schema=schema.VALUATION_SNAPSHOT), year=2024)


def write_auction(warehouse, symbol, day, volume):
    warehouse.write("minute_bars", pa.table({
        "symbol": [symbol],
        "dt": [datetime.combine(day, time(9, 30))],
        "open": [10.0], "high": [10.0], "low": [10.0], "close": [10.0],
        "volume": [volume], "amount": [0.0],
    }, schema=schema.MINUTE_BARS), year=2024)


def low_setup(warehouse, symbol="600001.SH", today_open=9.6):
    """构造一只能通过 check_low_open 的票：相对位置低、成交额足、低开 4%。"""
    n = len(DAYS)
    closes = [12.0] * (n - 1) + [10.0]
    closes[-2] = 10.0  # 昨日收盘 10.0
    build(
        warehouse, symbol,
        closes=closes,
        highs=[14.0] * n, lows=[9.5] * n,
        amounts=[2e8] * n, volumes=[1000] * n,
        opens=[12.0] * (n - 1) + [today_open],
        first_board_on=YESTERDAY,
    )
    write_regime(warehouse)


def selector(warehouse, **kw):
    query = WarehouseQuery(warehouse)
    calendar = AShareCalendar(DAYS)
    return StructuralSelection(
        HistoricalFeed(query, calendar), MarketFeed(query, calendar), query, **kw
    )


def test_low_branch_selects_a_valid_candidate(warehouse):
    low_setup(warehouse)
    picks = selector(warehouse, require_auction_volume=False).select(TODAY, YESTERDAY)
    assert [p.symbol for p in picks] == ["600001.SH"]
    assert picks[0].tag == "LOW_MAIN"


def test_low_main_needs_the_auction_volume_filter_when_required(warehouse):
    """忠实版：没有分钟线就拿不到竞价量，该只整只剔除。"""
    low_setup(warehouse)
    assert selector(warehouse, require_auction_volume=True).select(TODAY, YESTERDAY) == []


def test_low_main_passes_when_the_auction_volume_is_enough(warehouse):
    low_setup(warehouse)
    write_auction(warehouse, "600001.SH", TODAY, volume=30)  # 30/1000 = 3% >= 2%
    picks = selector(warehouse, require_auction_volume=True).select(TODAY, YESTERDAY)
    assert [p.symbol for p in picks] == ["600001.SH"]


def test_low_main_is_dropped_when_the_auction_volume_is_thin(warehouse):
    low_setup(warehouse)
    write_auction(warehouse, "600001.SH", TODAY, volume=10)  # 1% < 2%
    assert selector(warehouse, require_auction_volume=True).select(TODAY, YESTERDAY) == []


def test_low_chinext_skips_the_auction_volume_filter(warehouse):
    """Stage4 附加过滤只作用于 LOW_MAIN，创业板不受影响。"""
    low_setup(warehouse, symbol="300001.SZ")
    picks = selector(warehouse, require_auction_volume=True).select(TODAY, YESTERDAY)
    assert [p.tag for p in picks] == ["LOW_CHINEXT"]


def test_a_gated_out_candidate_is_not_returned(warehouse):
    """LOW_MAIN 判成 RISK 时被 S7 Gate 拒掉。"""
    low_setup(warehouse)
    write_regime(warehouse, slope=0.5, heat=0.5)  # 趋势不弱且情绪高 -> RISK
    picks = selector(warehouse, require_auction_volume=False).select(TODAY, YESTERDAY)
    assert picks == []


def test_gate_decisions_are_recorded_even_when_rejected(warehouse):
    """拒了也要留痕，否则对账时说不清是没选中还是被 Gate 拦了。"""
    low_setup(warehouse)
    write_regime(warehouse, slope=0.5, heat=0.5)
    sel = selector(warehouse, require_auction_volume=False)
    sel.select(TODAY, YESTERDAY)
    assert [d.state for d in sel.decisions] == ["RISK"]
    assert sel.decisions[0].allow is False


def test_a_non_first_board_is_not_a_candidate(warehouse):
    low_setup(warehouse)
    # 把首板标记挪到更早的一天
    build(
        warehouse, "600001.SH",
        closes=[12.0] * (len(DAYS) - 1) + [10.0],
        highs=[14.0] * len(DAYS), lows=[9.5] * len(DAYS),
        amounts=[2e8] * len(DAYS), volumes=[1000] * len(DAYS),
        opens=[12.0] * (len(DAYS) - 1) + [9.6],
        first_board_on=DAYS[10],
    )
    assert selector(warehouse, require_auction_volume=False).select(TODAY, YESTERDAY) == []


def test_no_candidates_on_an_empty_warehouse(warehouse):
    query = WarehouseQuery(warehouse)
    calendar = AShareCalendar(DAYS)
    sel = StructuralSelection(
        HistoricalFeed(query, calendar), MarketFeed(query, calendar), query
    )
    assert sel.select(TODAY, YESTERDAY) == []
