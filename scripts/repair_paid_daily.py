#!/usr/bin/env python3
"""用 BaoStock + 腾讯两路直接历史值生成付费日线第一批修补旁路表。

范围严格限定为沪深 OHLC 硬约束异常和 amount 空值。脚本保存上游原始响应，
不修改 paid_daily；只有加 ``--publish`` 才把已审计决策原子发布到独立修补目录。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import baostock as bs
import polars as pl
import pyarrow as pa
import requests

from qsys.data.pipeline.paid_daily import atomic_json, atomic_parquet
from qsys.data.pipeline.paid_daily_repair import REPAIR_SCHEMA, validate_decisions


RULE_VERSION = "paid-repair-v1"
PRICE_FIELDS = ("open", "high", "low", "close")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def raw_hashes(warehouse: Path) -> dict[str, str]:
    return {
        str(path.relative_to(warehouse)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((warehouse / "paid_daily").glob("year=*/part.parquet"))
    }


def find_candidates(warehouse: Path, scope: str) -> list[dict]:
    source = pl.scan_parquet(str(warehouse / "paid_daily/year=*/part.parquet")).select(
        "symbol", "dt", "open", "high", "low", "close", "amount"
    )
    if scope == "turnover":
        rows = pl.scan_parquet(str(warehouse / "paid_daily/year=*/part.parquet")).select(
            "symbol", "dt", "turnover_ratio"
        ).filter(
            ~pl.col("symbol").str.ends_with(".BJ") & pl.col("turnover_ratio").is_null()
        ).collect()
        return [
            {"symbol": row["symbol"], "dt": row["dt"], "field": "turnover_ratio",
             "original_value": None, "candidate_group": "missing_turnover_ratio"}
            for row in rows.iter_rows(named=True)
        ]

    invalid = source.filter(
        ~pl.col("symbol").str.ends_with(".BJ")
        & (
            (pl.col("low") > pl.col("high"))
            | (pl.col("open") < pl.col("low"))
            | (pl.col("open") > pl.col("high"))
            | (pl.col("close") < pl.col("low"))
            | (pl.col("close") > pl.col("high"))
        )
    ).collect()
    missing_amount = source.filter(
        ~pl.col("symbol").str.ends_with(".BJ") & pl.col("amount").is_null()
    ).collect()
    result: dict[tuple[str, date, str], dict] = {}
    for row in invalid.iter_rows(named=True):
        for field in PRICE_FIELDS:
            key = (row["symbol"], row["dt"], field)
            result[key] = {"symbol": key[0], "dt": key[1], "field": field,
                           "original_value": row[field], "candidate_group": "invalid_ohlc"}
    for row in missing_amount.iter_rows(named=True):
        key = (row["symbol"], row["dt"], "amount")
        result[key] = {"symbol": key[0], "dt": key[1], "field": "amount",
                       "original_value": None, "candidate_group": "missing_amount"}
    return [result[key] for key in sorted(result)]


def native_code(symbol: str, style: str) -> str:
    code, market = symbol.split(".")
    prefix = market.lower()
    return f"{prefix}.{code}" if style == "baostock" else f"{prefix}{code}"


def fetch_baostock(groups: dict, raw_dir: Path) -> tuple[dict, dict]:
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_code} {login.error_msg}")
    observations, hashes = {}, {}
    try:
        for (symbol, year), days in sorted(groups.items()):
            code = native_code(symbol, "baostock")
            start, end = min(days).isoformat(), max(days).isoformat()
            fields = "date,code,open,high,low,close,volume,amount,turn,adjustflag"
            rs = bs.query_history_k_data_plus(
                code, fields, start_date=start, end_date=end, frequency="d", adjustflag="3"
            )
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            payload = {
                "source_family": "baostock", "library_version": getattr(bs, "__version__", None),
                "request": {"code": code, "fields": fields, "start_date": start,
                            "end_date": end, "frequency": "d", "adjustflag": "3"},
                "error_code": rs.error_code, "error_msg": rs.error_msg,
                "fields": list(rs.fields), "rows": rows,
            }
            content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            path = raw_dir / "baostock" / f"{symbol}_{year}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            digest = sha256_bytes(content)
            hashes[(symbol, year)] = digest
            if rs.error_code != "0":
                continue
            for values in rows:
                row = dict(zip(rs.fields, values))
                day = date.fromisoformat(row["date"])
                values = {
                    field: Decimal(row[field]) for field in (*PRICE_FIELDS, "amount") if row[field] != ""
                }
                if row.get("turn", "") != "":
                    values["turnover_ratio"] = Decimal(row["turn"])
                observations[(symbol, day)] = values
            time.sleep(0.03)
    finally:
        bs.logout()
    return observations, hashes


def fetch_tencent(groups: dict, raw_dir: Path) -> tuple[dict, dict, dict]:
    url = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 paid-daily-repair/1.0"
    observations, hashes, amount_quantums = {}, {}, {}
    for (symbol, year), days in sorted(groups.items()):
        code = native_code(symbol, "tencent")
        params = {
            "_var": f"kline_day{year}",
            "param": f"{code},day,{year}-01-01,{year + 1}-12-31,640,",
            "r": "0.8205512681390605",
        }
        last_error = None
        response = None
        succeeded = False
        for attempt in range(3):
            try:
                response = session.get(url, params=params, timeout=20)
                response.raise_for_status()
                succeeded = True
                break
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(1 + attempt)
        if not succeeded:
            payload = json.dumps({"source_family": "tencent", "request": params,
                                  "error": str(last_error)}, ensure_ascii=False).encode()
        else:
            payload = response.content
        path = raw_dir / "tencent" / f"{symbol}_{year}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        hashes[(symbol, year)] = sha256_bytes(payload)
        if not succeeded:
            continue
        try:
            parsed = json.loads(response.text.split("=", 1)[1])
            rows = parsed["data"][code].get("day", [])
        except (ValueError, KeyError, IndexError):
            continue
        wanted = set(days)
        for values in rows:
            day = date.fromisoformat(values[0])
            if day not in wanted:
                continue
            amount_native = Decimal(str(values[8]))
            observation = {
                "open": Decimal(str(values[1])), "close": Decimal(str(values[2])),
                "high": Decimal(str(values[3])), "low": Decimal(str(values[4])),
                "amount": amount_native * Decimal(10000),
            }
            turnover_text = str(values[7]).strip()
            if turnover_text not in {"", "-", "--"}:
                observation["turnover_ratio"] = Decimal(turnover_text)
            observations[(symbol, day)] = observation
            decimals = max(0, -amount_native.as_tuple().exponent)
            amount_quantums[(symbol, day)] = (Decimal(10) ** -decimals) * Decimal(10000)
            if "turnover_ratio" in observation:
                turnover_native = observation["turnover_ratio"]
                turnover_decimals = max(0, -turnover_native.as_tuple().exponent)
                amount_quantums[(symbol, day, "turnover_ratio")] = Decimal(10) ** -turnover_decimals
        time.sleep(0.08)
    return observations, hashes, amount_quantums


def build_decisions(candidates: list[dict], bao: dict, bao_hash: dict,
                    tx: dict, tx_hash: dict, tx_quantum: dict, batch_id: str) -> pl.DataFrame:
    # 一根异常 K 线是一个事实整体。只要两源对四价中的任一项冲突，就不允许
    # 部分替换其余字段，否则可能制造一根仍不满足 OHLC 约束的混合来源 K 线。
    price_keys = {
        (candidate["symbol"], candidate["dt"])
        for candidate in candidates if candidate["field"] in PRICE_FIELDS
    }
    bar_agrees = {
        key: all(
            bao.get(key, {}).get(field) is not None
            and tx.get(key, {}).get(field) is not None
            and bao[key][field] == tx[key][field]
            for field in PRICE_FIELDS
        )
        for key in price_keys
    }
    rows = []
    for candidate in candidates:
        symbol, day, field = candidate["symbol"], candidate["dt"], candidate["field"]
        key, group = (symbol, day), (symbol, day.year)
        a = bao.get(key, {}).get(field)
        b = tx.get(key, {}).get(field)
        decision, reason, repaired = "UNAVAILABLE", "one or both direct sources unavailable", None
        if a is not None and b is not None:
            if field in PRICE_FIELDS:
                agrees = bar_agrees[key]
                reason = ("two sources agree exactly on the complete direct unadjusted OHLC bar"
                          if agrees else "complete OHLC bar conflicts; partial field repair forbidden")
            elif field == "amount":
                quantum = tx_quantum[key]
                agrees = abs(a - b) <= quantum / 2
                reason = (f"BaoStock direct amount lies within Tencent declared rounding interval ({quantum} CNY)"
                          if agrees else f"direct amounts conflict beyond Tencent rounding quantum ({quantum} CNY)")
            else:
                quantum = tx_quantum[(symbol, day, "turnover_ratio")]
                # 腾讯早期记录的 0.00 可能是缺失占位，不能拿来确认一个很小的非零值。
                agrees = b > 0 and abs(a - b) <= quantum / 2
                reason = (f"BaoStock direct turnover lies within Tencent declared rounding interval ({quantum} percentage point)"
                          if agrees else f"direct turnover unavailable/zero or conflicts beyond Tencent rounding quantum ({quantum})")
            if agrees:
                original = candidate["original_value"]
                repaired = a
                if original is not None and Decimal(str(original)) == a:
                    decision, reason = "CONFIRMED", reason + "; paid value already matches"
                else:
                    decision = "ACCEPTED"
            else:
                decision = "CONFLICT"
        identity = f"{batch_id}|{symbol}|{day}|{field}".encode()
        rows.append({
            "repair_id": hashlib.sha256(identity).hexdigest()[:24], "batch_id": batch_id,
            "symbol": symbol, "dt": day, "field": field,
            "original_value": candidate["original_value"],
            "repaired_value": float(repaired) if repaired is not None else None,
            "source_a_family": "baostock", "source_a_value": float(a) if a is not None else None,
            "source_a_evidence_sha256": bao_hash.get(group),
            "source_b_family": "tencent", "source_b_value": float(b) if b is not None else None,
            "source_b_evidence_sha256": tx_hash.get(group),
            "decision": decision, "reason": reason, "rule_version": RULE_VERSION,
        })
    return pl.from_arrow(pa.Table.from_pylist(rows, schema=REPAIR_SCHEMA))


def write_csv(frame: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_csv(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--work-root", type=Path, default=Path("var/paid_daily_repair"))
    parser.add_argument("--batch-id", default=f"batch1-{date.today().isoformat()}")
    parser.add_argument("--scope", choices=["batch1", "turnover"], default="batch1")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()

    started = datetime.now().astimezone().isoformat()
    batch = args.work_root / args.batch_id
    if batch.exists():
        shutil.rmtree(batch)
    raw_dir = batch / "raw"
    candidates = find_candidates(args.warehouse, args.scope)
    atomic_json(batch / "candidates.json", candidates)
    groups = defaultdict(set)
    for row in candidates:
        groups[(row["symbol"], row["dt"].year)].add(row["dt"])

    before = raw_hashes(args.warehouse)
    atomic_json(batch / "paid_daily_sha256_before.json", before)
    bao, bao_hash = fetch_baostock(groups, raw_dir)
    tx, tx_hash, tx_quantum = fetch_tencent(groups, raw_dir)
    decisions = build_decisions(candidates, bao, bao_hash, tx, tx_hash, tx_quantum, args.batch_id)
    validate_decisions(decisions)
    atomic_parquet(decisions, batch / "decisions.parquet", REPAIR_SCHEMA)
    write_csv(decisions, batch / "decisions.csv")

    after = raw_hashes(args.warehouse)
    if before != after:
        raise RuntimeError("paid_daily changed while repair evidence was collected")
    counts = Counter(decisions["decision"].to_list())
    summary = {
        "batch_id": args.batch_id, "started_at": started,
        "scope": args.scope,
        "finished_at": datetime.now().astimezone().isoformat(),
        "candidate_cells": len(candidates), "candidate_rows": len({(x['symbol'], x['dt']) for x in candidates}),
        "decisions": dict(sorted(counts.items())),
        "accepted_changed_cells": counts.get("ACCEPTED", 0),
        "source_families": ["baostock", "tencent"],
        "raw_paid_partitions": len(before), "raw_paid_unchanged": True,
        "published": bool(args.publish),
    }
    atomic_json(batch / "summary.json", summary)

    if args.publish:
        target = args.warehouse / "paid_daily_repairs" / f"batch={args.batch_id}"
        temp = target.with_name(target.name + ".tmp")
        if temp.exists():
            shutil.rmtree(temp)
        temp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(batch, temp)
        if target.exists():
            shutil.rmtree(target)
        os.replace(temp, target)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
