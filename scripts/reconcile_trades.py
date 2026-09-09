"""逐笔比对：同一只票同一天买入，两边的入场价/出场价/持仓天数差在哪。

净值对不上时先用这个——它能区分「交易本身错了」和「多做/少做了交易」。
实测后者才是主因：单笔完全一致，但多选出来的 10 笔均值 -5.58%。
"""
from datetime import date
import polars as pl

JQ = "jq/strategy_structural_v1_2_frozen_final"
LO, HI = date(2025, 9, 10), date(2026, 9, 8)

def norm(s):
    c,_,m = s.partition("."); return f"{c}.{'SZ' if m=='XSHE' else 'SH'}"

lots = (pl.read_csv(f"{JQ}/closed_lots.csv")
        .with_columns(
            pl.col("entry_time").str.slice(0,10).str.to_date().alias("entry_dt"),
            pl.col("exit_time").str.slice(0,10).str.to_date().alias("exit_dt"),
            pl.col("stock").map_elements(norm, return_dtype=pl.Utf8).alias("symbol"))
        .filter(pl.col("entry_dt").is_between(LO, HI)))

f = pl.read_csv("qsys_fills.csv", try_parse_dates=True).with_columns(
        pl.col("filled_at").dt.date().alias("dt"))
buys = f.filter(pl.col("side")=="buy")
sells = f.filter(pl.col("side")=="sell")

jq_buy = {(r["symbol"], r["entry_dt"]): r for r in lots.iter_rows(named=True)}
qs_buy = {(r["symbol"], r["dt"]): r for r in buys.iter_rows(named=True)}
common = set(jq_buy) & set(qs_buy)
print(f"聚宽平仓 {lots.height} 笔   qsys 买入 {buys.height} 笔   同股同日 {len(common)} 笔")

print("\n=== 入场价对比（同股同日）===")
bad = []
for k in sorted(common):
    a, b = jq_buy[k]["entry_price"], qs_buy[k]["price"]
    if abs(a-b)/a > 0.002: bad.append((k, a, b))
print(f"  入场价差 >0.2% 的 {len(bad)} / {len(common)}")
for k,a,b in bad[:8]: print(f"    {k[1]} {k[0]}  聚宽 {a:.3f}  qsys {b:.3f}  差 {(b/a-1)*100:+.2f}%")

print("\n=== 持仓天数与退出对比 ===")
qs_sell = {}
for r in sells.iter_rows(named=True):
    qs_sell.setdefault(r["symbol"], []).append(r)
matched = daydiff = 0
rows = []
for k in sorted(common):
    sym, ed = k
    outs = [x for x in qs_sell.get(sym, []) if x["dt"] >= ed]
    if not outs: continue
    out = min(outs, key=lambda x: x["dt"])
    matched += 1
    jq_days = jq_buy[k]["holding_trade_days"]
    rows.append((k, jq_buy[k]["exit_dt"], out["dt"], jq_buy[k]["exit_price"], out["price"],
                 jq_buy[k]["return_pct"], (out["price"]/qs_buy[k]["price"]-1)*100,
                 jq_buy[k]["exit_reason"]))
print(f"  可配对 {matched} 笔")
print(f"\n{'日期':<12}{'标的':<12}{'聚宽出场日':<12}{'qsys出场日':<12}{'聚宽收益%':>10}{'qsys收益%':>10}  退出原因")
for k, jed, qed, jp, qp, jr, qr, reason in rows[:20]:
    flag = "  <<<" if abs(jr-qr) > 3 else ""
    print(f"{str(k[1]):<12}{k[0]:<12}{str(jed):<12}{str(qed):<12}{jr:>10.2f}{qr:>10.2f}  {reason}{flag}")

if rows:
    import statistics
    jrs = [r[5] for r in rows]; qrs = [r[6] for r in rows]
    print(f"\n  聚宽平均收益 {statistics.mean(jrs):+.2f}%   qsys 平均收益 {statistics.mean(qrs):+.2f}%")
    print(f"  收益差 >3 个点的 {sum(1 for a,b in zip(jrs,qrs) if abs(a-b)>3)} / {len(rows)}")
