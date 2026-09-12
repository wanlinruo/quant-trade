"""付费日线的可撤回修补层。

原始 ``paid_daily`` 永不改写。这里只接受有两路独立直接证据的逐字段修补记录，
并在独立查询入口中覆盖对应单元格。
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import polars as pl
import pyarrow as pa

from qsys.data.pipeline.paid_daily import query_paid_daily


REPAIR_SCHEMA = pa.schema([
    ("repair_id", pa.string()),
    ("batch_id", pa.string()),
    ("symbol", pa.string()),
    ("dt", pa.date32()),
    ("field", pa.string()),
    ("original_value", pa.float64()),
    ("repaired_value", pa.float64()),
    ("source_a_family", pa.string()),
    ("source_a_value", pa.float64()),
    ("source_a_evidence_sha256", pa.string()),
    ("source_b_family", pa.string()),
    ("source_b_value", pa.float64()),
    ("source_b_evidence_sha256", pa.string()),
    ("decision", pa.string()),
    ("reason", pa.string()),
    ("rule_version", pa.string()),
])

REPAIRABLE_FIELDS = {
    "open", "high", "low", "close", "pre_close", "change", "pct_change",
    "volume", "amount", "factor", "turnover_ratio", "free_turnover_ratio",
    "volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm",
    "dividend_yield", "dividend_yield_ttm", "total_shares",
    "circulating_shares", "free_shares", "market_cap", "circulating_market_cap",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def validate_decisions(decisions: pl.DataFrame) -> None:
    """拒绝缺证据、同源或可能覆盖错原值的已接纳修补。"""
    missing = set(REPAIR_SCHEMA.names) - set(decisions.columns)
    if missing:
        raise ValueError(f"repair decisions missing columns: {sorted(missing)}")
    accepted = decisions.filter(pl.col("decision") == "ACCEPTED")
    if accepted.is_empty():
        return
    if accepted.select(pl.struct("symbol", "dt", "field").is_duplicated().any()).item():
        raise ValueError("duplicate accepted repair key")
    bad_fields = set(accepted["field"].unique().to_list()) - REPAIRABLE_FIELDS
    if bad_fields:
        raise ValueError(f"non-repairable fields: {sorted(bad_fields)}")
    if accepted.filter(pl.col("source_a_family") == pl.col("source_b_family")).height:
        raise ValueError("accepted repair requires two independent source families")
    if accepted.filter(
        pl.col("source_a_evidence_sha256").is_null()
        | pl.col("source_b_evidence_sha256").is_null()
    ).height:
        raise ValueError("accepted repair is missing evidence hash")
    for column in ["source_a_evidence_sha256", "source_b_evidence_sha256"]:
        if any(not _SHA256.fullmatch(value) for value in accepted[column].to_list()):
            raise ValueError("invalid evidence sha256")
    # 入库值必须逐字取自指定主来源，不能是两源均值或其他计算结果。
    if accepted.filter(pl.col("repaired_value") != pl.col("source_a_value")).height:
        raise ValueError("repaired value must equal direct primary-source value")


def apply_accepted_repairs(frame: pl.DataFrame, decisions: pl.DataFrame) -> pl.DataFrame:
    """把 ACCEPTED 旁路修补叠加到查询结果；先确认原值仍与决策时一致。"""
    validate_decisions(decisions)
    accepted = decisions.filter(pl.col("decision") == "ACCEPTED")
    if accepted.is_empty() or frame.is_empty():
        return frame
    result = frame
    for field in accepted["field"].unique().sort().to_list():
        patch = accepted.filter(pl.col("field") == field).select(
            "symbol", "dt",
            pl.col("original_value").alias("_expected_original"),
            pl.col("repaired_value").alias("_repair_value"),
        )
        result = result.join(patch, on=["symbol", "dt"], how="left")
        touched = result.filter(pl.col("_repair_value").is_not_null())
        mismatch = touched.filter(
            ~(
                (pl.col(field).is_null() & pl.col("_expected_original").is_null())
                | (pl.col(field) == pl.col("_expected_original"))
            )
        )
        if mismatch.height:
            raise ValueError(f"raw value changed since repair decision: {field}")
        result = result.with_columns(
            pl.when(pl.col("_repair_value").is_not_null())
            .then(pl.col("_repair_value"))
            .otherwise(pl.col(field))
            .alias(field)
        ).drop("_expected_original", "_repair_value")
    return result


def load_repair_decisions(warehouse: Path) -> pl.DataFrame:
    paths = sorted((warehouse / "paid_daily_repairs").glob("batch=*/decisions.parquet"))
    if not paths:
        return pl.from_arrow(REPAIR_SCHEMA.empty_table())
    result = pl.concat([pl.read_parquet(path) for path in paths], how="vertical_relaxed")
    validate_decisions(result)
    return result


def query_paid_daily_curated(
    warehouse: Path,
    start: date,
    end: date,
    symbols: list[str] | None = None,
    *,
    base_date: date | None = None,
) -> pl.DataFrame:
    """查询原始付费日线并叠加可审计的 ACCEPTED 修补；原始入口行为不变。"""
    # 必须先修原始字段，再生成用户显式请求的复权展示列；否则 qfq_* 会继续引用
    # 修补前的 OHLC。复权展示仍沿用 paid_daily 的既有确定性公式，不进入修补表。
    raw = query_paid_daily(warehouse, start, end, symbols)
    decisions = load_repair_decisions(warehouse)
    result = apply_accepted_repairs(raw, decisions)
    if base_date is not None:
        anchor = warehouse / "paid_adjustment_bases" / f"base_date={base_date}" / "part.parquet"
        if not anchor.exists():
            raise ValueError(f"adjustment base has not been frozen: {base_date}")
        result = result.join(pl.read_parquet(anchor), on="symbol", how="left").with_columns(
            *[
                pl.when((pl.col("base_factor") > 0) & (pl.col("factor") > 0))
                .then(pl.col(column) * pl.col("factor") / pl.col("base_factor"))
                .otherwise(None)
                .alias(f"qfq_{column}")
                for column in ["open", "high", "low", "close"]
            ]
        )
    return result.sort(["symbol", "dt"])
