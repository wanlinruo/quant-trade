"""Windows 侧：下载全市场分红送配历史并导出 parquet。

**只在 Windows 上运行**（依赖 xtquant）。可中断可续传。

`xtdata.get_divid_factors(code)` 返回逐次除权除息记录，索引是除权日
（YYYYMMDD）：

| 字段 | 含义 |
|---|---|
| `interest` | 每股现金红利（元） |
| `stockBonus` | 每股送股 |
| `stockGift` | 每股转增 |
| `allotNum` | 每股配股数 |
| `allotPrice` | 配股价 |
| `dr` | 单次复权因子（用于交叉校验） |

**它含配股**——akshare 的 `stock_fhps_detail_em` 只有分红送转，
做过配股的标的复权因子会偏差（见 spec §12 风险表）。这条缺口由此关闭。

用法：
  C:\\qsys\\.venv\\Scripts\\python.exe scripts\\win_download_dividends.py ^
      --symbols C:\\qsys\\symbols.csv --out C:\\qsys\\dividends ^
      --start-date 20260910 --end-date 20261028
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from datetime import datetime

COLUMNS = [
    "symbol", "ex_date", "cash_per_share", "stock_per_share",
    "rights_per_share", "rights_price", "factor",
]


def _arrow_schema():
    import pyarrow as pa
    return pa.schema([
        ("symbol", pa.string()), ("ex_date", pa.date32()),
        *[(name, pa.float64()) for name in COLUMNS[2:]],
    ])


def _as_date(stamp):
    text = str(stamp)
    if len(text) != 8 or not text.isascii() or not text.isdigit():
        raise ValueError("invalid event date")
    return datetime.strptime(text, "%Y%m%d").date()


def _num(value) -> float | None:
    """仅保留真实数值；缺失保留null，非法值不转换成市场事实。"""
    import pandas as pd
    if value is None or pd.isna(value):
        return None
    number = float(value)
    if math.isnan(number):
        return None
    if not math.isfinite(number):
        raise ValueError("nonfinite source value")
    return number


def read_symbols(path):
    with open(path, newline="", encoding="utf-8") as handle:
        symbols = [r["symbol"] for r in csv.DictReader(handle)]
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("empty or duplicate symbol manifest")
    for symbol in symbols:
        code, sep, market = symbol.partition(".")
        if len(code) != 6 or not code.isascii() or not code.isdigit() or sep != "." or market not in {"SH", "SZ", "BJ"}:
            raise ValueError("invalid symbol")
    return symbols


def normalize_frame(symbol, frame, start, end):
    rows = []
    for stamp, row in frame.iterrows():
        ex_date = _as_date(stamp)
        if not start <= ex_date <= end:
            raise ValueError("event outside requested window")
        bonus, gift = _num(row.get("stockBonus")), _num(row.get("stockGift"))
        stock = None if bonus is None or gift is None else _num(bonus + gift)
        rows.append((symbol, ex_date, _num(row.get("interest")), stock,
                     _num(row.get("allotNum")), _num(row.get("allotPrice")),
                     _num(row.get("dr"))))
    if len({r[1] for r in rows}) != len(rows):
        raise ValueError("duplicate source event")
    return rows


def _atomic_json(path, value):
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export(symbols, out_dir, start, end, fetch):
    """固定窗口的直取快照；文件先持久化，收据后提交，恢复时核对SHA。"""
    import pyarrow as pa
    import pyarrow.parquet as pq

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"protocol": "qsys-dividends-v2", "symbols": symbols,
                "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d")}
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            raise ValueError("snapshot scope changed; use a new directory")
    else:
        if any(out.iterdir()):
            raise ValueError("legacy or unknown output; use a new directory")
        _atomic_json(manifest_path, manifest)
    counts = {"done": 0, "empty": 0, "incomplete": 0, "failed": 0, "reused": 0}
    for symbol in symbols:
        receipt_path = out / (symbol + ".json")
        data_path = out / (symbol + ".parquet")
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("symbol") != symbol:
                raise ValueError("receipt identity mismatch")
            if receipt["status"] in {"done", "empty"}:
                if not data_path.exists() or _sha(data_path) != receipt["sha256"]:
                    raise ValueError("completed snapshot missing or changed")
                counts[receipt["status"]] += 1
                counts["reused"] += 1
                continue
        try:
            frame = fetch(symbol, start_time=manifest["start"], end_time=manifest["end"])
            if frame is None:
                raise ValueError("unknown source response")
            rows = normalize_frame(symbol, frame, start, end)
            table = pa.Table.from_pylist([dict(zip(COLUMNS, row)) for row in rows], schema=_arrow_schema())
            tmp = data_path.with_suffix(".parquet.tmp")
            pq.write_table(table, tmp)
            with tmp.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(tmp, data_path)
            nulls = sum(table.column(name).null_count for name in COLUMNS[2:])
            status = "incomplete" if nulls else ("done" if rows else "empty")
            receipt = {"symbol": symbol, "status": status, "rows": len(rows),
                       "null_cells": nulls, "sha256": _sha(data_path)}
        except Exception as exc:
            receipt = {"symbol": symbol, "status": "failed", "error_type": type(exc).__name__}
        _atomic_json(receipt_path, receipt)
        counts[receipt["status"]] += 1
    _atomic_json(out / "summary.json", counts)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="导出固定窗口的原生分红送配快照（空值不填充）")
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--start-date", required=True, help="YYYYMMDD")
    parser.add_argument("--end-date", required=True, help="YYYYMMDD")
    args = parser.parse_args()
    start, end = _as_date(args.start_date), _as_date(args.end_date)
    if start > end:
        parser.error("start-date must not be after end-date")
    symbols = read_symbols(args.symbols)
    from xtquant import xtdata
    counts = export(symbols, args.out, start, end, xtdata.get_divid_factors)
    print(json.dumps(counts, ensure_ascii=False))
    return 1 if counts["incomplete"] or counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
