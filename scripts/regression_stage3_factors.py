"""Task 10 Step 4：第 3 段（因子直取）差异。

**同锚归一后再比**：旧表 cum_factor 以窗口首日归一（首日恒 1.0），
新值带付费源自己的锚点，两者不同尺度，直接相减会全市场 100% 差异且毫无意义。
两个下游消费方都是锚点无关的——regime.py 用相邻日比值、feed.py 除以末日锚点。
"""
import json

import polars as pl

old = pl.read_parquet(
    "/data/qsys/warehouse/backups/adj_factors_before_direct/part.parquet"
)
new = pl.read_parquet("/data/qsys/warehouse/adj_factors/part.parquet")
lo, hi = old["dt"].min(), old["dt"].max()
new_win = new.filter(pl.col("dt").is_between(lo, hi))

j = (
    old.join(new_win, on=["symbol", "dt"], how="inner", suffix="_new")
    .sort(["symbol", "dt"])
    .with_columns(
        (pl.col("cum_factor") / pl.col("cum_factor").first().over("symbol")).alias("o"),
        (pl.col("cum_factor_new") / pl.col("cum_factor_new").first().over("symbol")).alias("n"),
        # 股本因子同样要归一：新表从 1990 年起连乘，旧表从 2023 年起，
        # 绝对值必然不同；下游 feed.py 除以末日锚点，本就只看比值。
        (pl.col("cum_share_factor")
         / pl.col("cum_share_factor").first().over("symbol")).alias("so"),
        (pl.col("cum_share_factor_new")
         / pl.col("cum_share_factor_new").first().over("symbol")).alias("sn"),
    )
    .with_columns(
        ((pl.col("n") - pl.col("o")).abs() / pl.col("o")).alias("rel"),
        ((pl.col("sn") - pl.col("so")).abs() / pl.col("so")).alias("srel"),
    )
)

big = j.filter(pl.col("rel") > 1e-2)
out = {
    "old_rows": old.height,
    "new_rows_total": new.height,
    "new_rows_in_window": new_win.height,
    "common_rows": j.height,
    "gt_1e_9": j.filter(pl.col("rel") > 1e-9).height,
    "gt_0_02pct": j.filter(pl.col("rel") > 2e-4).height,
    "gt_0_1pct": j.filter(pl.col("rel") > 1e-3).height,
    "gt_1pct": big.height,
    "gt_1pct_symbols": big["symbol"].n_unique(),
    "max_rel": float(j["rel"].max() or 0.0),
    "share_factor_gt_1e_9_normalized": j.filter(pl.col("srel") > 1e-9).height,
    "share_factor_gt_1pct_normalized": j.filter(pl.col("srel") > 1e-2).height,
    "share_factor_max_rel_normalized": float(j["srel"].max() or 0.0),
}
print(json.dumps(out, indent=2, ensure_ascii=False), flush=True)

print("\n>1% 按年:")
print(big.group_by(pl.col("dt").dt.year().alias("y")).len().sort("y"))
print("\n>1% 按板块（首字符）:")
print(big.with_columns(pl.col("symbol").str.slice(0, 2).alias("p"))
        .group_by("p").len().sort("len", descending=True).head(12))
print("\n>1% 的 59 只标的各自的行数与最大偏差:")
print(big.group_by("symbol").agg(pl.len().alias("rows"), pl.col("rel").max().alias("max_rel"))
        .sort("max_rel", descending=True).head(60))
print("\n偏差最大的 10 只:")
print(big.sort("rel", descending=True).select("symbol", "dt", "o", "n", "rel").head(10))

big.select("symbol", "dt", "o", "n", "rel").write_parquet(
    "artifacts/stage3_factor_diffs.parquet"
)
with open("artifacts/stage3_summary.json", "w") as fh:
    json.dump(out, fh, indent=2, ensure_ascii=False)
print("\nDONE", flush=True)
