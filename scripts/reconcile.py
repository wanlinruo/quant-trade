"""两个运行目录的逐笔对账。qsys 的 `var/runs/<name>/` 对聚宽的研究导出。

两边的文件名与列名已经对齐（`daily_nav.csv` / `daily_buy_details.csv` /
`closed_lots.csv` / `gate_decisions.csv`），所以这里不需要任何格式转换——
**这正是对齐文件名的目的**：对账脚本只做比对，不做翻译。

此前这套逻辑散在临时脚本里、路径写死、跑完就丢。对账是双向的，工具也该
是双向的：任一边的目录都能当 `--a` 或 `--b`。

用法：
  uv run python scripts/reconcile.py \\
      --a var/runs/jq100k --b jq/strategy_structural_v1_2_frozen_final \\
      --out var/reconcile/jq100k
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

_EXCHANGE = {"XSHE": "SZ", "XSHG": "SH"}


def normalize(code: str) -> str:
    """`002139.XSHE` -> `002139.SZ`。已经是我们的格式就原样返回。"""
    body, _, market = str(code).partition(".")
    return f"{body}.{_EXCHANGE.get(market.upper(), market)}"


def _read(directory: Path, name: str) -> pl.DataFrame | None:
    path = directory / name
    if not path.is_file():
        return None
    frame = pl.read_csv(path, try_parse_dates=True, encoding="utf8-lossy")
    # 聚宽的导出带 BOM，第一列名会变成 "﻿date"
    return frame.rename({c: c.lstrip("﻿") for c in frame.columns})


def _with_symbol(frame: pl.DataFrame) -> pl.DataFrame:
    """统一出一列 `symbol`。我们的文件本来就有；聚宽的要从 `stock` 转。"""
    if "symbol" in frame.columns:
        return frame
    return frame.with_columns(
        pl.col("stock").map_elements(normalize, return_dtype=pl.Utf8).alias("symbol")
    )


def _as_date(frame: pl.DataFrame, column: str) -> pl.DataFrame:
    """日期列可能是 date、datetime 或 "2026-01-02 09:26:00" 字符串。"""
    if frame[column].dtype == pl.Date:
        return frame
    if frame[column].dtype == pl.Datetime:
        return frame.with_columns(pl.col(column).dt.date().alias(column))
    return frame.with_columns(
        pl.col(column).cast(pl.Utf8).str.slice(0, 10).str.to_date().alias(column)
    )


@dataclass(frozen=True, slots=True)
class Side:
    label: str
    directory: Path

    def nav(self) -> pl.DataFrame | None:
        return _read(self.directory, "daily_nav.csv")

    def buys(self, lo: date, hi: date) -> pl.DataFrame:
        frame = _read(self.directory, "daily_buy_details.csv")
        if frame is None:
            return pl.DataFrame(schema={"date": pl.Date, "symbol": pl.Utf8,
                                        "strategy_tag": pl.Utf8})
        frame = _as_date(_with_symbol(frame), "date")
        return frame.filter(pl.col("date").is_between(lo, hi)).select(
            ["date", "symbol", "strategy_tag"]
        )

    def lots(self, lo: date, hi: date) -> pl.DataFrame:
        frame = _read(self.directory, "closed_lots.csv")
        if frame is None:
            return pl.DataFrame(schema={"date": pl.Date, "symbol": pl.Utf8,
                                        "return_pct": pl.Float64})
        frame = _with_symbol(frame).rename({"entry_time": "date"})
        frame = _as_date(frame, "date")
        return frame.filter(pl.col("date").is_between(lo, hi)).select(
            ["date", "symbol", "strategy_tag", "return_pct", "exit_reason"]
            if "exit_reason" in frame.columns
            else ["date", "symbol", "strategy_tag", "return_pct"]
        )

    def gates(self, lo: date, hi: date) -> pl.DataFrame:
        frame = _read(self.directory, "gate_decisions.csv")
        if frame is None:
            return pl.DataFrame(schema={"date": pl.Date, "symbol": pl.Utf8,
                                        "s7_state": pl.Utf8, "real_allow": pl.Boolean})
        frame = _as_date(_with_symbol(frame), "date")
        return frame.filter(pl.col("date").is_between(lo, hi)).select(
            ["date", "symbol", "strategy_tag", "s7_state", "real_allow"]
        )


def _report(a: Side, b: Side, lo: date, hi: date) -> tuple[str, pl.DataFrame]:
    lines: list[str] = []
    say = lines.append
    say(f"对账区间 {lo} ~ {hi}")
    say(f"  A = {a.label}  ({a.directory})")
    say(f"  B = {b.label}  ({b.directory})")

    # --- 净值 ---
    say("\n=== 一、净值 ===")
    for side in (a, b):
        nav = side.nav()
        if nav is None or nav.height == 0:
            say(f"  {side.label:<10} （无 daily_nav.csv）")
            continue
        nav = _as_date(nav, "date").filter(pl.col("date").is_between(lo, hi)).sort("date")
        first, last = nav["total_value"][0], nav["total_value"][-1]
        say(f"  {side.label:<10} {first:>14,.0f} -> {last:>14,.0f}   "
            f"{(last / first - 1) * 100:+8.2f}%   {nav.height} 个交易日")

    # --- 买入集合 ---
    say("\n=== 二、买入（股-日）===")
    ab, bb = a.buys(lo, hi), b.buys(lo, hi)
    A = set(zip(ab["date"], ab["symbol"]))
    B = set(zip(bb["date"], bb["symbol"]))
    both, only_a, only_b = A & B, A - B, B - A
    denom = len(B) or 1
    say(f"  {a.label} {len(A)}   {b.label} {len(B)}   都有 {len(both)}   "
        f"只 A 有 {len(only_a)}   只 B 有 {len(only_b)}   重合率(对 B) "
        f"{len(both) / denom * 100:.1f}%")
    for label, group in ((f"只 {a.label} 有", only_a), (f"只 {b.label} 有", only_b)):
        for day, symbol in sorted(group):
            say(f"    [{label}] {day} {symbol}")

    # --- 逐笔收益归因 ---
    say("\n=== 三、逐笔收益（各自口径）===")
    rows: list[dict] = []
    for side, mine in ((a, A), (b, B)):
        lots = side.lots(lo, hi)
        if lots.height == 0:
            say(f"  {side.label:<10} （无 closed_lots.csv）")
            continue
        keyed = lots.with_columns(
            pl.struct(["date", "symbol"])
            .map_elements(lambda s: (s["date"], s["symbol"]) in both,
                          return_dtype=pl.Boolean)
            .alias("shared")
        )
        for scope, sub in (("全部", keyed), ("共同", keyed.filter(pl.col("shared"))),
                           ("独有", keyed.filter(~pl.col("shared")))):
            if sub.height == 0:
                continue
            mean = float(sub["return_pct"].mean())
            win = float((sub["return_pct"] > 0).mean()) * 100
            say(f"  {side.label:<10} {scope:<4} {sub.height:>4} 笔   "
                f"均值 {mean:+7.2f}%   胜率 {win:5.1f}%")
            rows.append({"side": side.label, "scope": scope, "lots": sub.height,
                         "avg_return_pct": mean, "win_rate_pct": win})

    # --- Gate 判定 ---
    say("\n=== 四、Gate 判定 ===")
    ag, bg = a.gates(lo, hi), b.gates(lo, hi)
    if ag.height and bg.height:
        joined = ag.join(bg, on=["date", "symbol"], how="inner", suffix="_b")
        say(f"  共同判定 {joined.height} 条")
        if joined.height:
            same_state = int((joined["s7_state"] == joined["s7_state_b"]).sum())
            same_allow = int((joined["real_allow"] == joined["real_allow_b"]).sum())
            say(f"    state 一致 {same_state}/{joined.height}")
            say(f"    allow 一致 {same_allow}/{joined.height}")
            bad = joined.filter(pl.col("real_allow") != pl.col("real_allow_b"))
            for r in bad.iter_rows(named=True):
                say(f"    [分歧] {r['date']} {r['symbol']:<11} {r['strategy_tag']:<13}"
                    f" A={r['s7_state']}/{r['real_allow']}"
                    f"  B={r['s7_state_b']}/{r['real_allow_b']}")
        a_only = ag.join(bg, on=["date", "symbol"], how="anti")
        b_only = bg.join(ag, on=["date", "symbol"], how="anti")
        say(f"  只 {a.label} 评估过 {a_only.height} 条   "
            f"只 {b.label} 评估过 {b_only.height} 条")
    else:
        say("  一方没有 gate_decisions.csv，跳过")

    return "\n".join(lines), pl.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="两个运行目录的逐笔对账")
    parser.add_argument("--a", required=True, help="运行目录（通常是 qsys 的）")
    parser.add_argument("--b", required=True, help="运行目录（通常是聚宽的）")
    parser.add_argument("--a-label", default="qsys")
    parser.add_argument("--b-label", default="聚宽")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", default=None, help="落盘目录；不给则只打印")
    args = parser.parse_args()

    a = Side(args.a_label, Path(args.a))
    b = Side(args.b_label, Path(args.b))
    for side in (a, b):
        if not side.directory.is_dir():
            print(f"目录不存在: {side.directory}", file=sys.stderr)
            return 2

    text, summary = _report(
        a, b, date.fromisoformat(args.start), date.fromisoformat(args.end)
    )
    print(text)

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "reconciliation.txt").write_text(text + "\n", encoding="utf-8")
        summary.write_csv(out / "reconciliation_summary.csv")
        print(f"\n已写入 {out}/reconciliation.txt 与 reconciliation_summary.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
