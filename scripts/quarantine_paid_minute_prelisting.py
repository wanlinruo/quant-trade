#!/usr/bin/env python3
"""按官方上市日期隔离上市前分钟记录；不修改任何源字段。"""

import argparse
import json
import os
from pathlib import Path

import duckdb
import polars as pl

from qsys.data.pipeline.paid_minute import SCHEMA, _atomic_json, _write_shard


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--warehouse", type=Path, required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--listing-snapshot", type=Path, required=True)
    args = ap.parse_args()
    stage = args.warehouse / "paid_minute_bars_staging" / f"year={args.year}"
    quality = args.warehouse / "paid_minute_quality" / f"year={args.year}"
    snapshot = json.loads(args.listing_snapshot.read_text())
    listing_rows = []
    for record in snapshot["records"]:
        listing_rows.append({"symbol": record["symbol"], "listing_date": record["listing_date"]})
        # 存量北交所公司在 920 代码切换前使用原全国股转代码。官方对照表同时
        # 给出两者，因此两种直接源代码都使用同一官方上市日期判断范围。
        if record.get("old_symbol"):
            listing_rows.append({"symbol": record["old_symbol"], "listing_date": record["listing_date"]})
    listing = pl.DataFrame(listing_rows).unique().with_columns(
        pl.col("listing_date").str.strptime(pl.Date, "%Y%m%d")
    )
    con = duckdb.connect()
    aggregate_days = pl.from_arrow(con.execute("""
        SELECT symbol, dt AS trade_date
        FROM read_parquet(?)
        ORDER BY symbol, dt
    """, [str(quality / "daily_aggregate.parquet")]).arrow())
    con.close()
    # 官方快照只证明其中列明证券的上市日期。只隔离这些证券在该日期之前的
    # 直接源记录；未出现在快照中的证券不作推断，也不受影响。
    candidates = aggregate_days.join(listing, on="symbol", how="inner").filter(
        pl.col("trade_date") < pl.col("listing_date")
    )
    keys = candidates.select("symbol", "trade_date").with_columns(pl.lit(True).alias("remove"))
    prepared = []
    total_rows = 0
    evidence_root = args.warehouse / "paid_minute_quarantine" / f"year={args.year}" / "outside-official-listing-period"
    for source in sorted(stage.glob("shard=*/part.parquet")):
        frame = pl.read_parquet(source)
        marked = frame.with_columns(pl.col("dt").dt.date().alias("trade_date")).join(
            keys, on=["symbol", "trade_date"], how="left"
        )
        evidence = marked.filter(pl.col("remove") == True).select(SCHEMA.names)
        if evidence.height == 0:
            continue
        kept = marked.filter(pl.col("remove").is_null()).select(SCHEMA.names)
        cleaned = source.with_name("part.prelisting-cleaned.parquet")
        digest = _write_shard(kept, cleaned)
        target = evidence_root / source.parent.name / "part.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".parquet.tmp")
        evidence.with_columns(pl.lit("OUTSIDE_OFFICIAL_LISTING_PERIOD").alias("reason")).write_parquet(temp)
        receipt_path = source.parent / "manifest.json"
        receipt = json.loads(receipt_path.read_text())
        receipt.update(rows=kept.height, symbols=kept["symbol"].n_unique(),
                       start=str(kept["dt"].min()), end=str(kept["dt"].max()),
                       parquet_sha256=digest, quarantined_prelisting_rows=evidence.height)
        prepared.append((source, cleaned, target, temp, receipt_path, receipt))
        total_rows += evidence.height
    observed_days = sum(
        pl.read_parquet(temp, columns=["symbol", "dt"]).with_columns(pl.col("dt").dt.date()).unique().height
        for _, _, _, temp, _, _ in prepared
    )
    if observed_days != candidates.height:
        raise ValueError(f"待隔离键不完整: {observed_days} != {candidates.height}")
    for source, cleaned, target, temp, receipt_path, receipt in prepared:
        os.replace(cleaned, source); os.replace(temp, target); _atomic_json(receipt_path, receipt)
    manifest_path = stage / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    records = [json.loads(p.read_text()) for p in sorted(stage.glob("shard=*/manifest.json"))]
    manifest.update(rows=sum(r["rows"] for r in records), symbols=sum(r["symbols"] for r in records),
                    shard_inventory=records, prelisting_quarantine={
                        "days": candidates.height, "rows": total_rows,
                        "official_snapshot": str(args.listing_snapshot),
                        "reason": "OUTSIDE_OFFICIAL_LISTING_PERIOD"})
    _atomic_json(manifest_path, manifest)
    print(json.dumps({"year": args.year, "days": candidates.height, "rows": total_rows,
                      "shards": len(prepared)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
