"""tushare_daily vs paid_daily 逐键对账。

**为什么必须逐键**：两者已实测同源，因此任何差异都值得逐条解释，而不是
用一个「一致率 99.9%」掩盖过去。2026-09-11 单日已发现 total_shares 在 4 只
A+H 股上相差上亿股（旧源是 market_cap/close 反算的），这类问题只有逐键清单
才看得见。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from qsys.data import schema
from qsys.data.query import WarehouseQuery

_KEYS = ["symbol", "dt"]
_VALUE_FIELDS = [n for n in schema.PAID_DAILY.names if n not in ("symbol", "dt", "name")]


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    only_in_tushare: pl.DataFrame
    only_in_paid: pl.DataFrame
    value_diffs: pl.DataFrame
    summary: dict[str, int | float]


def _reconcile_year(query: WarehouseQuery, lo: date, hi: date):
    """比对一年。返回 (only_ts, only_paid, value_diffs, 本年计数)。

    **逐年处理是可行性前提，不是优化。** 全历史两张表同时载入约 8.2 GiB
    （18.2M 行 x 28 列 x 8 字节 x 2），而生产节点只有 7.5 GiB——2026-09-13
    实测被 OOM killer 杀掉。单年峰值约 450 MiB，宽裕。
    仓库各处（universe.py / flags.py / build_daily_flags）都是同一套逐年模式。
    """
    ts = query.tushare_daily(None, lo, hi)
    paid = query.paid_daily(None, lo, hi)

    only_ts = ts.join(paid.select(_KEYS), on=_KEYS, how="anti").sort(_KEYS)
    only_paid = paid.join(ts.select(_KEYS), on=_KEYS, how="anti").sort(_KEYS)
    common = ts.join(paid, on=_KEYS, how="inner", suffix="_paid")

    diffs: list[pl.DataFrame] = []
    for field in _VALUE_FIELDS:
        other = f"{field}_paid"
        if field not in common.columns or other not in common.columns:
            continue
        # 两边都 null 不算差异；一边 null 一边有值算差异。
        both_null = pl.col(field).is_null() & pl.col(other).is_null()
        differs = (~both_null) & (
            pl.col(field).is_null() | pl.col(other).is_null()
            | (pl.col(field) != pl.col(other))
        )
        hit = common.filter(differs)
        if hit.height == 0:
            continue
        diffs.append(hit.select(
            _KEYS + [
                pl.lit(field).alias("field"),
                pl.col(field).alias("tushare"),
                pl.col(other).alias("paid"),
                (pl.col(field) - pl.col(other)).abs().alias("abs_diff"),
            ]
        ))

    year_diffs = (
        pl.concat(diffs, how="vertical") if diffs else _empty_diffs()
    )
    counts = {
        "tushare_rows": ts.height,
        "paid_rows": paid.height,
        "common_rows": common.height,
        "only_in_tushare": only_ts.height,
        "only_in_paid": only_paid.height,
    }
    return only_ts, only_paid, year_diffs, counts


def _empty_diffs() -> pl.DataFrame:
    return pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date, "field": pl.Utf8,
                                "tushare": pl.Float64, "paid": pl.Float64,
                                "abs_diff": pl.Float64})


def reconcile(query: WarehouseQuery, start: date, end: date) -> ReconcileReport:
    """逐年比对，跨年汇总。接口与产物与逐次全量版完全一致。

    **不要改回一次性载入全历史**——那会在生产节点 OOM（见 `_reconcile_year`）。
    """
    only_ts_parts: list[pl.DataFrame] = []
    only_paid_parts: list[pl.DataFrame] = []
    diff_parts: list[pl.DataFrame] = []
    totals = {"tushare_rows": 0, "paid_rows": 0, "common_rows": 0,
              "only_in_tushare": 0, "only_in_paid": 0}

    for year in range(start.year, end.year + 1):
        lo = max(start, date(year, 1, 1))
        hi = min(end, date(year, 12, 31))
        if lo > hi:
            continue
        only_ts, only_paid, year_diffs, counts = _reconcile_year(query, lo, hi)
        for k, v in counts.items():
            totals[k] += v
        if only_ts.height:
            only_ts_parts.append(only_ts)
        if only_paid.height:
            only_paid_parts.append(only_paid)
        if year_diffs.height:
            diff_parts.append(year_diffs)

    only_ts_all = (pl.concat(only_ts_parts, how="vertical").sort(_KEYS)
                   if only_ts_parts else pl.DataFrame())
    only_paid_all = (pl.concat(only_paid_parts, how="vertical").sort(_KEYS)
                     if only_paid_parts else pl.DataFrame())
    value_diffs = (
        pl.concat(diff_parts, how="vertical").sort(["field", "abs_diff"],
                                                   descending=[False, True])
        if diff_parts else _empty_diffs()
    )

    by_field = (
        value_diffs.group_by("field").len().sort("len", descending=True).to_dicts()
        if value_diffs.height else []
    )
    summary = dict(totals)
    summary["diff_rows"] = value_diffs.height
    summary["diff_by_field"] = {d["field"]: d["len"] for d in by_field}
    return ReconcileReport(only_ts_all, only_paid_all, value_diffs, summary)
