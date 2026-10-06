"""一分钟线前导零成交量的可复用识别逻辑。"""

from __future__ import annotations

import polars as pl


def leading_zero_cases(frame: pl.DataFrame, *, time_column: str = "ts") -> pl.DataFrame:
    """返回首行零成交、随后有成交的标的日及连续前导行数。

    只描述形状，不在这里判定真假。调用方必须结合来源与独立证据裁决；尤其
    不能把单根零成交量自动当成填充。
    """
    result_schema = {
        "symbol": pl.Utf8,
        "source": pl.Utf8,
        "day": pl.Date,
        "first_ts": pl.Datetime("us"),
        "first_positive_ts": pl.Datetime("us"),
        "leading_rows": pl.UInt32,
    }
    if frame.height == 0:
        return pl.DataFrame(schema=result_schema)
    ordered = frame.sort(["symbol", time_column])
    stats = ordered.group_by("symbol", maintain_order=True).agg(
        pl.col("source").first(),
        pl.col(time_column).first().alias("first_ts"),
        pl.col("volume").first().alias("first_volume"),
        pl.col(time_column).filter(pl.col("volume") > 0).min()
        .alias("first_positive_ts"),
    ).filter(
        (pl.col("first_volume") == 0) & pl.col("first_positive_ts").is_not_null()
    )
    if stats.height == 0:
        return pl.DataFrame(schema=result_schema)
    counts = (
        ordered.join(stats.select("symbol", "first_positive_ts"), on="symbol")
        .filter(pl.col(time_column) < pl.col("first_positive_ts"))
        .group_by("symbol")
        .len(name="leading_rows")
    )
    return (
        stats.join(counts, on="symbol")
        .with_columns(pl.col("first_ts").dt.date().alias("day"))
        .select(*result_schema)
        .cast(result_schema)
    )
