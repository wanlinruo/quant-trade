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


def validate_industry_intervals(frame: pl.DataFrame) -> None:
    """Reject ambiguous PIT membership before publishing a full replacement."""
    keys = ("symbol", "l3_code", "in_date")
    if any(frame[column].null_count() for column in keys):
        raise ValueError("industry membership contains null primary key")
    if frame.select(pl.struct(*keys).is_duplicated().any()).item():
        raise ValueError("industry membership contains duplicate interval keys")

    by_symbol: dict[str, list[tuple[date, date | None]]] = {}
    for row in frame.select("symbol", "in_date", "out_date").iter_rows(named=True):
        start, end = row["in_date"], row["out_date"]
        if end is not None and end < start:
            raise ValueError(f"industry membership has inverted interval: {row}")
        by_symbol.setdefault(row["symbol"], []).append((start, end))
    for symbol, intervals in by_symbol.items():
        previous_end: date | None = None
        for index, (start, end) in enumerate(sorted(intervals, key=lambda item: item[0])):
            if index and (previous_end is None or previous_end >= start):
                raise ValueError(
                    f"industry membership has overlapping intervals: {symbol} {start}"
                )
            previous_end = end


def verify_first_board_asset_health(warehouse: Warehouse, day: date) -> int:
    """Read back daily/PIT assets and enforce the current auction quarantine."""
    from qsys.data.catalog import DATASETS
    from qsys.data.query import WarehouseQuery

    query = WarehouseQuery(warehouse)
    moneyflow = query.moneyflow(None, day, day)
    if moneyflow.is_empty():
        raise RuntimeError(f"moneyflow_daily missing session {day}")
    if moneyflow.select(pl.struct("symbol", "dt").is_duplicated().any()).item():
        raise RuntimeError(f"moneyflow_daily duplicate keys on {day}")

    industry = query.industry_membership()
    if industry.is_empty():
        raise RuntimeError("industry_membership is empty")
    validate_industry_intervals(industry)

    lifecycle = DATASETS["auction_open_daily"].lifecycle
    if "当前隔离" in lifecycle:
        files = sorted((warehouse.root / "auction_open_daily").rglob("*.parquet"))
        if files:
            raise RuntimeError(
                "auction_open_daily must remain empty while quarantined: "
                + ", ".join(str(path) for path in files[:5])
            )
    return moneyflow.height + industry.height


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
            *[
                (pl.col(name) * 10_000.0 if name.endswith("_amount") else pl.col(name))
                .alias(name)
                for name in _MONEY_COLUMNS
            ],
        ))
    incoming = pl.concat(frames, how="vertical_relaxed")
    if incoming.select(pl.struct("symbol", "dt").is_duplicated().any()).item():
        raise ValueError("moneyflow contains duplicate symbol/day rows")
    query = WarehouseQuery(warehouse)
    written = _replace_days(
        warehouse, "moneyflow_daily", incoming, schema.MONEYFLOW_DAILY, days,
        lambda lo, hi: query.moneyflow(None, lo, hi),
    )
    for day in days:
        expected = incoming.filter(pl.col("dt") == day).select("symbol", "dt").sort(
            ["symbol", "dt"]
        )
        actual = query.moneyflow(None, day, day).select("symbol", "dt").sort(
            ["symbol", "dt"]
        )
        if not expected.equals(actual):
            raise RuntimeError(f"moneyflow write-back key mismatch on {day}")
    return written


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
    )
    validate_industry_intervals(frame)
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
