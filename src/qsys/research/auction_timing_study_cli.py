"""集合竞价挂单时点研究（只读）：竞价内挂单 vs 09:25 后挂单 vs 09:31 下单。

原策略 09:26 下单、回测按当日开盘价成交；实盘 09:25 后提交的委托不参加开盘集合
竞价，拿不到开盘价。实盘只有三条路：

- A 竞价内挂单：在 09:22 … 09:24:50 用当时的虚拟开盘价与匹配量决策，按开盘价成交；
- B 09:25 后挂单：用最终开盘价与竞价量决策，09:30 连续竞价成交（09:31 分钟线）；
- C 09:31 下单：开盘第一分钟后下单（09:32 分钟线）。

A 用策略自己的选股代码重放，只把 ``session_open`` / ``auction_volume`` 换成该时点
的虚拟值（竞价快照第一档：买一 = 卖一 = 虚拟价，买一量 = 匹配量，单位手）。基准是
回测实际使用的最终数据。成本一律以 bp 计，正数表示买贵。

局限：竞价快照只覆盖首板次日候选、只有第一档（涨停开盘能否排上队无法判断）；分钟线
看不到买卖价差，B/C 成本是下限。

用法：
  uv run python scripts/auction_timing_study.py --warehouse warehouse \\
      --market-data-facade canonical --out var/auction-timing-study
"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path

import polars as pl

DECISION_TIMES = (time(9, 22), time(9, 23), time(9, 24), time(9, 24, 30), time(9, 24, 50))
LIMIT_MARKUPS = (0.0, 0.005, 0.01, 0.02, 0.03, None)  # None = 直接挂涨停价
AUCTION_AT = time(9, 30)


# ---- 纯计算 ----

def virtual_at(ticks: pl.DataFrame, moment: time) -> dict[tuple[str, date], tuple[float, int]]:
    """每个标的日在 ``moment``（含）之前最后一笔有效虚拟价快照：(虚拟价, 匹配量手)。"""
    rows = (
        ticks.filter(
            (pl.col("ts").dt.time() <= moment)
            & (pl.col("bid1") == pl.col("ask1")) & (pl.col("bid1") > 0)
        )
        .sort("ts")
        .group_by("symbol", "dt", maintain_order=True)
        .last()
    )
    return {(r["symbol"], r["dt"]): (float(r["bid1"]), int(r["bid_vol1"]))
            for r in rows.iter_rows(named=True)}


def limit_price(virtual: float, markup: float | None, limit_up: float) -> float:
    """虚拟价上浮后向下取整到分，不超过涨停价；markup=None 表示直接挂涨停价。"""
    if markup is None:
        return limit_up
    return min(math.floor(virtual * (1 + markup) * 100 + 1e-6) / 100, limit_up)


def auction_fill(*, open_: float, limit: float, limit_up: float) -> str:
    if open_ >= limit_up - 1e-6:
        return "limit_up_open"
    return "filled" if open_ <= limit + 1e-9 else "missed"


def bps(price: float | None, base: float | None) -> float | None:
    if price is None or not base:
        return None
    return (price / base - 1) * 1e4


def vwap(*, amount: float, volume_lots: float) -> float | None:
    return amount / (volume_lots * 100) if volume_lots else None


def describe(values: list[float]) -> dict:
    data = sorted(v for v in values if v is not None)
    if not data:
        return {"n": 0}

    def q(p: float) -> float:
        k = (len(data) - 1) * p
        lo, hi = math.floor(k), math.ceil(k)
        return data[lo] + (data[hi] - data[lo]) * (k - lo)

    return {
        "n": len(data), "p10": q(0.1), "p50": q(0.5), "p90": q(0.9),
        "mean": sum(data) / len(data), "mean_abs": sum(abs(v) for v in data) / len(data),
    }


# ---- 重放 ----

class _VirtualBound:
    """只替换开盘价与竞价量，其余行情访问原样委托给真实的 BoundMarket。"""

    def __init__(self, inner, snapshots: dict):
        self._inner, self._snapshots = inner, snapshots

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def session_open(self, symbols, day):
        return {s: self._snapshots[(s, day)][0] for s in symbols if (s, day) in self._snapshots}

    def auction_volume(self, symbols, day):
        return {s: self._snapshots[(s, day)][1] for s in symbols if (s, day) in self._snapshots}


class VirtualAuctionMarket:
    def __init__(self, inner, snapshots: dict):
        self._inner, self._snapshots = inner, snapshots

    def bind(self, as_of: datetime):
        return _VirtualBound(self._inner.bind(as_of), self._snapshots)


def replay(deps, market, days: list[date], previous: dict[date, date]) -> dict:
    from qsys.strategy.structural.selection import MissingAuctionDataError, StructuralSelection

    selection = StructuralSelection(deps.feed, market, deps.query)
    picks, routed, errors = {}, {}, {}
    for day in days:
        try:
            chosen = selection.select(day, previous[day])
        except MissingAuctionDataError as exc:
            errors[str(day)] = str(exc)
            continue
        # routed：通过入场分档（低开/高开价格带与竞价量门槛）的全部候选，闸门之前。
        # 闸门只看 T-1 的市场生态，与决策时点无关，所以时点的影响在这一层比较。
        for decision in selection.decisions:
            routed[(decision.symbol, day)] = decision.tag
        for pick in chosen:
            picks[(pick.symbol, day)] = pick.tag
    return {"picks": picks, "routed": routed, "errors": errors}


def consistency(picks: dict, final: dict) -> dict:
    both = picks.keys() & final.keys()
    extra, missed = picks.keys() - final.keys(), final.keys() - picks.keys()
    return {"picks": len(picks), "final": len(final), "both": len(both),
            "extra": len(extra), "missed": len(missed),
            "recall": len(both) / len(final) if final else None,
            "precision": len(both) / len(picks) if picks else None,
            "extra_by_tag": dict(Counter(picks[k] for k in extra)),
            "missed_by_tag": dict(Counter(final[k] for k in missed))}


def mismatch_causes(picks, final, snapshots, opens, final_volume) -> dict:
    """选错的标的日：时点虚拟价/匹配量相对最终开盘价/竞价量的比值分布。"""
    out = {}
    for label, keys in (("missed", final.keys() - picks.keys()),
                        ("extra", picks.keys() - final.keys())):
        price, volume = [], []
        for key in keys:
            if key not in snapshots:
                continue
            p_t, v_t = snapshots[key]
            if opens.get(key):
                price.append(bps(p_t, opens[key]))
            if final_volume.get(key):
                volume.append(v_t / final_volume[key])
        out[label] = {"n": len(keys), "virtual_vs_open_bps": describe(price),
                      "matched_vs_final_volume": describe(volume)}
    return out


def _final_auction_volume(deps, ticks, days) -> dict:
    out = {}
    for day in days:
        symbols = sorted(ticks.filter(pl.col("dt") == day)["symbol"].unique().to_list())
        bound = deps.market.bind(datetime.combine(day, AUCTION_AT))
        out.update({(s, day): v for s, v in bound.auction_volume(symbols, day).items()})
    return out


# ---- 主流程 ----

def _minute_bars(query, keys: list[tuple[str, date]], label: time) -> dict:
    out = {}
    for day in sorted({d for _s, d in keys}):
        symbols = sorted({s for s, d in keys if d == day})
        moment = datetime.combine(day, label)
        bars = query.bar_minute_1m(symbols, moment, moment,
                                   columns=["symbol", "ts", "open", "high", "low", "volume", "amount"])
        for r in bars.iter_rows(named=True):
            out[(r["symbol"], day)] = r
    return out


def run(warehouse: Path, start: date | None, end: date | None) -> dict:
    from qsys.strategy.deps import build_deps

    deps, query, calendar, sessions, _universe, _boards = build_deps(warehouse)
    ticks = query.auction_ticks(None, start or date(2000, 1, 1), end or date(2100, 1, 1))
    days = sorted(set(ticks["dt"].to_list()))
    previous = {day: calendar.prev_session(day) for day in days}

    baseline = replay(deps, deps.market, days, previous)
    final, final_routed = baseline["picks"], baseline["routed"]

    opens, limit_ups = {}, {}
    for day in days:
        symbols = sorted(ticks.filter(pl.col("dt") == day)["symbol"].unique().to_list())
        bound = deps.market.bind(datetime.combine(day, AUCTION_AT))
        opens.update({(s, day): p for s, p in bound.session_open(symbols, day).items()})
        limit_ups.update({(s, day): p for s, p in bound.limit_up_price(symbols, day).items()})

    report: dict = {
        "sample": {"start": str(days[0]), "end": str(days[-1]), "days": len(days),
                   "symbol_days": ticks.select("symbol", "dt").unique().height,
                   "final_picks": len(final), "final_tags": dict(Counter(final.values())),
                   "final_routed": len(final_routed),
                   "final_routed_tags": dict(Counter(final_routed.values())),
                   "baseline_errors": baseline["errors"]},
        "A": {}, "B": {}, "C": {},
    }

    final_volume = _final_auction_volume(deps, ticks, days)
    for moment in DECISION_TIMES:
        snapshots = virtual_at(ticks, moment)
        virtual = replay(deps, VirtualAuctionMarket(deps.market, snapshots), days, previous)
        picks = virtual["routed"]
        entry = {
            "allowed": consistency(virtual["picks"], final),
            "routed": consistency(picks, final_routed),
            "mismatch_causes": mismatch_causes(
                picks, final_routed, snapshots, opens, final_volume),
            "decision_drift_bps": describe([bps(opens.get(k), snapshots[k][0]) for k in picks]),
            "errors": virtual["errors"], "limits": {},
        }
        for markup in LIMIT_MARKUPS:
            outcomes, costs = Counter(), []
            for key in picks:
                if key not in opens or key not in limit_ups:
                    outcomes["no_open_or_limit"] += 1
                    continue
                limit = limit_price(snapshots[key][0], markup, limit_ups[key])
                outcome = auction_fill(open_=opens[key], limit=limit, limit_up=limit_ups[key])
                outcomes[outcome] += 1
                if outcome == "filled":
                    costs.append(bps(opens[key], snapshots[key][0]))
            decided = outcomes["filled"] + outcomes["missed"]
            entry["limits"]["limit_up" if markup is None else f"+{markup:.1%}"] = {
                "outcomes": dict(outcomes),
                "fill_rate": outcomes["filled"] / decided if decided else None,
                "cost_vs_decision_bps": describe(costs),
            }
        report["A"][moment.strftime("%H:%M:%S")] = entry

    for name, label in (("B", time(9, 31)), ("C", time(9, 32))):
        bars = _minute_bars(query, list(final_routed), label)
        rows = {"open": [], "vwap": [], "high": []}
        for key in final_routed:
            bar, base = bars.get(key), opens.get(key)
            if bar is None or base is None:
                continue
            rows["open"].append(bps(bar["open"], base))
            rows["vwap"].append(bps(vwap(amount=bar["amount"], volume_lots=bar["volume"]), base))
            rows["high"].append(bps(bar["high"], base))
        # 限价单近似：限价 = 开盘价上浮 x%（不超过涨停价），该分钟最低价不高于限价即视为
        # 可成交，成本按限价计（上限）。分钟线看不到盘口与排队，只作量级参考。
        limits = {}
        for markup in (0.0, 0.005, 0.01, 0.02, 0.03):
            filled, costs = 0, []
            for key in final_routed:
                bar, base = bars.get(key), opens.get(key)
                if bar is None or base is None or key not in limit_ups:
                    continue
                limit = limit_price(base, markup, limit_ups[key])
                if bar["low"] <= limit + 1e-9:
                    filled += 1
                    costs.append(bps(limit, base))
            limits[f"+{markup:.1%}"] = {"fill_rate": filled / len(final_routed) if final_routed else None,
                                        "cost_upper_bps": describe(costs)}
        report[name] = {"bar": label.strftime("%H:%M"), "limit_orders": limits,
                        "vs_open_bps": {k: describe(v) for k, v in rows.items()},
                        "missing_bars": len(final_routed) - len(rows["open"]),
                        "allowed_only": {k: describe([v for key, v in zip(final_routed, vals) if key in final])
                                         for k, vals in rows.items()} if len(rows["open"]) == len(final_routed) else None}
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--market-data-facade", choices=("legacy", "canonical"), default=None)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--out", type=Path, required=True,
                        help="产物目录；月度任务传 .../<年月>")
    parser.add_argument("--service-runs", type=Path, default=None,
                        help="给定则记录一次服务运行（月度定时任务用）")
    args = parser.parse_args(argv)
    if args.market_data_facade:
        os.environ["QSYS_MARKET_DATA_FACADE"] = args.market_data_facade
    if args.service_runs is None:
        return _study(args)
    from qsys.ops.services import ServiceRun

    with ServiceRun.open(args.service_runs, "auction-study") as service_run:
        with service_run.step("竞价挂单时点研究", interface="仓库只读",
                              table="research/auction-timing") as step:
            report = _study(args)
            sample = report["sample"] if isinstance(report, dict) else {}
            step.rows = int(sample.get("symbol_days", 0) or 0)
            step.detail = (
                f"{sample.get('start')}..{sample.get('end')}，{sample.get('days')} 个交易日、"
                f"{sample.get('symbol_days')} 个标的日，最终入档 {sample.get('final_routed')}、"
                f"最终入选 {sample.get('final_picks')}；产物 {args.out}"
            )
    return 0


def _study(args) -> dict | int:
    report = run(args.warehouse, args.start, args.end)
    report["generated_at"] = datetime.now().isoformat(timespec="seconds")
    args.out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    (args.out / "report.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    return report


if __name__ == "__main__":
    raise SystemExit(main())
