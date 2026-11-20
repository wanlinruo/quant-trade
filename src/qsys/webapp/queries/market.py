"""市场总览查询（设计文档 §8.8、§10）。

- 行情快照、交易时段、涨跌停核对来自 Redis，共用一个 15 秒单飞缓存（不快于发布间隔，#666）；
  某部分读取失败只把该部分置空并记入 errors，不回显异常；失败结果同样缓存 15 秒（退避）。
- 近 60 日市场情绪来自仓库（收盘后派生），300 秒缓存；按日期升序，逐行保留全部列与 null。
- 快照是否过期按读取时刻计算。
- 名录拆分（#460）：名录读不到或为空时不拆分——空名录不能当成“全部不在名录”。
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

import polars as pl

from qsys.data import schema
from qsys.live.messages import MarketOverviewSnapshot
from qsys.live.messages import MarketSchedule as ScheduleMessage
from qsys.webapp.cache import SingleFlightCache
from qsys.webapp.contracts.market import (
    LimitCheck,
    LimitCheckSample,
    LimitPair,
    LiveMarket,
    MarketOverview,
    MarketSchedule,
    MarketSnapshot,
    RegimeDay,
)

LIVE_TTL_SECONDS = 15.0
REGIME_TTL_SECONDS = 300.0
REGIME_DAYS = 60
STALE_SECONDS = 45.0
READ_FAILED = "读取失败"
MISSING = {
    "index_intraday": "行情快照只保存当前值，没有指数分时序列；需采集器保存（见 #675）",
    "return_distribution": "行情快照没有涨跌幅分布；需采集器提供（见 #675）",
}


class Transport(Protocol):
    def get_key(self, key: str) -> str | None: ...


def _pair(value: Any) -> LimitPair | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    return LimitPair(up=value[0], down=value[1])


def _sample(item: dict) -> LimitCheckSample:
    return LimitCheckSample(symbol=item["symbol"], tushare=_pair(item.get("tushare")),
                            qmt=_pair(item.get("qmt")))


class MarketQuery:
    def __init__(self, transport: Transport | None, *,
                 regime_source: Callable[[], pl.DataFrame],
                 roster_source: Callable[[], frozenset[str] | set[str] | None],
                 snapshot_key: str = "state:market_overview",
                 schedule_key: str = "state:market_schedule",
                 limit_check_prefix: str = "state:price_limits_check",
                 stale_seconds: float = STALE_SECONDS,
                 now: Callable[[], datetime] = datetime.now,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._t = transport
        self._regime_source, self._roster_source = regime_source, roster_source
        self._snapshot_key, self._schedule_key = snapshot_key, schedule_key
        self._limit_prefix, self._stale = limit_check_prefix, stale_seconds
        self._now = now
        self._live = SingleFlightCache(self._read_live, LIVE_TTL_SECONDS, clock=clock)
        self._regime = SingleFlightCache(self._read_regime, REGIME_TTL_SECONDS, clock=clock)

    @classmethod
    def from_warehouse(cls, transport: Transport | None, warehouse, **kw) -> MarketQuery:
        from qsys.data.cutover import CutoverConfig, CutoverMarketData
        from qsys.data.query import WarehouseQuery

        query = WarehouseQuery(warehouse)
        facade = CutoverMarketData(query, CutoverConfig.from_env())
        return cls(transport, regime_source=facade.market_regime,
                   roster_source=lambda: frozenset(query.instruments()["symbol"].to_list()), **kw)

    # ---- Redis 部分（15 秒单飞缓存） ----
    def _read_live(self) -> dict[str, Any]:
        out: dict[str, Any] = {"snapshot": None, "available": False, "schedule": None,
                               "limit_check": None, "errors": {}}
        if self._t is None:
            return out
        try:
            raw = self._t.get_key(self._snapshot_key)
            if raw is not None:
                try:
                    out["snapshot"] = MarketOverviewSnapshot.model_validate_json(raw)
                    out["available"] = True
                except ValueError:
                    pass  # 快照格式不符：按“不可用”展示，与发布中断同样处理
        except Exception:
            out["errors"]["live"] = READ_FAILED
        try:
            raw = self._t.get_key(self._schedule_key)
            out["schedule"] = ScheduleMessage.model_validate_json(raw) if raw else None
        except Exception:
            out["errors"]["schedule"] = READ_FAILED
        try:
            raw = self._t.get_key(f"{self._limit_prefix}:{self._now():%Y%m%d}")
            out["limit_check"] = self._limit_check(json.loads(raw)) if raw else None
        except Exception:
            out["errors"]["limit_check"] = READ_FAILED
        return out

    def _limit_check(self, report: dict) -> LimitCheck:
        samples = report.get("missing_samples") or []
        in_roster = outside = complete = None
        if samples:
            try:
                roster = self._roster_source()
            except Exception:
                roster = None
            if roster:
                symbols = [item["symbol"] for item in samples]
                in_roster = [s for s in symbols if s in roster]
                outside = [s for s in symbols if s not in roster]
                complete = len(symbols) == report.get("missing_in_qmt")
        checked_at = report.get("checked_at")
        return LimitCheck(
            session=report["session"], checked_at=datetime.fromisoformat(checked_at) if checked_at else None,
            checked=report["checked"], mismatched=report["mismatched"],
            unbounded_equivalent=report.get("unbounded_equivalent", 0),
            missing_in_qmt=report.get("missing_in_qmt", 0),
            samples=[_sample(i) for i in report.get("samples") or []],
            missing_samples=[_sample(i) for i in samples],
            missing_in_roster=in_roster, missing_outside_roster=outside,
            missing_split_complete=complete)

    # ---- 仓库部分（300 秒缓存） ----
    def _read_regime(self) -> list[RegimeDay]:
        frame = self._regime_source()
        if frame.height == 0:
            return []
        rows = frame.sort("dt").tail(REGIME_DAYS).to_dicts()
        names = schema.MARKET_REGIME.names
        return [RegimeDay(**{c: (None if isinstance(r.get(c), float) and math.isnan(r[c]) else r.get(c))
                             for c in names}) for r in rows]

    # ---- 对外 ----
    def schedule(self) -> MarketSchedule | None:
        s = self._live.get()["schedule"]
        return None if s is None else MarketSchedule(
            checked_at=s.checked_at, is_trading_day=s.is_trading_day,
            is_open=s.is_open, next_open_at=s.next_open_at)

    def overview(self) -> MarketOverview:
        parts = self._live.get()
        errors = dict(parts["errors"])
        snap: MarketOverviewSnapshot | None = parts["snapshot"]
        if "live" in errors:
            live = None
        else:
            stale = True if snap is None else (self._now() - snap.published_at).total_seconds() > self._stale
            live = LiveMarket(available=parts["available"], stale=stale,
                              snapshot=None if snap is None else MarketSnapshot.model_validate(
                                  snap.model_dump(exclude={"coverage"}) | {
                                      "coverage": None if snap.coverage is None else
                                      snap.coverage.model_dump(exclude={"universe_sha256"})}))
        try:
            regime = self._regime.get()
        except Exception:
            regime = None
            errors["regime"] = READ_FAILED
        return MarketOverview(live=live, schedule=self.schedule(), limit_check=parts["limit_check"],
                              regime=regime, missing=MISSING, errors=errors)
