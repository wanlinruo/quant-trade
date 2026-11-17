"""新前端市场总览只读接口 /api/app/market（#662，设计文档 §8.8、§10）。

- live / schedule / limit_check 读 Redis，经同一个 15 秒单飞缓存（不快于快照发布间隔）。
- regime 读 warehouse（收盘后派生），经 300 秒缓存；按日期升序、逐行保留全部列与 null。
- 任一部分失败只把该部分置 null 并在 errors 注明，不回显异常文本或路径。
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from qsys.data.schema import MARKET_REGIME
from qsys.web.read_cache import SingleFlightCache

LIVE_TTL_SECONDS = 15.0
REGIME_TTL_SECONDS = 300.0
REGIME_DAYS = 60
REGIME_COLUMNS = list(MARKET_REGIME.names)
READ_FAILED = "读取失败"

MISSING = {
    "index_intraday": "行情快照只保存当前值，没有指数分时序列；需采集器保存（见 #675）",
    "return_distribution": "行情快照没有涨跌幅分布；需采集器提供（见 #675）",
}


def _json_value(v: Any) -> Any:
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def regime_rows(history: list[dict]) -> list[dict]:
    rows = sorted(history, key=lambda r: r["dt"])[-REGIME_DAYS:]
    return [{c: _json_value(r.get(c)) for c in REGIME_COLUMNS} for r in rows]


def _live_parts(market) -> dict:
    out: dict[str, Any] = {"errors": {}}
    try:
        view = market.live()
        out["live"] = {
            "available": view.available, "stale": view.stale,
            "snapshot": view.snapshot.model_dump(mode="json") if view.snapshot else None,
        }
    except Exception:
        out["live"] = None
        out["errors"]["live"] = READ_FAILED
    try:
        s = market.schedule()
        out["schedule"] = s.model_dump(mode="json") if s is not None else None
    except Exception:
        out["schedule"] = None
        out["errors"]["schedule"] = READ_FAILED
    try:
        out["limit_check"] = market.limit_check()
    except Exception:
        out["limit_check"] = None
        out["errors"]["limit_check"] = READ_FAILED
    return out


def register_app_market(app: FastAPI, market) -> None:
    if market is None:
        @app.get("/api/app/market")
        def app_market_disabled():
            return JSONResponse({"enabled": False}, headers={"Cache-Control": "no-store"})
        return

    # 各部分失败已在 _live_parts 内隔离；这一组结果（含失败）缓存 15 秒，
    # Redis 不可用时也按发布间隔退避，不逐请求重试。
    live_cache = SingleFlightCache(lambda: _live_parts(market), LIVE_TTL_SECONDS)
    regime_cache = SingleFlightCache(lambda: regime_rows(market.history(REGIME_DAYS)), REGIME_TTL_SECONDS)
    app.state.app_market_caches = (live_cache, regime_cache)

    @app.get("/api/app/market")
    def app_market():
        live = live_cache.get()
        errors = dict(live["errors"])
        try:
            regime = regime_cache.get()
        except Exception:
            regime = None
            errors["regime"] = READ_FAILED
        body = {
            "enabled": True,
            "live": live["live"], "schedule": live["schedule"], "limit_check": live["limit_check"],
            "regime": regime, "regime_columns": REGIME_COLUMNS,
            "missing": MISSING, "errors": errors,
        }
        return JSONResponse(body, headers={"Cache-Control": "no-store"})
