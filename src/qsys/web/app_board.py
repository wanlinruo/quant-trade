"""新前端打板看板接口 /api/app/board（#662，设计文档 §8.9、§10）。

包装只读读取器 LimitUpBoardReader 的视图，并补上：
- effect / layers：“昨日涨停今日表现”的纯派生（board_effect），前端不重算；
- prev_rate：前一交易日看板最终时刻同层晋级率（按前一交易日缓存 300 秒）；
- schedule：取 /api/app/market 的同一份 15 秒缓存，不按请求读 Redis。
最新视图（不带 day / at）经 15 秒单飞缓存；回放与回看按请求计算，不复用最新值。
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from qsys.web.board_effect import layer_rates, layer_rows, money_effect
from qsys.webapp.cache import SingleFlightCache

LATEST_TTL_SECONDS = 15.0
PREV_TTL_SECONDS = 300.0
READ_FAILED = "读取失败"
NO_STORE = {"Cache-Control": "no-store"}


class _PrevRates:
    """按前一交易日缓存其各层晋级率。"""

    def __init__(self, board, ttl: float = PREV_TTL_SECONDS) -> None:
        self._board = board
        self._ttl = ttl
        self._lock = threading.Lock()
        self._cache: dict[date, tuple[float, dict[int, float | None]]] = {}

    def get(self, prev_day: date | None, days: list) -> dict[int, float | None]:
        if prev_day is None or prev_day.isoformat() not in {str(d) for d in days}:
            return {}
        with self._lock:
            hit = self._cache.get(prev_day)
            if hit and time.monotonic() - hit[0] < self._ttl:
                return hit[1]
            view = self._board.view(prev_day)
            rates = layer_rates(layer_rows(view.compare, {})) if view.available else {}
            self._cache = {prev_day: (time.monotonic(), rates)}
            return rates


def register_app_board(app: FastAPI, board, live_parts) -> None:
    if board is None:
        @app.get("/api/app/board")
        def app_board_disabled():
            return JSONResponse({"available": False, "message": "打板看板数据源未配置"},
                                headers=NO_STORE)

        @app.get("/api/app/board/series")
        def app_board_series_disabled(day: date, symbol: str):
            return JSONResponse({"available": False, "symbol": symbol,
                                 "message": "打板看板数据源未配置", "points": [], "events": []},
                                headers=NO_STORE)
        return

    prev_rates = _PrevRates(board)

    def build(day: date | None, at: datetime | None) -> dict:
        view = board.view(day, at)
        body = view.model_dump(mode="json")
        errors: dict[str, str] = {}
        body["provisional"] = view.mode == "live"
        body["prev_day"] = (view.meta or {}).get("prev_day")
        body["effect"] = money_effect(view.compare) if view.available else None
        try:
            prev = date.fromisoformat(body["prev_day"]) if body["prev_day"] else None
            rates = prev_rates.get(prev, body.get("days", []))
        except Exception:
            rates = {}
            errors["prev_rate"] = READ_FAILED
        body["layers"] = layer_rows(view.compare, rates) if view.available else []
        body["errors"] = errors
        return body

    latest = SingleFlightCache(lambda: build(None, None), LATEST_TTL_SECONDS)

    def schedule() -> dict | None:
        if live_parts is None:
            return None
        try:
            return live_parts.get()["schedule"]
        except Exception:
            return None

    @app.get("/api/app/board")
    def app_board(day: date | None = None, at: datetime | None = None):
        body = dict(latest.get()) if day is None and at is None else build(day, at)
        body["schedule"] = schedule()
        return JSONResponse(body, headers=NO_STORE)

    @app.get("/api/app/board/series")
    def app_board_series(day: date, symbol: str):
        body = board.series(day, symbol).model_dump(mode="json")
        body["events"] = [{"at": e.at.isoformat(), "kind": e.kind, "detail": e.detail}
                          for e in board.symbol_events(day, symbol)]
        return JSONResponse(body, headers=NO_STORE)
