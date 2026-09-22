from datetime import date, datetime, time

import polars as pl
import pytest

from scripts import auction_timing_study as study

DAY = date(2026, 9, 18)


def _ticks(rows):
    return pl.DataFrame(
        [{"symbol": s, "dt": DAY, "ts": datetime.combine(DAY, t), "bid1": b, "ask1": a,
          "bid_vol1": v, "last_price": lp, "volume": vol} for s, t, b, a, v, lp, vol in rows],
        schema_overrides={"bid_vol1": pl.Int64, "volume": pl.Int64},
    )


def test_virtual_snapshot_takes_the_last_matched_quote_not_after_the_moment():
    ticks = _ticks([
        ("600001.SH", time(9, 23, 57), 10.0, 10.0, 500, 0.0, 0),
        ("600001.SH", time(9, 24, 0), 10.1, 10.1, 600, 0.0, 0),
        ("600001.SH", time(9, 24, 3), 10.2, 10.2, 700, 0.0, 0),
        ("000001.SZ", time(9, 23, 51), 5.0, 5.01, 100, 0.0, 0),   # 买一 != 卖一：不是虚拟价
        ("000001.SZ", time(9, 23, 42), 5.0, 5.0, 90, 0.0, 0),
    ])

    got = study.virtual_at(ticks, time(9, 24, 0))

    assert got[("600001.SH", DAY)] == (10.1, 600)
    assert got[("000001.SZ", DAY)] == (5.0, 90)


def test_limit_price_is_rounded_down_and_capped_at_limit_up():
    assert study.limit_price(10.0, 0.013, 11.0) == pytest.approx(10.13)
    assert study.limit_price(10.0, 0.2, 11.0) == pytest.approx(11.0)
    assert study.limit_price(10.0, None, 11.0) == pytest.approx(11.0)
    assert study.limit_price(10.007, 0.0, 11.0) == pytest.approx(10.0)


def test_auction_fill_outcome():
    assert study.auction_fill(open_=10.1, limit=10.2, limit_up=11.0) == "filled"
    assert study.auction_fill(open_=10.3, limit=10.2, limit_up=11.0) == "missed"
    # 涨停开盘：只有第一档，看不到未匹配买量，能否排上队无法判断
    assert study.auction_fill(open_=11.0, limit=11.0, limit_up=11.0) == "limit_up_open"


def test_bps_is_positive_when_paying_more():
    assert study.bps(10.1, 10.0) == pytest.approx(100.0)
    assert study.bps(None, 10.0) is None


def test_bar_vwap_uses_lots():
    assert study.vwap(amount=1_010_000.0, volume_lots=1_000.0) == pytest.approx(10.1)
    assert study.vwap(amount=0.0, volume_lots=0.0) is None


def test_quantiles_summarise_bps():
    summary = study.describe([-100.0, 0.0, 50.0, 100.0, 300.0])
    assert summary["n"] == 5
    assert summary["p50"] == pytest.approx(50.0)
    assert summary["mean"] == pytest.approx(70.0)
    assert study.describe([])["n"] == 0


def test_consistency_counts_extra_and_missed_picks():
    final = {("A", DAY): "LOW_MAIN", ("B", DAY): "HIGH_MAIN"}
    picks = {("A", DAY): "LOW_MAIN", ("C", DAY): "HIGH_MAIN"}

    got = study.consistency(picks, final)

    assert (got["both"], got["extra"], got["missed"]) == (1, 1, 1)
    assert got["recall"] == pytest.approx(0.5)
    assert got["missed_by_tag"] == {"HIGH_MAIN": 1}
