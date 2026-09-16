import threading
from datetime import datetime

from qsys.live.config import StreamConfig
from qsys.live.messages import MarketQuote
from qsys.live.transport import MemoryTransport
from qsys.paper.quotes import MarketGateway, QuoteClient


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
