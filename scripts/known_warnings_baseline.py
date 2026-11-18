"""已知警告基线候选生成工具（#662 计划 04；只读）。

从仓库的 metric_checks 取最近一次（或指定 run_id）质检中未通过、非阻断、且不是上游自相矛盾的结果，
计算违规样本指纹，打印可直接粘贴进 ``qsys.data.catalog.known_warnings.KNOWN_WARNINGS`` 的字面量。
不写任何文件；不经 DuckDB（metric_checks 很小，polars 直接读 Parquet）。输出须经代码评审后再提交。

用法：python scripts/known_warnings_baseline.py --warehouse PATH --sample-root PATH --issue "#594"
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path, PurePath

import polars as pl

from qsys.data.catalog.known_warnings import UPSTREAM_SELF_CONTRADICTION, sample_fingerprint


def _rows(warehouse: Path) -> pl.DataFrame:
    files = sorted((warehouse / "metric_checks").glob("year=*/part.parquet"))
    if not files:
        return pl.DataFrame()
    return pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")


def _sample(row: dict, root: Path) -> Path | None:
    path = row.get("sample_path")
    if not path:
        return None
    parts = PurePath(str(path)).parts[-2:]
    if parts != (str(row["run_id"]), f"{row['check_id']}.parquet"):
        return None
    return root / parts[0] / parts[1]


def _lit(value) -> str:
    if isinstance(value, date):
        return f"date({value.year}, {value.month}, {value.day})"
    return repr(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--sample-root", type=Path, required=True)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--registered-on", type=date.fromisoformat, default=date.today())
    args = parser.parse_args(argv)

    frame = _rows(args.warehouse)
    if frame.is_empty():
        print("()")
        return 0
    run = args.run_id or frame.sort("checked_at")["run_id"][-1]
    picked = frame.filter(
        (pl.col("run_id") == run) & ~pl.col("passed").fill_null(False)
        & (pl.col("severity") != "blocking")
        & (pl.col("known_issue").fill_null("").str.strip_chars() != UPSTREAM_SELF_CONTRADICTION)
    ).sort(["check_id", "metric_id"])
    lines = ["("]
    for row in picked.iter_rows(named=True):
        if row["violations"] is None:  # 缺失不当 0：无法形成基线，只提示
            lines.append(f"    # 跳过 {row['check_id']} / {row['metric_id']}：违规数缺失，无法形成基线")
            continue
        fields = {
            "check_id": row["check_id"], "metric_id": row["metric_id"], "issue": args.issue,
            "baseline_violations": int(row["violations"]),
            "baseline_sample_sha256": sample_fingerprint(_sample(row, args.sample_root)),
            "registered_on": args.registered_on, "note": f"{run} 生成，待评审",
        }
        lines.append("    KnownWarning(" + ", ".join(f"{k}={_lit(v)}" for k, v in fields.items()) + "),")
    lines.append(")")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
