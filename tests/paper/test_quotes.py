import threading
from datetime import date, datetime

from qsys.live.config import StreamConfig
from qsys.live.messages import MarketOverviewSnapshot, MarketQuote
from qsys.live.transport import MemoryTransport
from qsys.data.pipeline.premarket import publish_key
from qsys.paper.quotes import (
    MarketGateway,
    QuoteClient,
    build_market_overview,
    build_market_schedule,
)


def test_quote_request_round_trip_is_read_only():
    transport, streams = MemoryTransport(), StreamConfig()

    def provider(symbols):
        return {s: MarketQuote(symbol=s, at=datetime.now(), price=10, open=9.8,
                               high=10.1, low=9.7, prev_close=9.7, volume=1000)
                for s in symbols}

    gateway = MarketGateway(transport, streams, provider)
    client = QuoteClient(transport, streams, consumer="test", timeout=1)
    output = {}
    thread = threading.Thread(target=lambda: output.update(client.request(["600036.SH"])))
    thread.start()
    while not transport.all_messages(streams.quote_requests):
        pass
    gateway.run_once()
    thread.join(2)
    assert output["600036.SH"].price == 10
    assert transport.all_messages(streams.orders) == []


def _quote(symbol, price, previous=10, *, high=None, upper=None, lower=None,
           amount=100, suspended=False, bid1_volume=0, ask1_volume=0):
    return MarketQuote(symbol=symbol, at=datetime(2026, 9, 16, 10), price=price,
                       open=previous, high=high or price, low=price,
                       prev_close=previous, amount=amount, upper_limit=upper,
                       lower_limit=lower, suspended=suspended,
                       bid1_volume=bid1_volume, ask1_volume=ask1_volume)


def test_market_overview_aggregates_all_a_share_boards():
    now = datetime(2026, 9, 16, 10, 30)
    snapshot = build_market_overview({
        "000001.SH": _quote("000001.SH", 3300, 3270),
        "600001.SH": _quote("600001.SH", 11, high=11),
        "000001.SZ": _quote("000001.SZ", 9, high=10.5),
        "300001.SZ": _quote("300001.SZ", 10),
        "688001.SH": _quote("688001.SH", 12),
        "920001.BJ": _quote("920001.BJ", 13),
        "600002.SH": _quote("600002.SH", 0, suspended=True),
    }, limits={"600001.SH": (11, 9), "000001.SZ": (11, 9), "300001.SZ": (12, 10),
               "688001.SH": (12, 8), "920001.BJ": (13, 7)}, now=now)
    assert snapshot.valid_stock_count == 5
    assert (snapshot.advance_count, snapshot.decline_count, snapshot.flat_count) == (3, 1, 1)
    assert snapshot.limit_up_count == 3
    assert snapshot.limit_down_count == 2
    assert snapshot.touched_limit_up_count == 3
    assert snapshot.broken_board_count == 0
    assert snapshot.limit_price_missing_count == 0
    assert snapshot.indices[0].name == "上证指数"


def test_gateway_publishes_one_atomic_overview_key():
    transport, streams = MemoryTransport(), StreamConfig()
    transport.set_key(publish_key(streams, date(2026, 9, 16)), '{"600001.SH": [11, 9]}')
    gateway = MarketGateway(transport, streams, lambda symbols: {})
    gateway.publish_overview({"600001.SH": _quote("600001.SH", 11)},
                             now=datetime(2026, 9, 16, 10, 30))
    raw = transport.get_key(streams.market_snapshot)
    assert raw is not None
    assert MarketOverviewSnapshot.model_validate_json(raw).limit_up_count == 1


def test_market_overview_treats_limit_price_with_remaining_ask_as_broken():
    snapshot = build_market_overview({
        "002631.SZ": _quote(
            "002631.SZ", 13.23, previous=12.03, high=13.23,
            ask1_volume=2_830_000,
        ),
    }, limits={"002631.SZ": (13.23, 10.83)},
        now=datetime(2026, 9, 18, 15, 30))

    assert snapshot.limit_up_count == 0
    assert snapshot.touched_limit_up_count == 1
    assert snapshot.broken_board_count == 1


