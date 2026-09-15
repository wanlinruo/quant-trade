"""验收后清理旧日线副本；默认仅审计，必须显式 --execute 才删除。

清理 paid_daily 的硬闸门是：指定生产区间内 stock_st_daily 与 tushare_daily
键集合完全相同。执行日志包含待删文件 SHA256，可据此从外部备份恢复和审计。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

TARGETS = ("bars_daily", "paid_daily", "paid_daily_repairs", "paid_daily_quality")
DEFAULT_TARGETS = ("bars_daily", "paid_daily")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def parquet_glob(root: Path, dataset: str) -> str:
    return str((root / dataset / "year=*" / "part.parquet").resolve()).replace("'", "''")


def st_gate(root: Path, start: date, end: date) -> dict[str, int | bool]:
    daily = parquet_glob(root, "tushare_daily")
    truth = parquet_glob(root, "stock_st_daily")
    if not list((root / "stock_st_daily").glob("year=*/part.parquet")):
        return {"ok": False, "daily_rows": 0, "st_rows": 0, "missing": -1, "extra": -1}
    con = duckdb.connect()
    params = [start, end]
    daily_rows = con.execute(
        f"SELECT count(*) FROM read_parquet('{daily}') WHERE dt BETWEEN ? AND ?", params
    ).fetchone()[0]
    st_rows = con.execute(
        f"SELECT count(*) FROM read_parquet('{truth}') WHERE dt BETWEEN ? AND ?", params
    ).fetchone()[0]
    missing = con.execute(f"""
        SELECT count(*) FROM read_parquet('{daily}') d
        ANTI JOIN read_parquet('{truth}') s USING (symbol, dt)
        WHERE d.dt BETWEEN ? AND ?
    """, params).fetchone()[0]
    extra = con.execute(f"""
        SELECT count(*) FROM read_parquet('{truth}') s
        ANTI JOIN read_parquet('{daily}') d USING (symbol, dt)
        WHERE s.dt BETWEEN ? AND ?
    """, params).fetchone()[0]
    return {"ok": daily_rows > 0 and missing == 0 and extra == 0,
            "daily_rows": daily_rows, "st_rows": st_rows,
            "missing": missing, "extra": extra}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--target", action="append", choices=TARGETS,
                        help="可重复；默认检查全部旧日线目录")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--log", type=Path, default=None)
    args = parser.parse_args()
    root = args.warehouse.resolve()
    selected = args.target or list(DEFAULT_TARGETS)
    gate_start = args.start - timedelta(days=150)
    gate = st_gate(root, gate_start, args.end)
    if "paid_daily" in selected and not gate["ok"]:
        raise SystemExit(f"拒绝清理 paid_daily：stock_st 覆盖闸门未通过：{gate}")

    files = []
    for name in selected:
        base = root / name
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            files.append({"dataset": name, "path": str(path),
                          "bytes": path.stat().st_size, "sha256": digest(path)})

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    record = {
        "operation": "cleanup_legacy_daily", "mode": "execute" if args.execute else "dry-run",
        "at": now.isoformat(), "warehouse": str(root), "start": str(args.start),
        "end": str(args.end), "stock_st_gate_start": str(gate_start),
        "stock_st_gate": gate, "targets": selected,
        "files": files, "total_bytes": sum(item["bytes"] for item in files),
    }
    target = args.log or Path("artifacts") / f"legacy_daily_cleanup_{now:%Y%m%d_%H%M%S}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")

    if args.execute:
        for name in selected:
            base = root / name
            if base.is_dir():
                shutil.rmtree(base)
        record["status"] = "deleted"
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    else:
        record["status"] = "ready" if files else "nothing-to-do"
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(f"{record['status']}：{len(files)} 个文件，{record['total_bytes']:,} bytes；日志 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
