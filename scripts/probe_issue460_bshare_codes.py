#!/usr/bin/env python3
"""#460 探针：Tushare 对沪深 B 股代码的实际返回，以及仓库里这些代码落在哪些表。

只读。凭证从部署配置读取，**不打印、不写进输出**。仓库盘点用 DuckDB 流式聚合，
显式限内存与溢写；输出只有计数、日期范围与代码列表，不含行情数值以外的凭证信息。

用法（生产节点，工作目录不要设在 /home/ubuntu/qsys）:
    nice -n 10 ionice -c3 timeout 900 python probe_issue460_bshare_codes.py \\
        --config /home/ubuntu/qsys/config/deploy.local.toml \\
        --warehouse /home/ubuntu/qsys/warehouse --trade-date 20261020 \\
        --scratch <可控临时目录> --out <结果.json>
"""

from __future__ import annotations

import argparse
import json
import re
import time
import tomllib
import urllib.request
from collections import Counter
from pathlib import Path

API = "https://api.tushare.pro"
# 沪市 B 股 900xxx、深市 B 股 200xxx：只用于挑出待核对的代码，不用于推断交易所。
B_SHARE = re.compile(r"^(900|200)\d{3}\.")
TABLES = (
    "price_limits_daily", "price_limit_daily", "tushare_daily", "bar_daily",
    "daily_flags", "instruments", "security_master", "security_status_daily",
    "stock_st_daily", "suspend_daily", "limit_list_daily", "valuation_daily",
    "moneyflow_daily", "adjustment_factor_daily", "name_history",
)


def token(config: Path) -> str:
    tok = tomllib.loads(config.read_text()).get("tushare", {}).get("token")
    if not tok:
        raise SystemExit("config has no [tushare] token")
    return tok


def call(tok: str, api_name: str, params: dict, fields: str = "") -> dict:
    body = json.dumps({"api_name": api_name, "token": tok, "params": params,
                       "fields": fields}).encode()
    request = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    time.sleep(0.5)
    if payload.get("code") != 0:
        return {"ok": False, "msg": payload.get("msg"), "fields": [], "items": []}
    data = payload.get("data") or {}
    return {"ok": True, "msg": "", "fields": data.get("fields") or [], "items": data.get("items") or []}


def rows(result: dict) -> list[dict]:
    return [dict(zip(result["fields"], item)) for item in result["items"]]


def tushare_facts(tok: str, trade_date: str) -> dict:
    facts = {}
    limits = rows(call(tok, "stk_limit", {"trade_date": trade_date}))
    facts["stk_limit_rows"] = len(limits)
    facts["stk_limit_suffixes"] = dict(Counter(r["ts_code"].split(".")[-1] for r in limits))
    facts["stk_limit_b_share_codes"] = sorted(r["ts_code"] for r in limits if B_SHARE.match(r["ts_code"]))
    daily = rows(call(tok, "daily", {"trade_date": trade_date}, "ts_code"))
    facts["daily_rows"] = len(daily)
    facts["daily_b_share_codes"] = sorted(r["ts_code"] for r in daily if B_SHARE.match(r["ts_code"]))
    basic = []
    for status in ("L", "D", "P"):
        basic += rows(call(tok, "stock_basic", {"list_status": status},
                           "ts_code,symbol,name,exchange,market,list_status"))
    facts["stock_basic_b_share"] = sorted(
        (r for r in basic if B_SHARE.match(r["ts_code"])), key=lambda r: r["ts_code"])
    for code in ("900901.SH", "900901.BJ", "200011.SZ"):
        result = call(tok, "stk_limit", {"ts_code": code, "trade_date": trade_date})
        facts[f"stk_limit_{code}"] = {"ok": result["ok"], "msg": result["msg"], "rows": rows(result)}
    return facts


def warehouse_facts(warehouse: Path, scratch: Path) -> dict:
    import duckdb

    connection = duckdb.connect()
    connection.execute("SET memory_limit='1GB'")
    connection.execute("SET threads=1")
    connection.execute(f"SET temp_directory='{scratch}'")
    connection.execute("SET max_temp_directory_size='2GB'")
    out = {}
    for table in TABLES:
        files = sorted(str(p) for p in (warehouse / table).rglob("*.parquet"))
        if not files:
            out[table] = {"present": False}
            continue
        relation = f"read_parquet({files!r}, union_by_name=true)"
        columns = {row[0] for row in connection.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()}
        key = "symbol" if "symbol" in columns else "ts_code" if "ts_code" in columns else None
        if key is None:
            out[table] = {"present": True, "symbol_column": None}
            continue
        date_column = "dt" if "dt" in columns else "trade_date" if "trade_date" in columns else None
        span = f", min({date_column})::VARCHAR, max({date_column})::VARCHAR" if date_column else ""
        query = (f"SELECT substr({key}, 1, 3) AS prefix, split_part({key}, '.', 2) AS suffix, "
                 f"count(*), count(DISTINCT {key}){span} FROM {relation} "
                 f"WHERE regexp_matches({key}, '^(900|200)[0-9]{{3}}\\.') GROUP BY 1, 2 ORDER BY 1, 2")
        groups = connection.execute(query).fetchall()
        out[table] = {"present": True, "symbol_column": key, "b_share_groups": [
            {"prefix": g[0], "suffix": g[1], "rows": g[2], "symbols": g[3],
             **({"first": g[4], "last": g[5]} if date_column else {})} for g in groups]}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"\d{8}", args.trade_date):
        raise SystemExit("trade-date must be YYYYMMDD")
    result = {"issue": 460, "trade_date": args.trade_date,
              "tushare": tushare_facts(token(args.config), args.trade_date),
              "warehouse": warehouse_facts(args.warehouse, args.scratch)}
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"ok": True, "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
