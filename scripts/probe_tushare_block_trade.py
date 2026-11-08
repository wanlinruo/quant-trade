#!/usr/bin/env python3
"""Tushare ``block_trade`` 探针（#608）：先实测字段、覆盖与单位，结论进 git 再写依赖代码。

背景：``A1.dimension.amount_vs_volume_close`` 要求日线 成交额/成交量 落在当日最低/最高价
之间，但日线总成交包含按协议价成交的大宗交易，会产生假阻断。修复必须用直取的大宗交易
事实扣除，而 ``block_trade`` 的 vol/amount 单位文档写法不足以直接依赖，这里用数据实证：
对同日每只有大宗交易的证券，按不同单位假设从日线总成交扣除大宗后重算均价，统计
落在 [low, high] 的比例。只读接口，不写仓库数据；凭证不打印。

用法:
    uv run python scripts/probe_tushare_block_trade.py --date 20261105 \
        --out artifacts/tushare_block_trade_probe_20261108.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Tushare daily: vol 单位“手”，amount 单位“千元”（仓库既有日线入库口径）。
DAILY_VOL_SHARES = 100.0
DAILY_AMOUNT_YUAN = 1000.0
HYPOTHESES = {
    "vol_wan_shares_amount_wan_yuan": (10000.0, 10000.0),
    "vol_shares_amount_yuan": (1.0, 1.0),
    "vol_hand_amount_thousand_yuan": (100.0, 1000.0),
}


def _load_probe():
    spec = importlib.util.spec_from_file_location("tushare_probe", ROOT / "scripts" / "tushare_probe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call_all(tok: str, api_name: str, params: dict, fields: str = "") -> dict:
    """Like tushare_probe.call but keeps every item (that one keeps a 3-row sample)."""
    import time
    import urllib.request
    body = json.dumps({"api_name": api_name, "token": tok, "params": params,
                       "fields": fields}).encode()
    started = time.time()
    request = urllib.request.Request("https://api.tushare.pro", data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as resp:
        payload = json.load(resp)
    if payload.get("code") != 0:
        return {"ok": False, "msg": payload.get("msg"), "fields": [], "items": [],
                "seconds": round(time.time() - started, 2)}
    data = payload.get("data") or {}
    return {"ok": True, "msg": "", "fields": data.get("fields") or [],
            "items": data.get("items") or [], "has_more": data.get("has_more"),
            "seconds": round(time.time() - started, 2)}


def rows(result: dict) -> list[dict]:
    fields = result.get("fields") or []
    return [dict(zip(fields, item)) for item in result.get("items") or []]


def within(avg: float, low: float, high: float) -> bool:
    # 与规则相同的 0.1% 容差，只用于比较单位假设，不改变规则本身。
    return low * 0.999 <= avg <= high * 1.001


def evaluate(daily: dict[str, dict], blocks: dict[str, list[dict]]) -> dict:
    """Per hypothesis: how many block-trade symbols pass the rule raw vs after subtraction."""
    report = {}
    for name, (vol_mul, amount_mul) in HYPOTHESES.items():
        raw_pass = adjusted_pass = consistent = examined = 0
        failures = []
        for code, trades in blocks.items():
            day = daily.get(code)
            if not day or not day.get("vol") or day.get("low") is None:
                continue
            examined += 1
            total_shares = day["vol"] * DAILY_VOL_SHARES
            total_yuan = day["amount"] * DAILY_AMOUNT_YUAN
            raw_pass += within(total_yuan / total_shares, day["low"], day["high"])
            block_shares = sum(t["vol"] * vol_mul for t in trades)
            block_yuan = sum(t["amount"] * amount_mul for t in trades)
            # price×vol≈amount 内部一致性（单位组合自洽才可能正确）
            consistent += all(
                abs(t["price"] * t["vol"] * vol_mul - t["amount"] * amount_mul)
                <= max(1.0, 0.002 * t["amount"] * amount_mul) for t in trades)
            rest_shares, rest_yuan = total_shares - block_shares, total_yuan - block_yuan
            if rest_shares <= 0 or rest_yuan <= 0:
                failures.append({"code": code, "reason": "non_positive_remainder"})
                continue
            ok = within(rest_yuan / rest_shares, day["low"], day["high"])
            adjusted_pass += ok
            if not ok and len(failures) < 20:
                failures.append({"code": code, "reason": "outside_after_subtraction",
                                 "avg": round(rest_yuan / rest_shares, 4),
                                 "low": day["low"], "high": day["high"]})
        report[name] = {"examined": examined, "raw_pass": raw_pass,
                        "adjusted_pass": adjusted_pass, "price_vol_amount_consistent": consistent,
                        "failures_sample": failures}
    return report


def by_exchange(daily: dict[str, dict], blocks: dict[str, list[dict]]) -> dict:
    """Pass counts per exchange with no subtraction vs subtracting under 万股/万元."""
    vol_mul, amount_mul = HYPOTHESES["vol_wan_shares_amount_wan_yuan"]
    stats: dict[str, Counter] = defaultdict(Counter)
    samples: dict[str, list] = defaultdict(list)
    for code, trades in blocks.items():
        day = daily.get(code)
        if not day or not day.get("vol") or day.get("low") is None:
            continue
        exchange = code.rsplit(".", 1)[-1]
        total_shares = day["vol"] * DAILY_VOL_SHARES
        total_yuan = day["amount"] * DAILY_AMOUNT_YUAN
        raw = within(total_yuan / total_shares, day["low"], day["high"])
        rest_shares = total_shares - sum(t["vol"] * vol_mul for t in trades)
        rest_yuan = total_yuan - sum(t["amount"] * amount_mul for t in trades)
        adjusted = rest_shares > 0 and rest_yuan > 0 and within(
            rest_yuan / rest_shares, day["low"], day["high"])
        block_exceeds_total = rest_shares <= 0
        stats[exchange].update(examined=1, raw_pass=raw, adjusted_pass=adjusted,
                               block_exceeds_daily_volume=block_exceeds_total)
        if exchange == "BJ" or raw != adjusted:
            samples[exchange].append({"code": code, "raw_pass": raw, "adjusted_pass": adjusted,
                                      "block_exceeds_daily_volume": block_exceeds_total})
    return {ex: {"counts": dict(c), "samples": samples[ex][:15]} for ex, c in stats.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYYMMDD")
    parser.add_argument("--extra-dates", default="", help="comma separated YYYYMMDD for per-exchange stats")
    parser.add_argument("--focus", default="920553.BJ")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "deploy.local.toml")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    probe = _load_probe()
    tok = probe.token(args.config)

    block_result = call_all(tok, "block_trade", {"trade_date": args.date})
    block_rows = rows(block_result)
    daily_result = call_all(tok, "daily", {"trade_date": args.date},
                              fields="ts_code,low,high,vol,amount")
    daily = {r["ts_code"]: r for r in rows(daily_result)}
    focus_result = call_all(tok, "block_trade", {"ts_code": args.focus,
                                                   "start_date": args.date, "end_date": args.date})

    blocks: dict[str, list[dict]] = defaultdict(list)
    for r in block_rows:
        if None not in (r.get("price"), r.get("vol"), r.get("amount")):
            blocks[r["ts_code"]].append(r)
    exchanges = Counter(code.rsplit(".", 1)[-1] for code in blocks)
    report = {
        "probe": "tushare_block_trade",
        "issue": 608,
        "at": datetime.now(timezone.utc).isoformat(),
        "trade_date": args.date,
        "block_trade": {
            "ok": block_result.get("ok"), "msg": block_result.get("msg"),
            "fields": block_result.get("fields"), "rows": len(block_rows),
            "symbols": len(blocks), "symbols_by_exchange": dict(exchanges),
            "rows_with_null_price_vol_amount": len(block_rows) - sum(map(len, blocks.values())),
            "seconds": block_result.get("seconds"), "has_more": block_result.get("has_more"),
        },
        "daily": {"ok": daily_result.get("ok"), "rows": len(daily)},
        "focus": {
            "code": args.focus,
            "block_rows": rows(focus_result),
            "daily_row": daily.get(args.focus),
        },
        "unit_hypotheses": evaluate(daily, blocks),
        "by_exchange": {args.date: by_exchange(daily, blocks)},
        "notes": [
            "只读探针；不写仓库数据。daily 单位按仓库既有口径（vol 手、amount 千元）。",
            "单位以 adjusted_pass 与 price_vol_amount_consistent 的实证结果为准，不按文档推定。",
            "当日更新时点需在交易日晚间另行多次采样，本次不能证明。",
        ],
    }
    for extra in filter(None, args.extra_dates.split(",")):
        extra_blocks: dict[str, list[dict]] = defaultdict(list)
        for r in rows(call_all(tok, "block_trade", {"trade_date": extra})):
            if None not in (r.get("price"), r.get("vol"), r.get("amount")):
                extra_blocks[r["ts_code"]].append(r)
        extra_daily = {r["ts_code"]: r for r in rows(call_all(
            tok, "daily", {"trade_date": extra}, fields="ts_code,low,high,vol,amount"))}
        report["by_exchange"][extra] = by_exchange(extra_daily, extra_blocks)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str) + "\n",
                        encoding="utf-8")
    summary = {k: {kk: v[kk] for kk in ("examined", "raw_pass", "adjusted_pass",
                                        "price_vol_amount_consistent")}
               for k, v in report["unit_hypotheses"].items()}
    totals: dict[str, Counter] = defaultdict(Counter)
    for per_day in report["by_exchange"].values():
        for exchange, value in per_day.items():
            totals[exchange].update(value["counts"])
    print(json.dumps({"by_exchange_totals": {k: dict(v) for k, v in totals.items()}},
                     ensure_ascii=False))
    print(json.dumps({"block_rows": len(block_rows), "symbols": len(blocks),
                      "by_exchange": dict(exchanges), "focus": report["focus"]["block_rows"],
                      "hypotheses": summary}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
