"""换源回归对账：付费源 vs 冻结的旧表，逐键列差异。

**为什么要逐键**：换源必然产生差异（volume 精度、amount 舍入、北交所覆盖、
停牌占位行）。只报一个总数，就无法区分「已知的口径差」与「真的错了」。
spec §10 的三段式验收全靠这份逐键清单。命令行入口在
scripts/regression_paid_daily.py。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from qsys.data.query import WarehouseQuery
from qsys.markets.ashare import board_of

_VALUE_FIELDS = ["open", "high", "low", "close", "volume", "amount"]
_OHLC = ["open", "high", "low", "close"]


@dataclass(frozen=True, slots=True)
class SourceDiff:
    only_in_paid: pl.DataFrame
    only_in_legacy: pl.DataFrame
    value_diffs: pl.DataFrame
    summary: dict[str, int | float]


def compare_daily_sources(
    query: WarehouseQuery,
    start: date,
    end: date,
    boards: set[str] | None,
) -> SourceDiff:
    """逐 (symbol, dt, field) 比对付费源与冻结旧表。

    `boards` 为 None 时不过滤；给定集合时只保留该板块的标的——旧表从来
    不含北交所，不过滤会把 343 只全算成「只在付费源」，淹没真正的差异。
    """
    paid = query.daily_bars(None, start, end)
    legacy = query.legacy_daily_bars(None, start, end)

    if boards is not None:
        keep = pl.col("symbol").map_elements(
            lambda x: board_of(x.split(".")[0]), return_dtype=pl.Utf8
        ).is_in(list(boards))
        paid = paid.filter(keep)
        legacy = legacy.filter(keep)

    legacy = legacy.with_columns(pl.col("volume").cast(pl.Float64))

    keys = ["symbol", "dt"]
    only_paid = paid.join(legacy.select(keys), on=keys, how="anti").sort(keys)
    only_legacy = legacy.join(paid.select(keys), on=keys, how="anti").sort(keys)

    common = paid.join(legacy, on=keys, how="inner", suffix="_legacy")
    diffs: list[pl.DataFrame] = []
    for field in _VALUE_FIELDS:
        other = f"{field}_legacy"
        # null 的比较要显式写。polars 里 `null != 有值` 求值为 null，
        # 而 filter 把 null 谓词当 False——一边缺值一边有值的差异会被静默
        # 吞掉，既不进 value_diffs 也不计入 summary，还不报错。
        both_null = pl.col(field).is_null() & pl.col(other).is_null()
        differs = (~both_null) & (
            pl.col(field).is_null() | pl.col(other).is_null()
            | (pl.col(field) != pl.col(other))
        )
        hit = common.filter(differs)
        if hit.height == 0:
            continue
        diffs.append(
            hit.select(
                keys
                + [
                    pl.lit(field).alias("field"),
                    pl.col(field).alias("paid"),
                    pl.col(other).alias("legacy"),
                    (pl.col(field) - pl.col(other)).abs().alias("abs_diff"),
                ]
            )
        )
    value_diffs = (
        pl.concat(diffs, how="vertical").sort(["field", "abs_diff"], descending=[False, True])
        if diffs
        else pl.DataFrame(
            schema={"symbol": pl.Utf8, "dt": pl.Date, "field": pl.Utf8,
                    "paid": pl.Float64, "legacy": pl.Float64, "abs_diff": pl.Float64}
        )
    )

    summary = {
        "paid_rows": paid.height,
        "legacy_rows": legacy.height,
        "common_rows": common.height,
        "only_in_paid": only_paid.height,
        "only_in_legacy": only_legacy.height,
        "ohlc_diff_rows": value_diffs.filter(pl.col("field").is_in(_OHLC)).height,
        "volume_diff_rows": value_diffs.filter(pl.col("field") == "volume").height,
        "amount_diff_rows": value_diffs.filter(pl.col("field") == "amount").height,
        "max_amount_diff": float(
            value_diffs.filter(pl.col("field") == "amount")["abs_diff"].max() or 0.0
        ),
    }
    return SourceDiff(only_paid, only_legacy, value_diffs, summary)
