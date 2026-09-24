"""选股组装：路由顺序、竞价量两版、Gate 串联。"""

from datetime import date, datetime, time, timedelta

import polars as pl
import pyarrow as pa
import pytest

from qsys.core.calendar import AShareCalendar
from qsys.data import schema
from qsys.data.feed import HistoricalFeed
from qsys.data.market_feed import MarketFeed
from qsys.data.pipeline.delisting import build_delisting_pool
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.strategy.structural.selection import (
    MissingAuctionDataError,
    StructuralSelection,
)

# 需要 101 根历史，构造 120 个连续交易日
START = date(2024, 1, 2)
DAYS = [START + timedelta(days=i) for i in range(120)]
YESTERDAY, TODAY = DAYS[-2], DAYS[-1]


def build(warehouse: Warehouse, write_daily_bars, symbol, closes, highs, lows, amounts,
          volumes, opens, first_board_on=None, broken_on=None):
    n = len(DAYS)
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": [symbol] * n, "dt": list(DAYS),
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": [float(v) for v in volumes], "amount": amounts,
    }))
    warehouse.write("daily_flags", pa.table({
        "dt": list(DAYS), "symbol": [symbol] * n,
        "prev_close": [10.0] * n, "adj_prev_close": [10.0] * n,
        "limit_up": [11.0] * n, "limit_down": [9.0] * n,
        "is_limit_up": [d == first_board_on for d in DAYS],
        "is_limit_down": [False] * n,
        "touched_limit_up": [d in (first_board_on, broken_on) for d in DAYS],
        "is_broken_board": [d == broken_on for d in DAYS],
        "is_first_board": [d == first_board_on for d in DAYS],
        "limit_pct": [0.10] * n,
        "is_st": [False] * n,
        "st_source": ["paid"] * n,
    }, schema=schema.DAILY_FLAGS), year=2024)
    warehouse.write("adj_factors", pa.table({
        "symbol": [symbol] * n, "dt": list(DAYS),
        "cum_factor": [1.0] * n, "cum_share_factor": [1.0] * n,
    }, schema=schema.ADJ_FACTORS))


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


def low_setup(warehouse, write_daily_bars, symbol="600001.SH", today_open=9.6):
    """构造一只能通过 check_low_open 的票：相对位置低、成交额足、低开 4%。"""
    n = len(DAYS)
    closes = [12.0] * (n - 1) + [10.0]
    closes[-2] = 10.0  # 昨日收盘 10.0
    build(
        warehouse, write_daily_bars, symbol,
        closes=closes,
        highs=[14.0] * n, lows=[9.5] * n,
        amounts=[2e8] * n, volumes=[1000] * n,
        opens=[12.0] * (n - 1) + [today_open],
        first_board_on=YESTERDAY,
    )
    write_regime(warehouse)


def selector(warehouse, **kw):
    # 候选池剔除只读退市整理期池，与生产一样先建好（此处没有整理期标的）
    build_delisting_pool(warehouse, DAYS[0], DAYS[-1], extra_sessions=DAYS)
    query = WarehouseQuery(warehouse)
    calendar = AShareCalendar(DAYS)
    return StructuralSelection(
        HistoricalFeed(query, calendar), MarketFeed(query, calendar), query, **kw
    )


def test_low_branch_selects_a_valid_candidate(warehouse, write_daily_bars):
    low_setup(warehouse, write_daily_bars)
    picks = selector(warehouse, require_auction_volume=False).select(TODAY, YESTERDAY)
    assert [p.symbol for p in picks] == ["600001.SH"]
    assert picks[0].tag == "LOW_MAIN"


def test_low_main_needs_the_auction_volume_filter_when_required(warehouse, write_daily_bars):
    """忠实版：缺竞价量必须失败，不能静默剔除并产出失真的低交易报告。"""
    low_setup(warehouse, write_daily_bars)
    with pytest.raises(MissingAuctionDataError, match="严格回测的候选缺少集合竞价成交量"):
        selector(warehouse, require_auction_volume=True).select(TODAY, YESTERDAY)


def test_low_main_passes_when_the_auction_volume_is_enough(warehouse, write_daily_bars):
    low_setup(warehouse, write_daily_bars)
    write_auction(warehouse, "600001.SH", TODAY, volume=30)  # 30/1000 = 3% >= 2%
    picks = selector(warehouse, require_auction_volume=True).select(TODAY, YESTERDAY)
    assert [p.symbol for p in picks] == ["600001.SH"]


def test_low_main_is_dropped_when_the_auction_volume_is_thin(warehouse, write_daily_bars):
    low_setup(warehouse, write_daily_bars)
    write_auction(warehouse, "600001.SH", TODAY, volume=10)  # 1% < 2%
    assert selector(warehouse, require_auction_volume=True).select(TODAY, YESTERDAY) == []


def test_low_chinext_skips_the_auction_volume_filter(warehouse, write_daily_bars):
    """Stage4 附加过滤只作用于 LOW_MAIN，创业板不受影响。"""
    low_setup(warehouse, write_daily_bars, symbol="300001.SZ")
    picks = selector(warehouse, require_auction_volume=True).select(TODAY, YESTERDAY)
    assert [p.tag for p in picks] == ["LOW_CHINEXT"]


def test_high_candidate_missing_auction_volume_fails_strict_mode(
    warehouse, write_daily_bars, monkeypatch
):
    low_setup(warehouse, write_daily_bars)
    selection = selector(warehouse, require_auction_volume=True)
    bars = selection._history(
        ["600001.SH"], YESTERDAY, datetime.combine(TODAY, time(9, 30))
    )["600001.SH"]
    monkeypatch.setattr(
        "qsys.strategy.structural.selection.check_low_open", lambda *_args: False
    )
    monkeypatch.setattr(
        "qsys.strategy.structural.selection.check_high_open",
        lambda *_args, **kwargs: kwargs["require_auction_volume"] is False,
    )

    with pytest.raises(MissingAuctionDataError, match="600001.SH"):
        selection._route(
            "600001.SH", TODAY, YESTERDAY, bars, 10.2, None,
            {"market_cap": 100.0, "circulating_market_cap": 100.0}, None,
        )


