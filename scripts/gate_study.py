#!/usr/bin/env python3
"""S7 生态闸门研究：被闸门拒掉的交易，如果买了会怎样。

闸门每天都在拒单，但从没人算过它值不值。两种测法：

1. **完整回测对照**（`--no-gate` 跑一遍，和开闸门的那次比）——反映真实资金
   路径，但一笔之差会改变之后所有仓位，样本小的时候噪声很大。
2. **逐笔反事实**（本脚本）——对回测留痕 `gate_decisions.csv` 里的**每一条**
   判定，用同一套退出规则单独模拟一笔固定金额的交易，互不影响。
   放行组与被拒组的收益分布直接可比。

退出规则照搬策略：11:25 高开走 `legacy_exit`、低开走 `low_1125`；14:25 低开
无条件走 `low_1425`；14:50 高开再走一次 `legacy_exit`。T+1 才可卖，所以从
入场次日开始判。超过 `--horizon` 个交易日仍没走，按当时收盘价平掉并标记。

**拿不到分钟价的时刻直接跳过**，不用日线冒充；整笔都拿不到价就记为无法评估，
不猜。
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path

import polars as pl

from qsys.core.calendar import AShareCalendar
from qsys.data.cutover import CutoverConfig, CutoverMarketData
from qsys.data.market_feed import MarketFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.strategy.structural.exits import (
    dynamic_ma5,
    legacy_exit,
    low_1125,
    low_1425,
)

CHECKPOINTS = (time(11, 25), time(14, 25), time(14, 50))
_MA_BARS = 4


def _decide(tag: str, at: time, price: float, limit_up: float | None,
            entry: float, ma5: float | None):
    branch = tag.split("_", 1)[0]
    if at == time(11, 25):
        return low_1125(price, limit_up, entry) if branch == "LOW" \
            else legacy_exit(price, limit_up, entry, ma5)
    if at == time(14, 25):
        return low_1425(price, limit_up, entry) if branch == "LOW" else None
    if at == time(14, 50):
        return legacy_exit(price, limit_up, entry, ma5) if branch == "HIGH" else None
    return None


def replay(
    market: MarketFeed, closes: dict[tuple[str, date], float],
    sessions: list[date], symbol: str, tag: str, entry_day: date,
    entry_price: float, horizon: int,
) -> dict:
    """单笔交易的反事实：从 T+1 起按检查点规则退出。"""
    start = sessions.index(entry_day)
    for offset in range(1, horizon + 1):
        if start + offset >= len(sessions):
            break
        day = sessions[start + offset]
        bound = market.bind(datetime.combine(day, time(15, 0)))
        limits = bound.limit_up_price([symbol], day)
        recent = [closes[(symbol, d)] for d in sessions[max(0, start + offset - _MA_BARS):start + offset]
                  if (symbol, d) in closes]
        for at in CHECKPOINTS:
            quotes = bound.intraday([symbol], day, at)
            quote = quotes.get(symbol)
            if quote is None or quote.price <= 0:
                continue
            decision = _decide(tag, at, quote.price, limits.get(symbol),
                               entry_price, dynamic_ma5(recent, quote.price))
            if decision is not None and decision.should_exit:
                return {"exit_day": day, "exit_at": at.isoformat(),
                        "exit_price": quote.price, "reason": decision.reason,
                        "return_pct": 100.0 * (quote.price / entry_price - 1.0)}
    # 到期未触发：按最后一个可得收盘价平掉，明确标记
    for offset in range(min(horizon, len(sessions) - start - 1), 0, -1):
        day = sessions[start + offset]
        close = closes.get((symbol, day))
        if close:
            return {"exit_day": day, "exit_at": "15:00", "exit_price": close,
                    "reason": "HORIZON", "return_pct": 100.0 * (close / entry_price - 1.0)}
    return {"exit_day": None, "exit_at": None, "exit_price": None,
            "reason": "NO_PRICE", "return_pct": None}


def describe(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    series = pl.Series(values)
    return {
        "n": len(values),
        "mean": round(float(series.mean()), 3),
        "p50": round(float(series.median()), 3),
        "win_rate": round(float((series > 0).mean()), 3),
        "sum": round(float(series.sum()), 2),
    }


def run(warehouse: Path, decisions: pl.DataFrame, horizon: int) -> dict:
    wh = Warehouse(warehouse)
    query = WarehouseQuery(wh)
    market_data = CutoverMarketData(query, CutoverConfig.from_env())
    sessions = sorted(market_data.market_regime()["dt"].to_list())
    calendar = AShareCalendar(sessions)
    market = MarketFeed(query, calendar, market_data)

    days = sorted(set(decisions["date"].to_list()))
    lo, hi = min(days), max(days)
    frame = market_data.daily_bars(None, lo, sessions[-1])
    closes = {
        (row["symbol"], row["dt"]): row["close"]
        for row in frame.select("symbol", "dt", "close").iter_rows(named=True)
    }
    opens = {
        (row["symbol"], row["dt"]): row["open"]
        for row in frame.select("symbol", "dt", "open").iter_rows(named=True)
    }

    trades: list[dict] = []
    for row in decisions.iter_rows(named=True):
        day, symbol = row["date"], row["symbol"]
        entry = opens.get((symbol, day))
        if entry is None:
            trades.append({**row, "return_pct": None, "reason": "NO_OPEN"})
            continue
        outcome = replay(market, closes, sessions, symbol, row["strategy_tag"],
                         day, entry, horizon)
        trades.append({**row, "entry_price": entry, **outcome})

    by_allow: dict[bool, list[float]] = defaultdict(list)
    by_tag: dict[tuple, list[float]] = defaultdict(list)
    by_state: dict[str, list[float]] = defaultdict(list)
    unpriced = 0
    for t in trades:
        if t["return_pct"] is None:
            unpriced += 1
            continue
        by_allow[bool(t["real_allow"])].append(t["return_pct"])
        by_tag[(t["strategy_tag"], bool(t["real_allow"]))].append(t["return_pct"])
        if not t["real_allow"]:
            by_state[str(t["s7_state"])].append(t["return_pct"])
    return {
        "sample": {"start": str(lo), "end": str(hi), "decisions": decisions.height,
                   "unpriced": unpriced, "horizon": horizon},
        "allowed": describe(by_allow[True]),
        "rejected": describe(by_allow[False]),
        "by_tag": {f"{tag}|{'放行' if allow else '拒绝'}": describe(v)
                   for (tag, allow), v in sorted(by_tag.items())},
        "rejected_by_state": {k: describe(v) for k, v in sorted(by_state.items())},
        "trades": trades,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--decisions", type=Path, required=True,
                        help="回测产物 gate_decisions.csv")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--horizon", type=int, default=20,
                        help="最多持有多少个交易日，超过按收盘价平掉")
    parser.add_argument("--market-data-facade", choices=("legacy", "canonical"), default=None)
    args = parser.parse_args()
    if args.market_data_facade:
        os.environ["QSYS_MARKET_DATA_FACADE"] = args.market_data_facade

    decisions = pl.read_csv(args.decisions, try_parse_dates=True)
    report = run(args.warehouse, decisions, args.horizon)
    report["generated_at"] = datetime.now().isoformat(timespec="seconds")
    args.out.mkdir(parents=True, exist_ok=True)
    trades = pl.DataFrame(report.pop("trades"))
    trades.write_csv(args.out / "gate_trades.csv")
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    (args.out / "gate_study.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
