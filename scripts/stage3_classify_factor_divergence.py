"""第 3 段：按单一判据把 59 只分歧标的分类，并把 market_regime 的新旧差异补上。

**判据只用付费源自己的两个事实**：当日 pre_close 相对**上一根实际 bar** 的 close
偏离（价格缺口），以及直取 factor 相对上一根 bar 的跳变。价格是仲裁者，
corporate_actions 只作旁证、不参与判定——事件表是第三方 akshare 数据，
拿它当裁判等于用一个未经验证的源去裁决另一个源。

「上一根实际 bar」不设日历窗口：长期停牌的标的在复牌日前 15 个日历日内
可能只有一根 bar，按日历取窗口会把它们整只剔除（上一轮就漏了 2 只）。

**这套判据的强度必须说清楚**：它回答的是「两个候选因子里哪个与本源自己的
价格序列自洽」，不是「哪个是客观真值」。要定客观真值必须引入独立行情源。
"""
import glob
import json
from datetime import date
from pathlib import Path

import polars as pl

from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

EPS = 0.005   # 缺口/跳变的"动了没有"阈值
TOL = 0.01    # 两者一致性的容差

q = WarehouseQuery(Warehouse(Path("warehouse")))
big = pl.read_parquet("artifacts/stage3_factor_diffs.parquet")
firsts = big.group_by("symbol").agg(pl.col("dt").min().alias("dt")).sort("symbol")
actions = q.corporate_actions()

rows = []
for sym, d in zip(firsts["symbol"].to_list(), firsts["dt"].to_list()):
    # 不设日历窗口：取该日及之前的全部 bar，用最后两根
    bars = q.paid_daily([sym], date(1990, 1, 1), d,
                        columns=["dt", "close", "pre_close", "factor"]).sort("dt")
    if bars.height < 2:
        rows.append({"symbol": sym, "dt": d, "verdict": "无法判定",
                     "reason": "该日及之前全历史不足两根 bar"})
        continue
    cur, prev = bars.row(-1, named=True), bars.row(-2, named=True)
    gap = abs(cur["pre_close"] - prev["close"]) / prev["close"]
    fjump = abs(cur["factor"] - prev["factor"]) / prev["factor"]
    implied = gap / (1 - gap) if gap < 1 else None
    ev = actions.filter((pl.col("symbol") == sym) & (pl.col("ex_date") == d))

    if gap < EPS and fjump < EPS:
        verdict, reason = "新值自洽", "价格无缺口、因子不跳；旧值却调整了"
    elif gap >= EPS and fjump < EPS:
        verdict, reason = "新值不自洽", "价格有真实除权缺口，但直取因子不跳"
    elif gap < EPS and fjump >= EPS:
        verdict, reason = "新值不自洽", "价格无缺口，但直取因子跳了"
    elif implied and abs(fjump - implied) / implied <= TOL:
        verdict, reason = "新值自洽", "因子跳变精确匹配价格缺口"
    else:
        verdict, reason = "新值不自洽", "因子跳变与价格缺口幅度不符"

    rows.append({
        "symbol": sym, "dt": d, "verdict": verdict, "reason": reason,
        "price_gap": round(gap, 6), "factor_jump": round(fjump, 6),
        "implied_jump": round(implied, 6) if implied else None,
        "prev_bar_dt": prev["dt"],
        "has_action": ev.height > 0,
        "stock_per_share": float(ev["stock_per_share"][0]) if ev.height else None,
        "cash_per_share": float(ev["cash_per_share"][0]) if ev.height else None,
    })

r = pl.DataFrame(rows)
pl.Config.set_tbl_rows(70)
print(r.select("symbol", "dt", "verdict", "reason", "price_gap", "factor_jump",
               "implied_jump", "has_action"))