def test_a_gated_out_candidate_is_not_returned(warehouse, write_daily_bars):
    """LOW_MAIN 判成 RISK 时被 S7 Gate 拒掉。"""
    low_setup(warehouse, write_daily_bars)
    write_regime(warehouse, slope=0.5, heat=0.5)  # 趋势不弱且情绪高 -> RISK
    picks = selector(warehouse, require_auction_volume=False).select(TODAY, YESTERDAY)
    assert picks == []


def test_gate_decisions_are_recorded_even_when_rejected(warehouse, write_daily_bars):
    """拒了也要留痕，否则对账时说不清是没选中还是被 Gate 拦了。"""
    low_setup(warehouse, write_daily_bars)
    write_regime(warehouse, slope=0.5, heat=0.5)
    sel = selector(warehouse, require_auction_volume=False)
    sel.select(TODAY, YESTERDAY)
    assert [d.state for d in sel.decisions] == ["RISK"]
    assert sel.decisions[0].allow is False


def test_a_non_first_board_is_not_a_candidate(warehouse, write_daily_bars):
    low_setup(warehouse, write_daily_bars)
    # 把首板标记挪到更早的一天
    build(
        warehouse, write_daily_bars, "600001.SH",
        closes=[12.0] * (len(DAYS) - 1) + [10.0],
        highs=[14.0] * len(DAYS), lows=[9.5] * len(DAYS),
        amounts=[2e8] * len(DAYS), volumes=[1000] * len(DAYS),
        opens=[12.0] * (len(DAYS) - 1) + [9.6],
        first_board_on=DAYS[10],
    )
    assert selector(warehouse, require_auction_volume=False).select(TODAY, YESTERDAY) == []


def test_no_candidates_on_an_empty_warehouse(warehouse):
    build_delisting_pool(warehouse, DAYS[0], DAYS[-1], extra_sessions=DAYS)
    query = WarehouseQuery(warehouse)
    calendar = AShareCalendar(DAYS)
    sel = StructuralSelection(
        HistoricalFeed(query, calendar), MarketFeed(query, calendar), query
    )
    assert sel.select(TODAY, YESTERDAY) == []


def test_candidate_with_missing_factors_is_skipped_and_logged(
    warehouse, write_daily_bars
):
    """缺因子传播为 null 后，选股应留痕跳过而不是在数值比较处崩溃。"""
    from structlog.testing import capture_logs

    low_setup(warehouse, write_daily_bars)
    warehouse.write("adj_factors", schema.ADJ_FACTORS.empty_table())
    with capture_logs() as logs:
        picks = selector(warehouse, require_auction_volume=False).select(
            TODAY, YESTERDAY
        )
    assert picks == []
    assert any(
        row.get("event") == "selection.history_incomplete"
        and row.get("symbol") == "600001.SH"
        for row in logs
    )


def test_select_exposes_the_funnel_it_already_computed(
    warehouse, write_daily_bars
):
    low_setup(warehouse, write_daily_bars)
    selection = selector(warehouse, require_auction_volume=False)

    picks = selection.select(TODAY, YESTERDAY)

    funnel = selection.funnel
    assert funnel is not None
    assert funnel.allowed == len(picks)
    assert funnel.routed == len(selection.decisions)
    assert funnel.pool >= funnel.routed
    rejected = [decision for decision in selection.decisions if not decision.allow]
    assert sum(funnel.reasons.values()) == len(rejected)
    for decision in rejected:
        assert decision.reason in funnel.reasons


def test_funnel_does_not_accumulate_across_calls(warehouse, write_daily_bars):
    low_setup(warehouse, write_daily_bars)
    selection = selector(warehouse, require_auction_volume=False)

    selection.select(TODAY, YESTERDAY)
    first = selection.funnel
    selection.select(TODAY, YESTERDAY)

    assert selection.funnel == first
    assert first is not None
    assert len(selection.decisions) == first.routed


def test_history_accumulates_every_session_while_decisions_is_the_latest(
    warehouse, write_daily_bars,
):
    """落盘产物要整段判定：只写最近一次时，gate_decisions.csv 永远只有最后一天

    （2026-09-24 做闸门研究时发现，历次回测的该产物都只有表头或一行）。
    """
    low_setup(warehouse, write_daily_bars)
    selection = selector(warehouse, require_auction_volume=False)

    selection.select(TODAY, YESTERDAY)
    first = list(selection.history)
    selection.select(TODAY, YESTERDAY)

    assert len(first) == 1
    assert len(selection.decisions) == 1          # 最近一次
    assert len(selection.history) == 2            # 整段累计


def test_the_gate_can_be_disabled_for_research(warehouse, write_daily_bars):
    """关闸门只改放行结果，S6 判定照常留痕，才看得出本该拒的是哪些。"""
    low_setup(warehouse, write_daily_bars)
    write_regime(warehouse, slope=0.5, heat=0.5)   # 让 LOW_MAIN 落进 RISK

    blocked = selector(warehouse, require_auction_volume=False).select(TODAY, YESTERDAY)
    opened = selector(warehouse, require_auction_volume=False,
                      gate_enabled=False)
    picks = opened.select(TODAY, YESTERDAY)

    assert blocked == []
    assert [p.symbol for p in picks] == ["600001.SH"]
    assert opened.history[0].state == "RISK"
    assert "GATE_DISABLED" in opened.history[0].reason
