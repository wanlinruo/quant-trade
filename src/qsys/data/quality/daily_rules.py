"""日线完整性：找出「内部空洞」——前后都有 bar、中间那天却没有。

mootdx 返回的日线序列会**静默少掉某些天**：采集日志报 0 失败，但序列里
就是没有那一天。实测 2023-01 ~ 2026-09 全市场 5,008 个空洞，占 0.125%。
2026-04-30 因此比聚宽少 41 只，直接翻转了当天的 `market_advance_ratio`。

空洞不等于 bug：真停牌本来就没有 bar。两者靠 QMT 的 `suspendFlag` 区分——
本模块只负责**找出可疑的股-日**，判定留给补采结果。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import polars as pl

from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


@dataclass(frozen=True, slots=True)
class HoleReport:
    stock_days: pl.DataFrame   # symbol, dt
    symbols: list[str]
    per_day: pl.DataFrame      # dt, holes

    @property
    def total(self) -> int:
        return self.stock_days.height


def find_holes(
    warehouse: Warehouse, start: date, end: date,
    *, exclude_prefixes: tuple[str, ...] = ("4", "8", "68"),
    sessions: Sequence[date] | None = None,
) -> HoleReport:
    """区间内每只标的的内部空洞。

    只看**首末 bar 之间**的缺失——上市前与退市后没有 bar 是正常的。

    `sessions` 不给则取全市场 bar 日期的并集。全市场几千只票时它等于
    交易日历，但那是巧合不是保证：若整个交易日被漏采，并集里就没有那天，
    空洞也就发现不了。定时任务应显式传入日历。
    """
    query = WarehouseQuery(warehouse)
    bars = query.daily_bars(None, start, end).select(["symbol", "dt"])
    empty = pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date})
    if bars.height == 0:
        return HoleReport(empty, [], pl.DataFrame(schema={"dt": pl.Date,
                                                          "holes": pl.Int64}))

    code = pl.col("symbol").str.split(".").list.first()
    excluded = pl.lit(False)
    for prefix in exclude_prefixes:
        excluded = excluded | code.str.starts_with(prefix)
    bars = bars.filter(~excluded)
    if bars.height == 0:
        return HoleReport(empty, [], pl.DataFrame(schema={"dt": pl.Date,
                                                          "holes": pl.Int64}))

    known = sorted(sessions) if sessions else sorted(bars["dt"].unique().to_list())
    known = [d for d in known if start <= d <= end]
    index = {d: i for i, d in enumerate(known)}

    span = bars.group_by("symbol").agg(
        pl.col("dt").min().alias("first_dt"),
        pl.col("dt").max().alias("last_dt"),
    )
    have = set(zip(bars["symbol"].to_list(), bars["dt"].to_list()))

    rows: list[dict] = []
    for symbol, first_dt, last_dt in span.iter_rows():
        if first_dt not in index or last_dt not in index:
            continue
        for i in range(index[first_dt], index[last_dt] + 1):
            day = known[i]
            if (symbol, day) not in have:
                rows.append({"symbol": symbol, "dt": day})

    holes = pl.DataFrame(rows, schema={"symbol": pl.Utf8, "dt": pl.Date})
    per_day = (
        holes.group_by("dt").len().rename({"len": "holes"}).sort("dt")
        if holes.height
        else pl.DataFrame(schema={"dt": pl.Date, "holes": pl.Int64})
    )
    return HoleReport(holes.sort(["symbol", "dt"]),
                      sorted(holes["symbol"].unique().to_list()) if holes.height else [],
                      per_day)
