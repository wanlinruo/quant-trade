"""Task 10 Step 3：第 2 段（ST 真值）逐键差异。"""
import glob
import json

import polars as pl


def load(path: str) -> pl.DataFrame:
    files = sorted(glob.glob(path + "/**/*.parquet", recursive=True))
    return pl.concat([pl.read_parquet(f) for f in files])


def differs(a: str, b: str) -> pl.Expr:
    """两边都 null 才不算差异。

    polars 里 `null != value` 求值为 null，被 filter 当成 False，
    直接用 != 会把「一边有值一边没有」的差异静默吞掉。
    """
    both_null = pl.col(a).is_null() & pl.col(b).is_null()
    return (~both_null) & (
        pl.col(a).is_null() | pl.col(b).is_null() | (pl.col(a) != pl.col(b))
    )


old = load("/data/qsys/warehouse/backups/daily_flags_before_st_truth")
new = load("/data/qsys/warehouse/daily_flags")
print("旧表列", old.columns, flush=True)
print("新表列", new.columns, flush=True)

j = old.join(new, on=["dt", "symbol"], how="inner", suffix="_new")

fb = j.filter(differs("is_first_board", "is_first_board_new"))
lp = j.filter(differs("limit_pct", "limit_pct_new"))
# is_st 只在新表存在，join 不加后缀
st = j.filter(differs("st_inferred_final", "is_st"))
lu = j.filter(differs("is_limit_up", "is_limit_up_new"))

only_old = old.join(new, on=["dt", "symbol"], how="anti")
only_new = new.join(old, on=["dt", "symbol"], how="anti")

out = {
    "old_rows": old.height,
    "new_rows": new.height,
    "common_rows": j.height,
    "only_in_old": only_old.height,
    "only_in_new": only_new.height,
    "first_board_flips": fb.height,
    "first_board_symbols": fb["symbol"].n_unique(),
    "limit_up_flips": lu.height,
    "limit_up_symbols": lu["symbol"].n_unique(),
    "limit_pct_changes": lp.height,
    "limit_pct_symbols": lp["symbol"].n_unique(),
    "st_flips": st.height,
    "st_symbols": st["symbol"].n_unique(),
}
print(json.dumps(out, indent=2, ensure_ascii=False), flush=True)

print("\nst_source 分布:")
print(new.group_by("st_source").len().sort("len", descending=True))
print("\nST 翻转按年:")
print(st.group_by(pl.col("dt").dt.year().alias("y")).len().sort("y"))
print("\nST 翻转方向 (旧 st_inferred_final -> 新 is_st):")
print(st.group_by(["st_inferred_final", "is_st"]).len().sort("len", descending=True))
print("\n首板翻转按年:")
print(fb.group_by(pl.col("dt").dt.year().alias("y")).len().sort("y"))
print("\n首板翻转方向:")
print(fb.group_by(["is_first_board", "is_first_board_new"]).len().sort("len", descending=True))
print("\n涨跌幅档位变化 (旧 -> 新):")
print(lp.group_by(["limit_pct", "limit_pct_new"]).len().sort("len", descending=True))
print("\n只在新表的股-日按年:")
print(only_new.group_by(pl.col("dt").dt.year().alias("y")).len().sort("y"))
print("\n只在旧表的股-日:")
print(only_old.group_by(pl.col("dt").dt.year().alias("y")).len().sort("y"))

fb.write_parquet("artifacts/stage2_first_board_flips.parquet")
st.write_parquet("artifacts/stage2_st_flips.parquet")
only_new.write_parquet("artifacts/stage2_only_in_new.parquet")
with open("artifacts/stage2_summary.json", "w") as fh:
    json.dump(out, fh, indent=2, ensure_ascii=False)
print("\nDONE", flush=True)
