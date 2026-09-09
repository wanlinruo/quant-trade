"""数据台的视图模型。

与监控台同一条纪律：Web 层不持有业务逻辑，只把仓库里的事实摆出来。

监控台读 `live_*`（实盘会话产生），数据台读数据层本身——两者互不依赖，
所以还没跑过任何交易时数据台照样有东西看。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from qsys.data.coverage import DatasetCoverage, coverage
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

_SELECTION_COLUMNS = ["dt", "symbol", "tag", "state", "reason", "allow"]


@dataclass(frozen=True, slots=True)
class SelectionSummary:
    total: int
    allowed: int
    sessions: int
    symbols: int
    by_tag: list[tuple[str, int]]
    by_year: list[tuple[int, int]]
    by_state: list[tuple[str, str, int]]


class DataService:
    def __init__(self, warehouse: Warehouse, selection_path: Path | None = None) -> None:
        self._warehouse = warehouse
        self._query = WarehouseQuery(warehouse)
        self._selection_path = selection_path

    # ---- 数据覆盖 ----

    def coverage(self) -> list[DatasetCoverage]:
        return coverage(self._warehouse)

    def warehouse_totals(self) -> tuple[int, int]:
        """(总行数, 最大标的数)——给页首的 KPI。"""
        rows = self.coverage()
        if not rows:
            return 0, 0
        return sum(c.rows for c in rows), max(c.symbols for c in rows)

    # ---- 选股结果 ----

    def _selections(self) -> pl.DataFrame:
        empty = pl.DataFrame(
            schema={
                "dt": pl.Date, "symbol": pl.Utf8, "tag": pl.Utf8,
                "state": pl.Utf8, "reason": pl.Utf8, "allow": pl.Boolean,
            }
        )
        path = self._selection_path
        if path is None or not Path(path).exists():
            return empty
        try:
            frame = pl.read_csv(path, try_parse_dates=True)
        except Exception:
            return empty
        missing = set(_SELECTION_COLUMNS) - set(frame.columns)
        if missing:
            return empty
        return frame.select(_SELECTION_COLUMNS)

    def selection_summary(self) -> SelectionSummary:
        frame = self._selections()
        if frame.height == 0:
            return SelectionSummary(0, 0, 0, 0, [], [], [])
        allowed = frame.filter(pl.col("allow"))
        by_tag = [
            (row["tag"], row["len"])
            for row in allowed.group_by("tag").len().sort("tag").iter_rows(named=True)
        ]
        by_year = [
            (row["year"], row["len"])
            for row in allowed.with_columns(pl.col("dt").dt.year().alias("year"))
            .group_by("year")
            .len()
            .sort("year")
            .iter_rows(named=True)
        ]
        by_state = [
            (row["tag"], row["state"], row["len"])
            for row in frame.group_by(["tag", "state"])
            .len()
            .sort(["tag", "state"])
            .iter_rows(named=True)
        ]
        return SelectionSummary(
            total=frame.height,
            allowed=allowed.height,
            sessions=allowed["dt"].n_unique() if allowed.height else 0,
            symbols=allowed["symbol"].n_unique() if allowed.height else 0,
            by_tag=by_tag,
            by_year=by_year,
            by_state=by_state,
        )

    def selections(self, *, tag: str | None = None, limit: int = 200) -> list[dict]:
        frame = self._selections()
        if frame.height == 0:
            return []
        frame = frame.filter(pl.col("allow"))
        if tag:
            frame = frame.filter(pl.col("tag") == tag)
        return frame.sort("dt", descending=True).head(limit).to_dicts()

    def selection_tags(self) -> list[str]:
        frame = self._selections()
        if frame.height == 0:
            return []
        return sorted(frame["tag"].unique().to_list())

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
