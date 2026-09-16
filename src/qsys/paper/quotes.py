"""Request/response bridge between paper workers and the Windows QMT gateway."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime

from qsys.live.config import StreamConfig
from qsys.live.messages import Heartbeat, MarketQuote, QuoteRequest, QuoteSnapshot
from qsys.live.transport import Transport


class QuoteTimeout(TimeoutError):
    pass


class QuoteClient:
    def __init__(
        self, transport: Transport, streams: StreamConfig, *,
        consumer: str, timeout: float = 5.0,
    ) -> None:
        self._t, self._s = transport, streams
        self._consumer, self._timeout = consumer, timeout
        self._group = f"paper-quotes-{consumer}"
        self._t.ensure_group(streams.quote_snapshots, self._group)
        self._deferred: dict[str, QuoteSnapshot] = {}

    def request(self, symbols: Sequence[str]) -> dict[str, MarketQuote]:
        unique = sorted(set(symbols))
        if not unique:
            return {}
        request_id = uuid.uuid4().hex
        self._t.append(self._s.quote_requests, QuoteRequest(
            request_id=request_id, symbols=unique, requested_at=datetime.now()
        ).to_payload())
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            cached = self._deferred.pop(request_id, None)
            if cached is not None:
                return self._unwrap(cached)
            for message in self._t.read_group(
                self._s.quote_snapshots, self._group, self._consumer, count=100
            ):
                self._t.ack(self._s.quote_snapshots, self._group, message.id)
                snapshot = QuoteSnapshot.from_payload(message.payload)
                if snapshot.request_id == request_id:
                    return self._unwrap(snapshot)
                self._deferred[snapshot.request_id] = snapshot
            time.sleep(0.05)
        raise QuoteTimeout(f"QMT 行情请求超时（{len(unique)} 个标的）")

    @staticmethod
    def _unwrap(snapshot: QuoteSnapshot) -> dict[str, MarketQuote]:
        if snapshot.error:
            raise RuntimeError(f"QMT 行情错误: {snapshot.error}")
        return snapshot.quotes


class MarketGateway:
    """Read-only gateway. The provider callable is the only QMT-specific seam."""

    def __init__(
        self, transport: Transport, streams: StreamConfig,
        provider: Callable[[Sequence[str]], dict[str, MarketQuote]], *,
        group: str = "market-gateway", consumer: str = "market-1",
    ) -> None:
        self._t, self._s, self._provider = transport, streams, provider
        self._group, self._consumer = group, consumer
        self._t.ensure_group(streams.quote_requests, group)

    def run_once(self) -> int:
        handled = 0
        for message in self._t.read_group(
            self._s.quote_requests, self._group, self._consumer, count=50
        ):
            handled += 1
            try:
                request = QuoteRequest.from_payload(message.payload)
                try:
                    quotes = self._provider(request.symbols)
                    response = QuoteSnapshot(
                        request_id=request.request_id, published_at=datetime.now(),
                        quotes=quotes,
                    )
                except Exception as exc:
                    response = QuoteSnapshot(
                        request_id=request.request_id, published_at=datetime.now(),
                        quotes={}, error=str(exc),
                    )
                self._t.append(self._s.quote_snapshots, response.to_payload())
            finally:
                self._t.ack(self._s.quote_requests, self._group, message.id)
        return handled

    def beat(self) -> None:
        self._t.set_key(
            f"{self._s.heartbeat}:market",
            Heartbeat(source="market", at=datetime.now()).model_dump_json(),
        )
