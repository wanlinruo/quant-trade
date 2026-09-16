"""数据台的视图模型。

与监控台同一条纪律：Web 层不持有业务逻辑，只把仓库里的事实摆出来。

监控台读 `live_*`（实盘会话产生），数据台读数据层本身——两者互不依赖，
所以还没跑过任何交易时数据台照样有东西看。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import polars as pl

from qsys.data.coverage import DatasetCoverage, coverage
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.live.messages import MarketOverviewSnapshot, MarketSchedule
from qsys.live.transport import Transport


@dataclass(frozen=True, slots=True)
class LiveMarketView:
    snapshot: MarketOverviewSnapshot | None
    stale: bool
    available: bool


class DataService:
    def __init__(self, warehouse: Warehouse) -> None:
        self._warehouse = warehouse
        self._query = WarehouseQuery(warehouse)

    # ---- 数据覆盖 ----

    def coverage(self) -> list[DatasetCoverage]:
        return coverage(self._warehouse)

    def warehouse_totals(self) -> tuple[int, int]:
        """(总行数, 最大标的数)——给页首的 KPI。"""
        rows = self.coverage()
        if not rows:
            return 0, 0
        return sum(c.rows for c in rows), max(c.symbols for c in rows)

    # ---- 市场生态因子 ----

    def regime(self, limit: int = 60) -> pl.DataFrame:
        frame = self._query.market_regime()
        if frame.height == 0:
            return frame
        return frame.sort("dt").tail(limit)

    def regime_series(self, column: str, limit: int = 250) -> list[float]:
        frame = self._query.market_regime()
        if frame.height == 0 or column not in frame.columns:
            return []
        values = frame.sort("dt").tail(limit)[column].to_list()
        return [float(v) for v in values if v is not None]

    def latest_session(self) -> date | None:
        frame = self._query.market_regime()
        return frame["dt"].max() if frame.height else None


class MarketService:
    def __init__(
        self, warehouse: Warehouse, transport: Transport | None = None, *,
        snapshot_key: str = "state:market_overview", stale_seconds: float = 45.0,
        schedule_key: str = "state:market_schedule",
    ) -> None:
        self._data = DataService(warehouse)
        self._transport = transport
        self._snapshot_key = snapshot_key
        self._schedule_key = schedule_key
        self._stale_seconds = stale_seconds

    def live(self, *, now: datetime | None = None) -> LiveMarketView:
        if self._transport is None:
            return LiveMarketView(None, True, False)
        raw = self._transport.get_key(self._snapshot_key)
        if raw is None:
            return LiveMarketView(None, True, False)
        try:
            snapshot = MarketOverviewSnapshot.model_validate_json(raw)
        except Exception:
            return LiveMarketView(None, True, False)
        age = ((now or datetime.now()) - snapshot.published_at).total_seconds()
        return LiveMarketView(snapshot, age > self._stale_seconds, True)

    def schedule(self) -> MarketSchedule | None:
        if self._transport is None:
            return None
        raw = self._transport.get_key(self._schedule_key)
        if raw is None:
            return None
        try:
            return MarketSchedule.model_validate_json(raw)
        except Exception:
            return None

    def history(self, limit: int = 60) -> list[dict]:
        frame = self._data.regime(limit)
        return frame.sort("dt", descending=True).to_dicts() if frame.height else []

    def series(self, column: str, limit: int = 250) -> list[float]:
        return self._data.regime_series(column, limit)

    def latest_session(self) -> date | None:
        return self._data.latest_session()