def test_market_overview_uses_direct_limits_and_never_derives_them():
    """D-19：QMT get_full_tick 不给涨跌停价。只认盘前发布的 stk_limit，缺价不计入并计数。

    主板 ST 在 2026-07-06 前是 5% 档：按板块 10% 自算会把封在 5% 涨停的票算成未涨停。
    """
    snapshot = build_market_overview({
        "600001.SH": _quote("600001.SH", 10.5, high=10.5),   # ST，直取涨停 10.50
        "300001.SZ": _quote("300001.SZ", 12, high=12),       # 无直取价
        "000001.SZ": _quote("000001.SZ", 10.5, high=11),
    }, limits={"600001.SH": (10.5, 9.5), "000001.SZ": (11, 9)},
        now=datetime(2026, 9, 16, 10, 30))
    assert snapshot.limit_up_count == 1
    assert snapshot.touched_limit_up_count == 2
    assert snapshot.broken_board_count == 1
    assert snapshot.limit_price_missing_count == 1


def test_market_schedule_uses_the_operational_overview_windows():
    sessions = [date(2026, 9, 16), date(2026, 9, 18)]
    before = build_market_schedule(datetime(2026, 9, 16, 8, 59, 59), sessions)
    morning_start = build_market_schedule(datetime(2026, 9, 16, 9), sessions)
    lunch = build_market_schedule(datetime(2026, 9, 16, 12), sessions)
    afternoon_end = build_market_schedule(datetime(2026, 9, 16, 15, 30), sessions)
    after = build_market_schedule(datetime(2026, 9, 16, 15, 30, 1), sessions)
    holiday = build_market_schedule(datetime(2026, 9, 17, 10), sessions)
    assert before.is_open is False
    assert before.next_open_at == datetime(2026, 9, 16, 9)
    assert morning_start.is_open is True
    assert lunch.is_open is False
    assert lunch.next_open_at == datetime(2026, 9, 16, 13)
    assert afternoon_end.is_open is True
    assert after.is_open is False
    assert after.next_open_at == datetime(2026, 9, 18, 9)
    assert holiday.is_trading_day is False
    assert holiday.next_open_at == datetime(2026, 9, 18, 9)


# --- D-19：盘前 Tushare stk_limit 与 QMT get_instrument_detail 逐只核对 -------

def test_limit_check_runs_in_batches_and_publishes_once_complete():
    import json

    transport, streams = MemoryTransport(), StreamConfig()
    day = date(2026, 9, 17)
    transport.set_key(publish_key(streams, day), json.dumps({
        "600000.SH": [10.01, 8.19], "000010.SZ": [1.73, 1.41], "300001.SZ": [12.0, 8.0],
    }))
    qmt = {"600000.SH": (10.01, 8.19), "000010.SZ": (1.74, 1.41)}  # 300001.SZ QMT 无数据
    calls = []

    def detail(symbols):
        calls.append(list(symbols))
        return {s: qmt[s] for s in symbols if s in qmt}

    gateway = MarketGateway(transport, streams, lambda symbols: {})
    assert gateway.check_limits_step(detail, day, batch=2) is False
    assert transport.get_key(f"{streams.price_limits}_check:20260917") is None
    assert gateway.check_limits_step(detail, day, batch=2) is True
    report = json.loads(transport.get_key(f"{streams.price_limits}_check:20260917"))
    assert report["checked"] == 3
    assert report["mismatched"] == 1 and report["missing_in_qmt"] == 1
    assert report["samples"][0]["symbol"] == "000010.SZ"
    # 当日核对完成后不再重复调用 QMT
    assert gateway.check_limits_step(detail, day, batch=2) is True
    assert len(calls) == 2


def test_limit_check_waits_until_tushare_limits_are_published():
    transport, streams = MemoryTransport(), StreamConfig()
    gateway = MarketGateway(transport, streams, lambda symbols: {})
    assert gateway.check_limits_step(lambda s: {}, date(2026, 9, 17)) is False
