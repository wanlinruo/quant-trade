#!/usr/bin/env python3
"""Tushare Pro 探针：实测文档没写清楚的事实，结论落盘再写依赖它的代码。

**为什么要探针**：接口的历史起始年份、当日更新时点、实际频次上限，官方文档
要么没写要么写得含糊。猜错了会让整条采集链路在半年后才暴露问题。本仓库的规矩是
先实测、结论进 git，再写依赖它的代码。

凭证从 config/deploy.local.toml 读取，**不打印、不写进日志**。

用法:
    uv run python scripts/tushare_probe.py --out artifacts/tushare_probe.json
"""

from __future__ import annotations

import argparse
import json
import time
import tomllib
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

API = "https://api.tushare.pro"


def token(config: Path) -> str:
    if not config.exists():
        raise SystemExit(f"未找到凭证文件 {config}")
    cfg = tomllib.loads(config.read_text())
    tok = cfg.get("tushare", {}).get("token")
    if not tok:
        raise SystemExit(f"{config} 里没有 [tushare] token")
    return tok


def call(tok: str, api_name: str, params: dict, fields: str = "",
         retries: int = 3) -> dict:
    """调一个接口。返回 {'ok', 'rows', 'fields', 'items', 'msg', 'seconds'}。

    失败不抛，原样把 msg 带回——探针的任务是记录事实，包括失败的事实。
    """
    body = json.dumps({
        "api_name": api_name, "token": tok, "params": params, "fields": fields,
    }).encode()
    started = time.time()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                API, data=body, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.load(resp)
            elapsed = round(time.time() - started, 2)
            if payload.get("code") != 0:
                return {"ok": False, "rows": 0, "msg": payload.get("msg"),
                        "seconds": elapsed}
            data = payload.get("data") or {}
            items = data.get("items") or []
            return {"ok": True, "rows": len(items), "fields": data.get("fields"),
                    "items": items[:3], "msg": "", "seconds": elapsed}
        except Exception as exc:
            if attempt == retries - 1:
                return {"ok": False, "rows": 0, "msg": f"{type(exc).__name__}: {exc}",
                        "seconds": round(time.time() - started, 2)}
            time.sleep(2 ** attempt)
    return {"ok": False, "rows": 0, "msg": "unreachable", "seconds": 0.0}


def earliest_year(tok: str, api_name: str, date_param: str, probe_dates: list[str],
                  extra: dict | None = None) -> dict:
    """逐个试探日期，找出接口最早能返回数据的那一天。

    探针日期由调用方给定，一律取各年首个交易周的某一天；返回每次探测的行数，
    **不做插值、不推断中间年份**——只报告实测到的点。
    """
    out = {}
    for d in probe_dates:
        params = {date_param: d}
        if extra:
            params.update(extra)
        got = call(tok, api_name, params)
        out[d] = {"ok": got["ok"], "rows": got["rows"], "msg": got["msg"]}
        time.sleep(0.3)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=Path("config/deploy.local.toml"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    tok = token(args.config)
    result: dict = {
        "probed_at": datetime.now().isoformat(timespec="seconds"),
        "note": "凭证不落盘。本文件可入库。",
    }

    # --- 1. 连通性与积分档 ---
    # stock_basic 是最低门槛的接口之一，用它确认 token 有效。
    basic = call(tok, "stock_basic", {"exchange": "", "list_status": "L"},
                 "ts_code,symbol,name,area,industry,list_date")
    result["connectivity"] = {
        "ok": basic["ok"], "rows": basic["rows"], "msg": basic["msg"],
        "seconds": basic["seconds"],
    }
    if not basic["ok"]:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    # --- 2. 退市股是否可得（策略的广度池需要）---
    delisted = call(tok, "stock_basic", {"exchange": "", "list_status": "D"},
                    "ts_code,name,list_date,delist_date")
    result["stock_basic_delisted"] = {
        "ok": delisted["ok"], "rows": delisted["rows"], "msg": delisted["msg"],
    }

    # --- 3. stk_limit 的历史起始年份（文档未写，最关键的未知项）---
    # 探测点取各年 1 月的一个交易日。只报实测点，不推断中间年份。
    probe_dates = ["20260911", "20250102", "20240102", "20230103", "20220104",
                   "20210104", "20200102", "20190102", "20180102", "20160104",
                   "20140102", "20100104", "20050104", "20000104"]
    result["stk_limit_history"] = earliest_year(
        tok, "stk_limit", "trade_date", probe_dates
    )

    # --- 4. 当日更新时点：最近交易日的日线是否已到 ---
    for api_name in ("daily", "daily_basic", "adj_factor"):
        got = call(tok, api_name, {"trade_date": "20260911"})
        result[f"{api_name}_20260911"] = {
            "ok": got["ok"], "rows": got["rows"], "msg": got["msg"],
            "seconds": got["seconds"], "fields": got.get("fields"),
        }

    # --- 5. 指数日线（mootdx 死后无源，S6 Gate 依赖它）---
    idx = call(tok, "index_daily",
               {"ts_code": "000300.SH", "start_date": "20260901",
                "end_date": "20260911"})
    result["index_daily_csi300"] = {
        "ok": idx["ok"], "rows": idx["rows"], "msg": idx["msg"],
        "fields": idx.get("fields"),
    }

    # --- 6. 停牌真值 ---
    sus = call(tok, "suspend_d", {"trade_date": "20260911"})
    result["suspend_d"] = {
        "ok": sus["ok"], "rows": sus["rows"], "msg": sus["msg"],
        "fields": sus.get("fields"),
    }

    # --- 7. 涨跌停/炸板列表（首板/炸板直取；文档称 2020 起、不含 ST）---
    result["limit_list_d_history"] = earliest_year(
        tok, "limit_list_d", "trade_date",
        ["20260911", "20250102", "20240102", "20230103", "20220104",
         "20210104", "20200102", "20190102"],
        extra={"limit_type": "U"},
    )

    # --- 8. 曾用名（ST 起止日期）---
    nc = call(tok, "namechange", {"ts_code": "000010.SZ"})
    result["namechange"] = {
        "ok": nc["ok"], "rows": nc["rows"], "msg": nc["msg"],
        "fields": nc.get("fields"), "sample": nc.get("items"),
    }

    # --- 9. 交易日历 ---
    cal = call(tok, "trade_cal",
               {"exchange": "SSE", "start_date": "20260901", "end_date": "20260930"})
    result["trade_cal"] = {"ok": cal["ok"], "rows": cal["rows"], "msg": cal["msg"]}

    # --- 10. 实测频次：连打 12 次 daily，记录耗时与失败 ---
    # 5000 积分档标称 500 次/分钟。这里只验证「连打不被拒」，不去压测上限——
    # 压测会占用额度且对判断没有帮助。
    burst = []
    for i in range(12):
        got = call(tok, "daily", {"trade_date": "20260911"}, "ts_code,close",
                   retries=1)
        burst.append({"ok": got["ok"], "rows": got["rows"],
                      "seconds": got["seconds"], "msg": got["msg"]})
    result["burst_12_calls"] = {
        "all_ok": all(x["ok"] for x in burst),
        "failures": [x for x in burst if not x["ok"]],
        "total_seconds": round(sum(x["seconds"] for x in burst), 2),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
