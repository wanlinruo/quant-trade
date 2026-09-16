import threading
from datetime import datetime

from qsys.live.config import StreamConfig
from qsys.live.messages import MarketOverviewSnapshot, MarketQuote
from qsys.live.transport import MemoryTransport
from qsys.paper.quotes import MarketGateway, QuoteClient, build_market_overview


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
           amount=100, suspended=False):
    return MarketQuote(symbol=symbol, at=datetime(2026, 9, 16, 10), price=price,
                       open=previous, high=high or price, low=price,
                       prev_close=previous, amount=amount, upper_limit=upper,
                       lower_limit=lower, suspended=suspended)


def test_market_overview_aggregates_the_live_breadth_universe():
    now = datetime(2026, 9, 16, 10, 30)
    snapshot = build_market_overview({
        "000001.SH": _quote("000001.SH", 3300, 3270),
        "600001.SH": _quote("600001.SH", 11, high=11, upper=11),
        "000001.SZ": _quote("000001.SZ", 9, high=10.5, upper=11),
        "300001.SZ": _quote("300001.SZ", 10, lower=10),
        "688001.SH": _quote("688001.SH", 12),  # 与日频广度口径一致：排除科创
        "600002.SH": _quote("600002.SH", 0, suspended=True),
    }, now=now)
    assert snapshot.valid_stock_count == 3
    assert (snapshot.advance_count, snapshot.decline_count, snapshot.flat_count) == (1, 1, 1)
    assert snapshot.limit_up_count == 1
    assert snapshot.limit_down_count == 1
    assert snapshot.touched_limit_up_count == 1
    assert snapshot.broken_board_count == 0
    assert snapshot.indices[0].name == "上证指数"


def test_gateway_publishes_one_atomic_overview_key():
    transport, streams = MemoryTransport(), StreamConfig()
    gateway = MarketGateway(transport, streams, lambda symbols: {})
    gateway.publish_overview({"600001.SH": _quote("600001.SH", 11, upper=11)})
    raw = transport.get_key(streams.market_snapshot)
    assert raw is not None
    assert MarketOverviewSnapshot.model_validate_json(raw).limit_up_count == 1
