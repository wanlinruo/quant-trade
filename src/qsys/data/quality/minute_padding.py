"""一分钟线前导零成交量的可复用识别逻辑。"""

from __future__ import annotations

import math

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


def zero_prefix_issues(
    frame: pl.DataFrame, daily: pl.DataFrame, *, time_column: str = "ts",
) -> pl.DataFrame:
    """核对前导零量的值一致性；连续无成交根数和来源都不是失败条件。

    参考仅为同证券、同日Tushare直取open/pre_close/volume。缺失或不一致
    明确失败，不填值、不裁行、不从价格反推成交，更不改变任何市场原值。
    """
    result_schema = {"symbol": pl.String, "day": pl.Date, "reason": pl.String}
    if frame.height == 0:
        return pl.DataFrame(schema=result_schema)
    ordered = frame.with_columns(pl.col(time_column).dt.date().alias("_day")).sort(
        ["symbol", "_day", time_column]
    )
    first = ordered.group_by(["symbol", "_day"]).agg(pl.col("volume").first())
    wanted = first.filter(
        pl.col("volume").is_null() | ~pl.col("volume").is_finite() | (pl.col("volume") <= 0)
    ).select("symbol", "_day")
    if wanted.height == 0:
        return pl.DataFrame(schema=result_schema)
    if not {"open", "high", "low", "close", "amount"} <= set(frame.columns) \
            or not {"symbol", "dt", "open", "pre_close", "volume"} <= set(daily.columns):
        raise ValueError("zero-prefix consistency requires complete minute/daily fields")
    references = {}
    for row in daily.iter_rows(named=True):
        references.setdefault((row["symbol"], row["dt"]), []).append(row)

    def finite(value):
        return value is not None and not isinstance(value, bool) and math.isfinite(value)

    problems = []
    candidates = ordered.join(wanted, on=["symbol", "_day"], how="inner", nulls_equal=True)
    for (symbol, day), group in candidates.partition_by(["symbol", "_day"], as_dict=True).items():
        reasons = []
        refs = references.get((symbol, day), [])
        ref = refs[0] if len(refs) == 1 else None
        if ref is None or not all(finite(ref[k]) for k in ("open", "pre_close", "volume")):
            reasons.append("missing_or_ambiguous_daily_reference")
        elif ref["pre_close"] <= 0 or ref["volume"] < 0:
            reasons.append("invalid_daily_reference")
        rows = group.sort(time_column).to_dicts()
        positive = [i for i, row in enumerate(rows) if finite(row["volume"]) and row["volume"] > 0]
        cut = positive[0] if positive else len(rows)
        prefix = rows[:cut]
        if group[time_column].null_count() or group[time_column].n_unique() != group.height:
            reasons.append("missing_or_duplicate_timestamp")
        for row in prefix:
            fields = [row[k] for k in ("open", "high", "low", "close", "volume", "amount")]
            if not all(finite(value) for value in fields):
                reasons.append("missing_or_nonfinite_prefix_value")
                continue
            if row["volume"] != 0 or row["amount"] != 0:
                reasons.append("nonzero_prefix_volume_or_amount")
            prices = [row[k] for k in ("open", "high", "low", "close")]
            if prices[0] <= 0 or len(set(prices)) != 1:
                reasons.append("invalid_or_nonflat_prefix_price")
            if ref is not None and finite(ref["pre_close"]) and any(
                price != ref["pre_close"] for price in prices
            ):
                reasons.append("prefix_reference_price_mismatch")
        if ref is not None and finite(ref["volume"]):
            if not positive and ref["volume"] > 0:
                reasons.append("missing_positive_volume")
            if positive and (
                not finite(rows[cut]["open"]) or rows[cut]["open"] != ref["open"]
                or ref["volume"] <= 0
            ):
                reasons.append("first_traded_open_mismatch")
        if reasons:
            problems.append({"symbol": symbol, "day": day, "reason": ",".join(sorted(set(reasons)))})
    return pl.DataFrame(problems, schema=result_schema) if problems else pl.DataFrame(schema=result_schema)
