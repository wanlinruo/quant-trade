"""第 4 段：付费源 → Tushare 直连的 daily_flags 逐键差异。

计划 C Task 10 把派生链路从 paid_daily 切到 tushare_daily，这是第三次改动
daily_flags（前两次是 ST 真值、因子直取）。执行记录只报了终态行数，
没出新旧对账——没有对账就无法把候选池的变化归因到这次换源上。

回滚点 /data/qsys/backups/tushare-switch-20260914 存的正是切换前（付费源）的状态。
"""
import glob
import json

import polars as pl

OLD = "/data/qsys/backups/tushare-switch-20260914/daily_flags"
NEW = "/data/qsys/warehouse/daily_flags"


def load(path):
    return pl.concat([pl.read_parquet(f) for f in
                      sorted(glob.glob(path + "/**/*.parquet", recursive=True))])


def differs(a, b):
    """两边都 null 才不算差异——`null != value` 求值为 null，会被 filter 吞掉。"""
    both_null = pl.col(a).is_null() & pl.col(b).is_null()
    return (~both_null) & (
        pl.col(a).is_null() | pl.col(b).is_null() | (pl.col(a) != pl.col(b))
    )


old, new = load(OLD), load(NEW)
print("旧表 st_source:", old.group_by("st_source").len().to_dicts())
print("新表 st_source:", new.group_by("st_source").len().to_dicts())

j = old.join(new, on=["dt", "symbol"], how="inner", suffix="_new")
out = {
    "old_rows": old.height, "new_rows": new.height, "common_rows": j.height,
    "only_in_old": old.join(new, on=["dt", "symbol"], how="anti").height,
    "only_in_new": new.join(old, on=["dt", "symbol"], how="anti").height,
}
for col in ["is_st", "limit_pct", "limit_up", "is_limit_up", "is_first_board",
            "is_broken_board", "prev_close", "adj_prev_close"]:
    d = j.filter(differs(col, col + "_new"))
    out[col + "_diffs"] = d.height
    out[col + "_symbols"] = d["symbol"].n_unique() if d.height else 0

out["old_is_st_null"] = int(old["is_st"].null_count())
out["new_is_st_null"] = int(new["is_st"].null_count())
out["old_first_board"] = int(old["is_first_board"].sum())
out["new_first_board"] = int(new["is_first_board"].sum())
print(json.dumps(out, indent=2, ensure_ascii=False))

st = j.filter(differs("is_st", "is_st_new"))
print("\nST 翻转方向（旧付费源 -> 新 Tushare）:")
print(st.group_by(["is_st", "is_st_new"]).len().sort("len", descending=True))

fb = j.filter(differs("is_first_board", "is_first_board_new"))
print("\n首板翻转方向:")
print(fb.group_by(["is_first_board", "is_first_board_new"]).len().sort("len", descending=True))
print("按年:")
print(fb.group_by(pl.col("dt").dt.year().alias("y")).len().sort("y"))

fb.write_parquet("artifacts/stage4_first_board_flips.parquet")
st.write_parquet("artifacts/stage4_st_flips.parquet")
with open("artifacts/stage4_summary.json", "w") as fh:
    json.dump(out, fh, indent=2, ensure_ascii=False)
print("\nDONE")
