#!/usr/bin/env python3
"""同花顺概念探针（#648 P0）：当前积分能否调用 ths_index / ths_member，及其形状。

打板看板按同花顺概念给涨停票归列（成分 >300 的泛概念不参与）。依赖它之前先实测：

1. ``ths_index`` 概念类（type=N）能否调用、返回哪些列、概念数量；
2. ``count`` 列给出的成分数分布（>300 的有多少）；
3. 抽样 ``ths_member``：返回列、实际行数与 ``count`` 是否一致、是否带
   生效/剔除日期（决定能否按日确定成分）；
4. 按股票反查（``con_code``）能否返回其所属概念。

只记录事实，不推断：失败原样记录接口返回的 msg。凭证读 config/deploy.local.toml，
不打印、不落盘。输出只含概念代码/名称/计数与抽样摘要，不含成分全量明细。

用法:
    uv run python scripts/probe_ths_concepts.py --out artifacts/ths_concept_probe_20261112.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tushare_probe import call, token  # noqa: E402

SIZE_CAP = 300  # 设计文档 §3：成分 >300 的概念不参与归列
SAMPLE_STOCKS = ("000001.SZ", "600519.SH", "300750.SZ")


def _rows(got: dict) -> list[dict]:
    fields = got.get("fields") or []
    return [dict(zip(fields, item, strict=False)) for item in got.get("all_items", [])]


def call_all(tok: str, api: str, params: dict, fields: str = "") -> dict:
    """调接口并保留全部行（tushare_probe.call 只回前 3 行做样本）。"""
    import urllib.request

    body = json.dumps({"api_name": api, "token": tok, "params": params,
                       "fields": fields}).encode()
    started = time.time()
    try:
        req = urllib.request.Request("https://api.tushare.pro", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.load(resp)
    except Exception as exc:  # 探针记录失败事实
        return {"ok": False, "msg": f"{type(exc).__name__}: {exc}", "rows": 0}
    elapsed = round(time.time() - started, 2)
    if payload.get("code") != 0:
        return {"ok": False, "msg": payload.get("msg"), "rows": 0, "seconds": elapsed}
    data = payload.get("data") or {}
    items = data.get("items") or []
    return {"ok": True, "msg": "", "rows": len(items), "fields": data.get("fields"),
            "all_items": items, "has_more": data.get("has_more"), "seconds": elapsed}


def probe(tok: str) -> dict:
    out: dict = {"probed_at": datetime.now().isoformat(timespec="seconds")}

    index = call_all(tok, "ths_index", {"exchange": "A", "type": "N"})
    out["ths_index"] = {k: index.get(k) for k in ("ok", "msg", "rows", "fields",
                                                  "has_more", "seconds")}
    if not index["ok"]:
        return out
    concepts = _rows(index)
    counts = [c.get("count") for c in concepts]
    known = [n for n in counts if isinstance(n, int)]
    out["ths_index"]["count_null"] = len(counts) - len(known)
    out["ths_index"]["count_distribution"] = {
        "le_50": sum(n <= 50 for n in known),
        "51_100": sum(50 < n <= 100 for n in known),
        "101_300": sum(100 < n <= SIZE_CAP for n in known),
        "gt_300": sum(n > SIZE_CAP for n in known),
        "max": max(known) if known else None,
    }
    out["ths_index"]["gt_300_concepts"] = sorted(
        ({"ts_code": c["ts_code"], "name": c.get("name"), "count": c["count"]}
         for c in concepts if isinstance(c.get("count"), int) and c["count"] > SIZE_CAP),
        key=lambda c: -c["count"],
    )
    out["ths_index"]["exchange_type_values"] = dict(
        Counter(f"{c.get('exchange')}/{c.get('type')}" for c in concepts))

    # 抽样：最大、中位附近、最小各一个，外加一个普通题材
    ordered = sorted((c for c in concepts if isinstance(c.get("count"), int)),
                     key=lambda c: c["count"])
    picks = []
    if ordered:
        picks = [ordered[-1], ordered[len(ordered) // 2], ordered[0]]
    samples = []
    for concept in picks:
        time.sleep(0.5)
        got = call_all(tok, "ths_member", {"ts_code": concept["ts_code"]})
        rows = _rows(got) if got["ok"] else []
        dated = {f: sum(r.get(f) not in (None, "") for r in rows)
                 for f in ("in_date", "out_date", "weight", "is_new")
                 if got.get("fields") and f in got["fields"]}
        samples.append({
            "ts_code": concept["ts_code"], "name": concept.get("name"),
            "index_count": concept["count"], "ok": got["ok"], "msg": got.get("msg"),
            "member_rows": got["rows"], "fields": got.get("fields"),
            "has_more": got.get("has_more"), "non_null_by_field": dated,
            "first_rows": [r for r in rows[:2]],
        })
    out["ths_member_samples"] = samples

    reverse = []
    for code in SAMPLE_STOCKS:
        time.sleep(0.5)
        got = call_all(tok, "ths_member", {"con_code": code})
        rows = _rows(got) if got["ok"] else []
        concept_codes = {r.get("ts_code") for r in rows}
        reverse.append({
            "con_code": code, "ok": got["ok"], "msg": got.get("msg"),
            "rows": got["rows"],
            "in_concept_index": len(concept_codes & {c["ts_code"] for c in concepts}),
            "other_index_codes": len(concept_codes - {c["ts_code"] for c in concepts}),
        })
    out["ths_member_by_stock"] = reverse
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/deploy.local.toml"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = probe(token(args.config))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    summary = {k: result.get(k, {}) for k in ("ths_index",)}
    print(json.dumps({"ok": summary["ths_index"].get("ok"),
                      "msg": summary["ths_index"].get("msg"),
                      "concepts": summary["ths_index"].get("rows"),
                      "distribution": summary["ths_index"].get("count_distribution")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
