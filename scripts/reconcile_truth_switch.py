"""切源对账：`stock_st` 直取真值 vs 旧名称链路，逐键差异报告。

在临时仓库里用旧版 `flags.py`（git 对象，默认 96803af）重算一份旧语义的
`daily_flags` 与 `market_regime`，与生产表逐键比对。源表以软链接接入，
生产目录全程只读。

差异分四类落盘：ST 翻转、首板翻转、炸板翻转、涨跌停价差异；生态因子
按列给出差异行数与最大绝对差。摘要含行数、区间与每份产物的 SHA256。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

from qsys.data.pipeline.regime import build_market_regime
from qsys.data.storage import Warehouse

# 旧版 build_daily_flags 读的源表；regime 另需 adj_factors 与 index_bars。
_SOURCE_TABLES = (
    "tushare_daily", "paid_daily", "name_snapshots", "name_history",
    "instruments", "adj_factors", "index_bars",
)
_BOARDS = {"main", "gem", "star"}
# 逐键比对的事实列。st_source 单独看，它在两套语义下必然不同。
_FACT_COLUMNS = (
    "prev_close", "adj_prev_close", "limit_up", "limit_down",
    "is_limit_up", "is_limit_down", "touched_limit_up",
    "is_broken_board", "is_first_board", "limit_pct", "is_st",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare_scratch(production: Path, scratch: Path) -> Warehouse:
    """软链源表、清空派生表。软链接让重算读生产数据但写不进去。"""
    scratch.mkdir(parents=True, exist_ok=True)
    for name in _SOURCE_TABLES:
        link = scratch / name
        target = (production / name).resolve()
        if link.is_symlink():
            link.unlink()
        if not target.exists():
            raise SystemExit(f"生产仓库缺少源表：{target}")
        link.symlink_to(target)
    for name in ("daily_flags", "market_regime"):
        derived = scratch / name
        if derived.is_symlink():
            raise SystemExit(f"{derived} 是软链接，重算会写进生产目录，拒绝执行")
        if derived.exists():
            for leaf in sorted(derived.rglob("*"), reverse=True):
                leaf.unlink() if leaf.is_file() else leaf.rmdir()
            derived.rmdir()
    return Warehouse(scratch)


def load_old_flags(repo: Path, ref: str, scratch: Path):
    """从 git 对象取旧版模块，按文件路径加载——不覆盖已导入的新版。"""
    source = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:src/qsys/data/pipeline/flags.py"],
        capture_output=True, check=True,
    ).stdout
    path = scratch / "_old_flags.py"
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("_old_flags", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, hashlib.sha256(source).hexdigest()


def _flags(root: Path) -> pl.LazyFrame:
    return pl.scan_parquet(str(root / "daily_flags" / "**" / "*.parquet"))


def compare_flags(old_root: Path, new_root: Path, lo: date, hi: date, out: Path) -> dict:
    window = pl.col("dt").is_between(lo, hi)
    old = _flags(old_root).filter(window)
    new = _flags(new_root).filter(window)

    old_keys = old.select("symbol", "dt")
    new_keys = new.select("symbol", "dt")
    only_old = old_keys.join(new_keys, on=["symbol", "dt"], how="anti").collect().height
    only_new = new_keys.join(old_keys, on=["symbol", "dt"], how="anti").collect().height

    joined = old.select("symbol", "dt", "st_source", *_FACT_COLUMNS).join(
        new.select("symbol", "dt", "st_source", *_FACT_COLUMNS),
        on=["symbol", "dt"], how="inner", suffix="_new",
    )

    report: dict[str, object] = {
        "keys": {
            "old_rows": old.select(pl.len()).collect().item(),
            "new_rows": new.select(pl.len()).collect().item(),
            "only_in_old": only_old,
            "only_in_new": only_new,
        },
        "columns": {},
        "files": {},
    }

    # 逐列差异计数。null 与非 null 之间的变化也算差异，ne_missing 两侧都认。
    for column in _FACT_COLUMNS:
        differs = joined.filter(
            pl.col(column).ne_missing(pl.col(f"{column}_new"))
        ).select(pl.len()).collect().item()
        report["columns"][column] = differs

    dumps = {
        "switch_st_flips": pl.col("is_st").ne_missing(pl.col("is_st_new")),
        "switch_first_board_flips": pl.col("is_first_board").ne_missing(
            pl.col("is_first_board_new")
        ),
        "switch_broken_board_flips": pl.col("is_broken_board").ne_missing(
            pl.col("is_broken_board_new")
        ),
        "switch_limit_price_diffs": pl.col("limit_up").ne_missing(
            pl.col("limit_up_new")
        ) | pl.col("limit_down").ne_missing(pl.col("limit_down_new")),
    }
    for name, predicate in dumps.items():
        frame = joined.filter(predicate).sort(["dt", "symbol"]).collect()
        target = out / f"{name}.parquet"
        frame.write_parquet(target)
        report["files"][target.name] = {
            "rows": frame.height, "sha256": _sha256(target),
            "bytes": target.stat().st_size,
        }
    return report


def compare_regime(old_root: Path, new_root: Path, out: Path) -> dict:
    old = pl.read_parquet(str(old_root / "market_regime" / "**" / "*.parquet"))
    new = pl.read_parquet(str(new_root / "market_regime" / "**" / "*.parquet"))
    new = new.filter(pl.col("dt").is_in(old["dt"]))
    joined = old.join(new, on="dt", how="inner", suffix="_new").sort("dt")

    factors = [c for c in old.columns if c != "dt" and f"{c}_new" in joined.columns]
    per_column: dict[str, dict] = {}
    for column in factors:
        differs = joined.filter(pl.col(column).ne_missing(pl.col(f"{column}_new")))
        entry: dict[str, object] = {"rows_differ": differs.height}
        if differs.height and joined[column].dtype.is_numeric():
            gap = (differs[column] - differs[f"{column}_new"]).abs()
            entry["max_abs_diff"] = float(gap.max())
        per_column[column] = entry

    changed = [c for c in factors if per_column[c]["rows_differ"]]
    target = out / "switch_regime_diffs.parquet"
    if changed:
        mask = changed[0]
        predicate = pl.col(mask).ne_missing(pl.col(f"{mask}_new"))
        for column in changed[1:]:
            predicate = predicate | pl.col(column).ne_missing(pl.col(f"{column}_new"))
        rows = joined.filter(predicate)
    else:
        rows = joined.clear()
    rows.write_parquet(target)
    return {
        "old_rows": old.height, "new_rows": new.height,
        "compared_days": joined.height, "columns": per_column,
        "file": {
            "name": target.name, "rows": rows.height,
            "sha256": _sha256(target), "bytes": target.stat().st_size,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--old-ref", default="96803af")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--out", type=Path, default=Path("artifacts"))
    parser.add_argument("--skip-rebuild", action="store_true")
    args = parser.parse_args()

    started = datetime.now(ZoneInfo("Asia/Shanghai"))
    args.out.mkdir(parents=True, exist_ok=True)
    scratch_warehouse = (
        Warehouse(args.scratch) if args.skip_rebuild
        else prepare_scratch(args.warehouse, args.scratch)
    )
    old_flags, source_sha = load_old_flags(args.repo, args.old_ref, args.scratch)

    rebuilt = {}
    if not args.skip_rebuild:
        rebuilt["daily_flags"] = old_flags.build_daily_flags(
            scratch_warehouse, args.start, args.end, boards=_BOARDS
        )
        print(f"旧语义 daily_flags 重算完成：{rebuilt['daily_flags']} 行", flush=True)
        rebuilt["market_regime"] = build_market_regime(
            scratch_warehouse, args.start, args.end, boards=_BOARDS
        )
        print(f"旧语义 market_regime 重算完成：{rebuilt['market_regime']} 行", flush=True)

    record = {
        "operation": "reconcile_truth_switch",
        "started_at": started.isoformat(),
        "warehouse": str(args.warehouse.resolve()),
        "scratch": str(args.scratch.resolve()),
        "old_ref": args.old_ref,
        "old_flags_sha256": source_sha,
        "window": {"start": str(args.start), "end": str(args.end)},
        "rebuilt": rebuilt,
        "flags": compare_flags(args.scratch, args.warehouse, args.start, args.end, args.out),
        "regime": compare_regime(args.scratch, args.warehouse, args.out),
    }
    record["ended_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    target = args.out / f"switch_reconcile_{started:%Y%m%d_%H%M%S}.json"
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(f"对账摘要：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
