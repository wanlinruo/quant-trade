#!/usr/bin/env python3
"""把 #119 的 QMT 独立验真结论幂等写入永久 ``metric_evidence``。"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import polars as pl

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


SOURCE = "QMT get_divid_factors raw archive"
METRIC = "adj_factors.cum_factor"
SCOPES = ("d1_non_reform_a_share", "d2_bj_92", "d2_bj_legacy")


def evidence_rows(details: pl.DataFrame, checked_at: datetime) -> pl.DataFrame:
    scoped = details.filter(pl.col("scope").is_in(SCOPES))
    return scoped.with_columns(
        pl.lit(METRIC).alias("metric_id"),
        pl.format("daily_jump={}", pl.col("direct")).alias("our_value"),
        pl.when(pl.col("qmt_factor").is_not_null())
        .then(pl.format("single_event_factor={}", pl.col("qmt_factor")))
        .when(pl.col("attribution") == "qmt_no_event_on_date")
        .then(pl.lit("NO_EVENT_ON_DATE"))
        .otherwise(pl.lit("SYMBOL_MISSING"))
        .alias("reference_value"),
        pl.lit(SOURCE).alias("reference_source"),
        pl.when(pl.col("attribution") == "qmt_supports_tushare_factor")
        .then(pl.lit("match"))
        .when(pl.col("attribution") == "qmt_supports_price_gap")
        .then(pl.lit("mismatch"))
        .otherwise(pl.lit("inconclusive"))
        .alias("verdict"),
        pl.lit("Codex").alias("checked_by"),
        pl.lit(checked_at).alias("checked_at"),
        pl.format(
            "issue #119; scope={}; shape={}; attribution={}; "
            "days_from_official_listing={}",
            pl.col("scope"), pl.col("shape"), pl.col("attribution"),
            pl.col("days_from_listing"),
        ).alias("note"),
    ).select(schema.METRIC_EVIDENCE.names)


def record(warehouse: Warehouse, details: Path, checked_at: datetime) -> tuple[int, int]:
    incoming = evidence_rows(pl.read_parquet(details), checked_at)
    existing_all = WarehouseQuery(warehouse).metric_evidence()
    keys = ["metric_id", "symbol", "dt", "reference_source"]
    if existing_all.height:
        incoming = incoming.join(
            existing_all.select(keys).unique(), on=keys, how="anti"
        )
    for year in incoming["dt"].dt.year().unique().sort().to_list():
        rows = incoming.filter(pl.col("dt").dt.year() == year)
        existing = existing_all.filter(pl.col("dt").dt.year() == year)
        warehouse.write(
            "metric_evidence",
            pl.concat([existing, rows], how="vertical")
            .to_arrow().cast(schema.METRIC_EVIDENCE),
            year=year,
        )
    return incoming.height, evidence_rows(pl.read_parquet(details), checked_at).height


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--details", type=Path, required=True)
    args = parser.parse_args()
    added, total = record(Warehouse(args.warehouse), args.details, datetime.now())
    print(f"added={added} scoped_evidence={total}")


if __name__ == "__main__":
    main()