counts = r.group_by(["verdict", "reason"]).len().sort("len", descending=True)
print("\n分类计数:"); print(counts)
summary = {"symbols": r.height} | {
    v: int(r.filter(pl.col("verdict") == v).height)
    for v in ["新值自洽", "新值不自洽", "无法判定"]
}
summary["判据"] = ("价格缺口 vs 因子跳变，阈值 0.5%，一致性容差 1%；"
                   "只用付费源自身两列，corporate_actions 仅作旁证")
summary["强度说明"] = ("回答的是「哪个候选因子与本源价格序列自洽」，"
                       "不是「哪个是客观真值」——后者需独立行情源")
print("\n" + json.dumps(summary, indent=2, ensure_ascii=False))
r.write_parquet("artifacts/stage3_divergence_causes.parquet")
with open("artifacts/stage3_divergence_causes.json", "w") as fh:
    json.dump(summary, fh, indent=2, ensure_ascii=False)

# --- market_regime 新旧差异（评审指出的空白）---
def load(p):
    return pl.concat([pl.read_parquet(f) for f in
                      sorted(glob.glob(p + "/**/*.parquet", recursive=True))])

old_r = load("/data/qsys/warehouse/backups/market_regime_before_st_truth")
new_r = load("/data/qsys/warehouse/market_regime")
j = old_r.join(new_r, on="dt", how="inner", suffix="_new")
metrics = [c for c in old_r.columns if c != "dt"]
mr = {"old_rows": old_r.height, "new_rows": new_r.height, "common_dates": j.height,
      "only_in_old": old_r.join(new_r, on="dt", how="anti").height,
      "only_in_new": new_r.join(old_r, on="dt", how="anti").height, "fields": {}}
for c in metrics:
    a, b = pl.col(c), pl.col(c + "_new")
    both_null = a.is_null() & b.is_null()
    differs = (~both_null) & (a.is_null() | b.is_null() | (a != b))
    d = j.filter(differs)
    rel = None
    if d.height and old_r[c].dtype in (pl.Float64, pl.Float32):
        rel = float(d.select(((b - a).abs() / a.abs()).max()).item() or 0.0)
    mr["fields"][c] = {"diff_dates": d.height, "max_rel": rel}
print("\nmarket_regime 新旧差异:")
print(json.dumps(mr, indent=2, ensure_ascii=False))
with open("artifacts/stage2_market_regime_diff.json", "w") as fh:
    json.dump(mr, fh, indent=2, ensure_ascii=False)

# --- 那 10 行只在旧表的，落盘备查 ---
syms = ['000016.SZ','002731.SZ','002870.SZ','002998.SZ','301139.SZ',
        '600825.SH','600929.SH','605577.SH','688291.SH','688432.SH']
leg = q.legacy_daily_bars(syms, date(2026, 9, 9), date(2026, 9, 9)).sort("symbol")
leg.write_parquet("artifacts/stage1_only_in_legacy.parquet")
print("\n只在旧表的 10 行已落盘:"); print(leg)

# --- 那 146 只旧表没有的标的，看看是什么性质 ---
only_new = pl.read_parquet("artifacts/stage2_only_in_new.parquet")
early = only_new.filter(pl.col("dt") <= date(2026, 9, 9))
esyms = early["symbol"].unique().sort().to_list()
nm = q.paid_daily(esyms, date(2026, 1, 1), date(2026, 9, 11), columns=["symbol", "name"])
last = nm.group_by("symbol").agg(pl.col("name").last())
st_like = last.filter(pl.col("name").str.contains("ST") | pl.col("name").str.contains("退"))
print(f"\n旧表缺的 {len(esyms)} 只里，2026 年名称含 ST/退 的: {st_like.height}")
print(last.head(20))
with open("artifacts/stage1_missing_symbols.json", "w") as fh:
    json.dump({"symbols": esyms, "count": len(esyms),
               "st_or_delisting_named": st_like.height,
               "sample_names": last.head(30).to_dicts()}, fh,
              indent=2, ensure_ascii=False, default=str)
print("\nDONE")
