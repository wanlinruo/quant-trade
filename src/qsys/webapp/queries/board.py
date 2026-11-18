"""打板看板查询（#662 R2，设计文档 §8.9、§10）：把读取器视图映射为对外契约。

- 最新视图（不带 day / at）经 15 秒单飞缓存；回放与回看按请求计算，不复用最新值。
- 前一交易日同层晋级率取该日看板最终时刻，按前一交易日缓存 300 秒。
- 交易时段取市场查询层的同一份 15 秒缓存，不按请求读 Redis。
- 存储里是无时区本地时间：带时区的回放时刻先换算到 Asia/Shanghai 再查询。
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime

import structlog

from qsys.webapp.cache import SingleFlightCache
from qsys.webapp.contracts.base import LOCAL_TZ
from qsys.webapp.contracts.board import Board, BoardSeries, BoardUnavailable, BoardSeriesEvent
from qsys.webapp.contracts.market import MarketSchedule
from qsys.webapp.queries.board_effect import layer_rates, layer_rows, money_effect

log = structlog.get_logger(__name__)

UNCONFIGURED = "打板看板数据源未配置"
READ_FAILED = "读取失败"


def to_local_naive(at: datetime | None) -> datetime | None:
    if at is None or at.tzinfo is None:
        return at
    return at.astimezone(LOCAL_TZ).replace(tzinfo=None)


class _PrevRates:
    """按前一交易日缓存其各层晋级率。"""

    def __init__(self, reader, ttl: float) -> None:
        self._reader = reader
        self._ttl = ttl
        self._lock = threading.Lock()
        self._cache: dict[date, tuple[float, dict[int, float | None]]] = {}

    def get(self, prev_day: date | None, days: list[date]) -> dict[int, float | None]:
        if prev_day is None or prev_day not in days:
            return {}
        with self._lock:
            hit = self._cache.get(prev_day)
            if hit and time.monotonic() - hit[0] < self._ttl:
                return hit[1]
            view = self._reader.view(prev_day)
            rates = layer_rates(layer_rows(view.compare, {})) if view.available else {}
            self._cache = {prev_day: (time.monotonic(), rates)}
            return rates


class BoardQuery:
    def __init__(self, reader, market=None, *, latest_ttl: float = 15.0,
                 prev_ttl: float = 300.0) -> None:
        self._reader = reader
        self._market = market
        self._prev = _PrevRates(reader, prev_ttl) if reader is not None else None
        self._latest = SingleFlightCache(lambda: self._build(None, None), latest_ttl)

    def _schedule(self) -> MarketSchedule | None:
        if self._market is None:
            return None
        try:
            return self._market.schedule()
        except Exception as exc:  # 交易时段只用于前端刷新节奏，读不到就当未知
            log.info("webapp.board.schedule_unavailable", error_type=type(exc).__name__)
            return None

    def _build(self, day: date | None, at: datetime | None) -> Board | BoardUnavailable:
        view = self._reader.view(day, at)
        if not view.available:
            return BoardUnavailable(message=view.message, day=view.day, days=view.days)
        errors: dict[str, str] = {}
        prev_day = (view.meta or {}).get("prev_day")
        try:
            rates = self._prev.get(date.fromisoformat(prev_day) if prev_day else None, view.days)
        except Exception as exc:
            log.warning("webapp.board.prev_rate_failed", error_type=type(exc).__name__)
            rates, errors["prev_rate"] = {}, READ_FAILED
        body = view.model_dump(exclude={"status", "available"})
        body["meta"] = {k: v for k, v in body["meta"].items() if k != "concept_sha"}
        return Board.model_validate(body | {
            "provisional": view.mode == "live", "effect": money_effect(view.compare),
            "layers": layer_rows(view.compare, rates), "schedule": None, "errors": errors})

    def board(self, day: date | None = None, at: datetime | None = None) -> Board | BoardUnavailable:
        if self._reader is None:
            return BoardUnavailable(message=UNCONFIGURED, day=None, days=[])
        at = to_local_naive(at)
        body = self._latest.get() if day is None and at is None else self._build(day, at)
        if isinstance(body, BoardUnavailable):
            return body
        return body.model_copy(update={"schedule": self._schedule()})

    def series(self, day: date, symbol: str) -> BoardSeries:
        if self._reader is None:
            return BoardSeries(available=False, message=UNCONFIGURED, symbol=symbol, name="",
                               limit_up=None, prev_close=None, first_seal=None, points=[], events=[])
        view = self._reader.series(day, symbol)
        events = [BoardSeriesEvent(at=e.at, kind=e.kind, detail=e.detail)
                  for e in self._reader.symbol_events(day, symbol)]
        return BoardSeries.model_validate(view.model_dump() | {"events": events})
