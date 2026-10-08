"""停复牌事件的共同解释规则。"""

from __future__ import annotations

import polars as pl


def full_day_suspensions(events: pl.DataFrame) -> pl.DataFrame:
    """返回权威确认的全天停牌 ``(symbol, dt)``。

    ``S`` 且无 ``suspend_timing`` 才表示全天停牌；同一标的日若同时出现
    ``R``，说明当天已经复牌，不能再把整日从交易和分钟归档中删除。
    带时间段的 S 是盘中临停，也不属于全天停牌。
    """
    if events.height == 0:
        return pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date})
    resumed = (
        events.filter(pl.col("suspend_type") == "R")
        .select("symbol", "dt").unique()
    )
    return (
        events.filter(
            (pl.col("suspend_type") == "S")
            & pl.col("suspend_timing").is_null()
        )
        .select("symbol", "dt").unique()
        .join(resumed, on=["symbol", "dt"], how="anti")
    )
