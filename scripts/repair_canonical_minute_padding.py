#!/usr/bin/env python3
"""审计并修复 canonical 中已由源表证伪的 QMT 前导填充。

默认只读：扫描全部 ``bar_minute_1m`` 日分区，输出三类案例的可复现报告和
明细 Parquet。只有 ``source=qmt``、连续前导零不少于两根，且删去前缀后与
当前 canonical 构建器逐键逐字段完全一致的标的日才进入修复计划。

``--apply`` 会先逐分区原样备份，再持仓库写锁原子回写并回读复验。paid 多根
和所有单根案例永不由本脚本删除。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from contextlib import nullcontext
from datetime import date, datetime
from pathlib import Path

import polars as pl

from qsys.agent.locking import single_instance
from qsys.data import schema
from qsys.data.pipeline.canonical_minute import _validate, canonical_minute_frame
from qsys.data.quality.minute_padding import leading_zero_cases
from qsys.data.storage import Warehouse

_KEYS = ["symbol", "ts"]


def _day(path: Path) -> date:
    return datetime.strptime(path.parent.name.split("=", 1)[1], "%Y%m%d").date()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(warehouse: Path) -> pl.DataFrame:
    chunks: list[pl.DataFrame] = []
    paths = sorted((warehouse / "bar_minute_1m").glob("year=*/date=*/part.parquet"))
    for index, path in enumerate(paths, 1):
        frame = pl.read_parquet(path, columns=["symbol", "ts", "volume", "source"])
        cases = leading_zero_cases(frame)
        if cases.height:
            chunks.append(cases)
        if index % 250 == 0:
            print(f"审计进度 {index}/{len(paths)}", flush=True)
    if not chunks:
        return leading_zero_cases(pl.from_arrow(schema.BAR_MINUTE_1M.empty_table()))
    return pl.concat(chunks).sort(["day", "symbol"])


def plan_qmt_day(warehouse: Path, day: date, cases: pl.DataFrame) -> dict:
    target = warehouse / "bar_minute_1m" / f"year={day.year}" \
        / f"date={day:%Y%m%d}" / "part.parquet"
    old = pl.read_parquet(target).select(schema.BAR_MINUTE_1M.names).sort(_KEYS)
    symbols = cases.filter(
        (pl.col("source") == "qmt") & (pl.col("leading_rows") >= 2)
    )["symbol"].to_list()
    if not symbols:
        raise ValueError(f"{day} 没有具备证据的 QMT 多根前导零案例")
    selected = old.filter(pl.col("symbol").is_in(symbols))
    first_positive = cases.filter(pl.col("symbol").is_in(symbols)).select(
        "symbol", "first_positive_ts"
    )
    kept = (
        selected.join(first_positive, on="symbol")
        .filter(pl.col("ts") >= pl.col("first_positive_ts"))
        .drop("first_positive_ts")
        .sort(_KEYS)
    )
    rebuilt = canonical_minute_frame(Warehouse(warehouse), day).filter(
        pl.col("symbol").is_in(symbols)
    ).select(schema.BAR_MINUTE_1M.names).sort(_KEYS)
    if set(rebuilt["symbol"].unique()) != set(symbols):
        raise RuntimeError(f"{day} 当前构建器未以 QMT 重建全部目标标的")
    if rebuilt["source"].unique().to_list() != ["qmt"]:
        raise RuntimeError(f"{day} 当前构建器的目标标的已改变来源")
    if not kept.equals(rebuilt):
        raise RuntimeError(f"{day} 删除前导行后未与当前 QMT 源逐键逐字段一致")
    untouched = old.filter(~pl.col("symbol").is_in(symbols))
    new = pl.concat([untouched, rebuilt]).sort(_KEYS)
    _validate(new, day)
    removed = selected.join(rebuilt.select(_KEYS), on=_KEYS, how="anti")
    expected = int(cases.filter(pl.col("symbol").is_in(symbols))["leading_rows"].sum())
    if removed.height != expected or removed.filter(pl.col("volume") != 0).height:
        raise RuntimeError(f"{day} 待删除行与审计前缀不一致")
    return {
        "day": day,
        "path": target,
        "symbols": symbols,
        "rows_before": old.height,
        "rows_after": new.height,
        "removed_rows": removed.height,
        "before_sha256": _sha256(target),
        "frame": new,
        "old": old,
    }


def apply_qmt_day(warehouse: Path, backup_dir: Path, item: dict) -> dict:
    target: Path = item["path"]
    backup = backup_dir / target.relative_to(warehouse)
    if backup.exists():
        raise FileExistsError(f"backup already exists: {backup}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup)
    if _sha256(backup) != item["before_sha256"]:
        raise RuntimeError(f"{item['day']} 备份校验失败")
    Warehouse(warehouse).write_day(
        "bar_minute_1m",
        item["frame"].to_arrow().cast(schema.BAR_MINUTE_1M),
        day=item["day"],
    )
    written = pl.read_parquet(target).select(schema.BAR_MINUTE_1M.names).sort(_KEYS)
    if not written.equals(item["frame"]):
        raise RuntimeError(f"{item['day']} 写后逐键逐字段复验失败")
    return {
        key: value for key, value in item.items() if key not in {"frame", "old", "path"}
    } | {"day": str(item["day"]), "after_sha256": _sha256(target),
         "backup": str(backup)}


def _summary(cases: pl.DataFrame) -> dict:
    rows = cases.iter_rows(named=True)
    buckets: Counter[tuple[str, str]] = Counter()
    years: Counter[tuple[str, int]] = Counter()
    lengths: Counter[tuple[str, int]] = Counter()
    for row in rows:
        group = "single" if row["leading_rows"] == 1 else "multi"
        buckets[(row["source"], group)] += 1
        years[(row["source"], row["day"].year)] += 1
        lengths[(row["source"], row["leading_rows"])] += 1
    return {
        "total": cases.height,
        "by_source_and_class": {
            f"{source}.{group}": count for (source, group), count in sorted(buckets.items())
        },
        "by_source_and_year": {
            f"{source}.{year}": count for (source, year), count in sorted(years.items())
        },
        "leading_length_distribution": {
            f"{source}.{length}": count
            for (source, length), count in sorted(lengths.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--details", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--lock", type=Path, default=Path("var/warehouse-write.lock"))
    args = parser.parse_args()
    if args.apply and args.backup_dir is None:
        parser.error("--apply 必须同时给 --backup-dir")

    cases = audit(args.warehouse)
    args.details.parent.mkdir(parents=True, exist_ok=True)
    cases.write_parquet(args.details)
    qmt = cases.filter((pl.col("source") == "qmt") & (pl.col("leading_rows") >= 2))
    plans = []
    changes = []
    context = single_instance(args.lock) if args.apply else nullcontext()
    with context:
        for day in sorted(qmt["day"].unique().to_list()):
            item = plan_qmt_day(args.warehouse, day, qmt.filter(pl.col("day") == day))
            plans.append({
                key: value for key, value in item.items()
                if key not in {"frame", "old", "path"}
            } | {"day": str(day)})
            if args.apply:
                changes.append(apply_qmt_day(args.warehouse, args.backup_dir, item))
    report = {
        "operation": "repair_canonical_minute_padding",
        "generated_at": datetime.now().isoformat(),
        "applied": args.apply,
        "warehouse": str(args.warehouse),
        "details": str(args.details),
        "summary": _summary(cases),
        "qmt_proven_plan": {
            "symbol_days": qmt.height,
            "days": len(plans),
            "removed_rows": sum(item["removed_rows"] for item in plans),
            "partitions": plans,
        },
        "changes": changes,
        "protected_unmodified": {
            "paid_multi_symbol_days": cases.filter(
                (pl.col("source") == "paid") & (pl.col("leading_rows") >= 2)
            ).height,
            "single_symbol_days": cases.filter(pl.col("leading_rows") == 1).height,
            "reason": "尚未取得逐行独立证据；禁止按形状删除",
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "applied": args.apply,
        "summary": report["summary"]["by_source_and_class"],
        "qmt_proven_plan": {
            key: report["qmt_proven_plan"][key]
            for key in ("symbol_days", "days", "removed_rows")
        },
        "report": str(args.report),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
