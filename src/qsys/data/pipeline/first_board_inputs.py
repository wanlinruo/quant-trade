"""Reusable point-in-time inputs for Issue #230 and later strategies."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl

from qsys.data import schema
from qsys.data.pipeline.tushare_reference import _date_expr, _replace_days
from qsys.data.storage import Warehouse


_MONEY_COLUMNS = [name for name in schema.MONEYFLOW_DAILY.names
                  if name not in {"symbol", "dt"}]


def ingest_moneyflow(provider, warehouse: Warehouse, days: Sequence[date]) -> int:
    """Replace complete trading-day money-flow snapshots; empty is a failure."""
    from qsys.data.query import WarehouseQuery

    frames: list[pl.DataFrame] = []
    for day in days:
        raw = provider.moneyflow(day)
        if raw.is_empty():
            raise RuntimeError(f"moneyflow returned no rows for {day:%Y%m%d}")
        frames.append(raw.select(
            pl.col("ts_code").alias("symbol"),
            _date_expr("trade_date").alias("dt"),
            *[(pl.col(name) * 10_000.0).alias(name) for name in _MONEY_COLUMNS],
        ))
    incoming = pl.concat(frames, how="vertical_relaxed")
    if incoming.select(pl.struct("symbol", "dt").is_duplicated().any()).item():
        raise ValueError("moneyflow contains duplicate symbol/day rows")
    query = WarehouseQuery(warehouse)
    return _replace_days(
        warehouse, "moneyflow_daily", incoming, schema.MONEYFLOW_DAILY, days,
        lambda lo, hi: query.moneyflow(None, lo, hi),
    )


def ingest_industry_membership(provider, warehouse: Warehouse) -> int:
    """Atomically replace the complete dated SW membership dimension."""
    raw = provider.industry_membership()
    if raw.is_empty():
        raise RuntimeError("index_member_all returned no rows")
    frame = raw.select(
        pl.col("ts_code").alias("symbol"),
        *[pl.col(name).cast(pl.Utf8) for name in (
            "l1_code", "l1_name", "l2_code", "l2_name", "l3_code", "l3_name",
        )],
        _date_expr("in_date").alias("in_date"),
        _date_expr("out_date").alias("out_date"),
        (pl.col("is_new").cast(pl.Utf8) == "Y").alias("is_new"),
    ).unique(subset=["symbol", "l3_code", "in_date"], keep="last")
    if frame["in_date"].null_count():
        raise ValueError("industry membership contains null in_date")
    warehouse.write(
        "industry_membership",
        frame.sort(["symbol", "in_date", "l3_code"])
        .to_arrow().cast(schema.INDUSTRY_MEMBERSHIP),
    )
    return frame.height


def ingest_auction_open(provider, warehouse: Warehouse, days: Sequence[date]) -> int:
    """Store source-native auction facts in quarantine pending QMT parity."""
    from qsys.data.query import WarehouseQuery

    frames: list[pl.DataFrame] = []
    for day in days:
        raw = provider.auction_open(day)
        if raw.is_empty():
            raise RuntimeError(f"stk_auction_o returned no rows for {day:%Y%m%d}")
        frames.append(raw.select(
            pl.col("ts_code").alias("symbol"),
            _date_expr("trade_date").alias("dt"),
            "open", "close", "high", "low",
            pl.col("vol").alias("volume_raw"),
            pl.col("amount").alias("amount_raw"), "vwap",
        ))
    incoming = pl.concat(frames, how="vertical_relaxed")
    query = WarehouseQuery(warehouse)
    return _replace_days(
        warehouse, "auction_open_daily", incoming, schema.AUCTION_OPEN_DAILY, days,
        lambda lo, hi: query.auction_open(None, lo, hi),
    )
