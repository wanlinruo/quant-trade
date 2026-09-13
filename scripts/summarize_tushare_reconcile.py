"""把全历史 Tushare/百度网盘逐键差异压缩成可审阅 JSON。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import polars as pl


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diffs", type=Path)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    diffs = pl.read_parquet(args.diffs).with_columns(
        pl.col("dt").dt.year().alias("year")
    )
    common_rows = int(json.loads(args.summary.read_text())["common_rows"])
    fields = []
    for row in diffs.group_by("field").agg(
        pl.len().alias("rows"),
        pl.col("abs_diff").is_null().sum().alias("null_mismatches"),
        pl.col("abs_diff").median().alias("median_abs_diff"),
        pl.col("abs_diff").max().alias("max_abs_diff"),
    ).sort("rows", descending=True).to_dicts():
        field = row["field"]
        years = (
            diffs.filter(pl.col("field") == field)
            .group_by("year").len().sort("year")
        )
        row["ratio_of_common_rows"] = row["rows"] / common_rows
        row["by_year"] = {str(year): count for year, count in years.iter_rows()}
        fields.append(row)

    payload = {
        "source": str(args.diffs),
        "common_rows": common_rows,
        "field_count": len(fields),
        "fields": fields,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
