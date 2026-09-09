"""qsys 移植版 vs 聚宽原版：逐条对账。

选股是离散的——错一只就看得出来，比对净值严格得多。这个脚本已经查出
三个真 bug（见 docs/superpowers/plans/2026-09-09-phase2-2b-strategy-port.md
的对账记录）。

用法：把聚宽导出的目录放在当前工作目录下，与 qsys 的 selection csv 并列：
  jq/strategy_structural_v1_2_frozen_final/gate_decisions.csv
  qsys_selection.csv
  uv run python scripts/reconcile_joinquant.py
"""
from datetime import date
import polars as pl

JQ = "jq/strategy_structural_v1_2_frozen_final"
START, END = date(2023, 6, 6), date(2026, 9, 8)

def norm(s: str) -> str:
    """聚宽 002139.XSHE / 600036.XSHG -> qsys 002139.SZ / 600036.SH"""
    code, _, mkt = s.partition(".")
    return f"{code}.{'SZ' if mkt == 'XSHE' else 'SH'}"

jq = (
    pl.read_csv(f"{JQ}/gate_decisions.csv")
    .with_columns(
        pl.col("date").str.slice(0, 10).str.to_date().alias("dt"),
        pl.col("stock").map_elements(norm, return_dtype=pl.Utf8).alias("symbol"),
        pl.col("strategy_tag").alias("tag"),
        pl.col("real_allow").alias("allow"),
        pl.col("s7_state").alias("state"),
    )
    .select(["dt", "symbol", "tag", "state", "allow"])
)
qs = (
    pl.read_csv("qsys_selection.csv", try_parse_dates=True)
    .filter(pl.col("dt").is_between(START, END))
    .select(["dt", "symbol", "tag", "state", "allow"])
)

print(f"对账区间 {START} ~ {END}")
print(f"  聚宽  判定 {jq.height:>4}  通过 {jq.filter(pl.col('allow')).height:>4}")
print(f"  qsys  判定 {qs.height:>4}  通过 {qs.filter(pl.col('allow')).height:>4}")

print("\n=== 一、Gate 判定分布对比 ===")
a = jq.group_by(["tag", "state"]).len().rename({"len": "聚宽"})
b = qs.group_by(["tag", "state"]).len().rename({"len": "qsys"})
merged = a.join(b, on=["tag", "state"], how="full", coalesce=True).fill_null(0).sort(["tag", "state"])
print(merged)

print("\n=== 二、候选（股-日）集合对比：进入 Gate 的全部 ===")
jq_all = set(zip(jq["dt"], jq["symbol"]))
qs_all = set(zip(qs["dt"], qs["symbol"]))
both, only_jq, only_qs = jq_all & qs_all, jq_all - qs_all, qs_all - jq_all
print(f"  两边都有 {len(both):>4}")
print(f"  只聚宽有 {len(only_jq):>4}  <- qsys 漏选")
print(f"  只 qsys 有 {len(only_qs):>4}  <- qsys 多选")
print(f"  重合率（对聚宽）{len(both)/len(jq_all)*100:.1f}%")

print("\n=== 三、通过 Gate 的（真正会买的）===")
jq_ok = set(zip(jq.filter(pl.col("allow"))["dt"], jq.filter(pl.col("allow"))["symbol"]))
qs_ok = set(zip(qs.filter(pl.col("allow"))["dt"], qs.filter(pl.col("allow"))["symbol"]))
print(f"  两边都有 {len(jq_ok & qs_ok):>4}")
print(f"  只聚宽有 {len(jq_ok - qs_ok):>4}")
print(f"  只 qsys 有 {len(qs_ok - jq_ok):>4}")
print(f"  重合率（对聚宽）{len(jq_ok & qs_ok)/len(jq_ok)*100:.1f}%")

print("\n=== 四、同一股-日、标签是否一致 ===")
jq_tag = {(d, s): t for d, s, t in zip(jq["dt"], jq["symbol"], jq["tag"])}
qs_tag = {(d, s): t for d, s, t in zip(qs["dt"], qs["symbol"], qs["tag"])}
diff = [(k, jq_tag[k], qs_tag[k]) for k in both if jq_tag[k] != qs_tag[k]]
print(f"  标签不一致 {len(diff)} / {len(both)}")
for k, t1, t2 in diff[:5]:
    print(f"    {k[0]} {k[1]}: 聚宽={t1}  qsys={t2}")

print("\n=== 五、同一股-日、Gate 判定是否一致 ===")
jq_state = {(d, s): st for d, s, st in zip(jq["dt"], jq["symbol"], jq["state"])}
qs_state = {(d, s): st for d, s, st in zip(qs["dt"], qs["symbol"], qs["state"])}
same_tag = [k for k in both if jq_tag[k] == qs_tag[k]]
sdiff = [(k, jq_state[k], qs_state[k]) for k in same_tag if jq_state[k] != qs_state[k]]
print(f"  判定不一致 {len(sdiff)} / {len(same_tag)}（同标签的）")
from collections import Counter
for combo, n in Counter((a, b) for _, a, b in sdiff).most_common(6):
    print(f"    聚宽={combo[0]:<14} qsys={combo[1]:<14} {n} 条")

print("\n=== 六、多选/漏选按分支拆 ===")
print("  漏选（只聚宽有）：", Counter(jq_tag[k] for k in only_jq).most_common())
print("  多选（只 qsys 有）：", Counter(qs_tag[k] for k in only_qs).most_common())

print("\n=== 七、多选/漏选按时间段拆（分钟线分界 2025-09-10）===")
CUT = date(2025, 9, 10)
print(f"  漏选  早期 {sum(1 for d,_ in only_jq if d < CUT)}  近期 {sum(1 for d,_ in only_jq if d >= CUT)}")
print(f"  多选  早期 {sum(1 for d,_ in only_qs if d < CUT)}  近期 {sum(1 for d,_ in only_qs if d >= CUT)}")

print("\n=== 八、近期窗口（有分钟线，条件最接近）===")
CUT2 = date(2025, 9, 10)
jq_l = {k for k in jq_all if k[0] >= CUT2}
qs_l = {k for k in qs_all if k[0] >= CUT2}
print(f"  候选：聚宽 {len(jq_l)}  qsys {len(qs_l)}  都有 {len(jq_l&qs_l)}"
      f"  漏 {len(jq_l-qs_l)}  多 {len(qs_l-jq_l)}  重合率 {len(jq_l&qs_l)/max(len(jq_l),1)*100:.1f}%")
jq_ol = {k for k in jq_ok if k[0] >= CUT2}
qs_ol = {k for k in qs_ok if k[0] >= CUT2}
print(f"  通过：聚宽 {len(jq_ol)}  qsys {len(qs_ol)}  都有 {len(jq_ol&qs_ol)}"
      f"  漏 {len(jq_ol-qs_ol)}  多 {len(qs_ol-jq_ol)}  重合率 {len(jq_ol&qs_ol)/max(len(jq_ol),1)*100:.1f}%")
