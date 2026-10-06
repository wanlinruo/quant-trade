#!/usr/bin/env python3
"""用既有日级质量产物归因 D-20，不重扫全部付费分钟事实。

默认只读取 ``daily_discrepancies.parquet`` 与独立的 Tushare 日线。传入
``--verify-bj-post-close`` 时，才额外扫描有差异的北交所标的日，验证把
15:01--15:30 源记录纳入日聚合后量额差异是否消失。脚本从不改写事实表。
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from qsys.data.pipeline.minute_repairs import compare_with_daily, daily_from_minutes


def _sql_path(path: Path | str) -> str:
    return str(path).replace("'", "''")


def _row(cursor: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    values = cursor.fetchone()
    assert values is not None
    return dict(zip((column[0] for column in cursor.description), values))


def _daily_attribution(con: duckdb.DuckDBPyConnection, warehouse: Path,
                       year: int) -> dict[str, Any]:
    discrepancy = warehouse / "paid_minute_quality" / f"year={year}" \
        / "daily_discrepancies.parquet"
    independent = warehouse / "tushare_daily" / f"year={year}" / "part.parquet"
    if not discrepancy.exists() or not independent.exists():
        raise ValueError(f"{year} 缺少差异产物或 Tushare 日线")
    d = _sql_path(discrepancy)
    t = _sql_path(independent)
    result = _row(con.execute(f"""
        WITH x AS (
          SELECT d.*, t.open AS t_open, t.high AS t_high, t.low AS t_low,
                 t.close AS t_close, t.volume * 100 AS t_volume,
                 t.amount AS t_amount
          FROM read_parquet('{d}') d
          JOIN read_parquet('{t}') t USING(symbol, dt)
        )
        SELECT count(*)::BIGINT AS discrepancy_rows,
          count(*) FILTER (WHERE ohlc_mismatch)::BIGINT AS ohlc_days,
          count(*) FILTER (WHERE volume_mismatch)::BIGINT AS volume_days,
          count(*) FILTER (WHERE amount_mismatch_gt_100)::BIGINT AS amount_days,
          count(*) FILTER (WHERE ohlc_mismatch AND
            abs(paid_open-t_open)<=0.005 AND abs(paid_high-t_high)<=0.005 AND
            abs(paid_low-t_low)<=0.005 AND abs(paid_close-t_close)<=0.005
          )::BIGINT AS ohlc_paid_daily_matches_independent,
          count(*) FILTER (WHERE ohlc_mismatch AND
            greatest(abs(open-paid_open), abs(high-paid_high),
                     abs(low-paid_low), abs(close-paid_close))<=0.02
          )::BIGINT AS ohlc_difference_le_2c,
          count(*) FILTER (WHERE ohlc_mismatch AND
            greatest(abs(open-paid_open), abs(high-paid_high),
                     abs(low-paid_low), abs(close-paid_close))>0.02 AND
            greatest(abs(open-paid_open), abs(high-paid_high),
                     abs(low-paid_low), abs(close-paid_close))<=0.10
          )::BIGINT AS ohlc_difference_2c_to_10c,
          count(*) FILTER (WHERE ohlc_mismatch AND
            greatest(abs(open-paid_open), abs(high-paid_high),
                     abs(low-paid_low), abs(close-paid_close))>0.10
          )::BIGINT AS ohlc_difference_gt_10c,
          count(*) FILTER (WHERE volume_mismatch AND
            abs(paid_volume_lots*100-t_volume)<=0.5
          )::BIGINT AS volume_paid_daily_matches_independent,
          count(*) FILTER (WHERE volume_mismatch AND
            abs(volume-paid_volume_lots*100)<=100
          )::BIGINT AS volume_rounding_le_one_lot,
          count(*) FILTER (WHERE volume_mismatch AND
            abs(volume-paid_volume_lots*100)>100 AND
            abs(volume-paid_volume_lots*100)<=greatest(1000, paid_volume_lots*0.1)
          )::BIGINT AS volume_small_residual,
          count(*) FILTER (WHERE volume_mismatch AND
            abs(volume-paid_volume_lots*100)>greatest(1000, paid_volume_lots*0.1)
          )::BIGINT AS volume_material,
          count(*) FILTER (WHERE amount_mismatch_gt_100 AND
            abs(paid_amount-t_amount)<=1
          )::BIGINT AS amount_paid_daily_matches_independent,
          count(*) FILTER (WHERE amount_mismatch_gt_100 AND right(symbol, 2)='BJ'
          )::BIGINT AS amount_bj,
          count(*) FILTER (WHERE amount_mismatch_gt_100 AND right(symbol, 2)<>'BJ'
          )::BIGINT AS amount_non_bj
        FROM x
    """))
    result["year"] = year
    return result


def _bj_post_close(con: duckdb.DuckDBPyConnection, warehouse: Path,
                   year: int) -> dict[str, Any]:
    discrepancy = warehouse / "paid_minute_quality" / f"year={year}" \
        / "daily_discrepancies.parquet"
    minute_glob = warehouse / "paid_minute_bars" / f"year={year}" \
        / "shard=*" / "part.parquet"
    d = _sql_path(discrepancy)
    m = _sql_path(minute_glob)
    target_days = con.execute(f"""
        SELECT count(*) FROM read_parquet('{d}')
        WHERE right(symbol, 2)='BJ'
          AND (volume_mismatch OR amount_mismatch_gt_100)
    """).fetchone()[0]
    if not target_days:
        return {
            "target_days": 0,
            "target_days_with_post_close_rows": 0,
            "sum_rows_resolved": 0,
            "last_row_resolved": 0,
            "max_row_resolved": 0,
        }
    return _row(con.execute(f"""
        WITH targets AS (
          SELECT symbol, dt, volume AS regular_volume, amount AS regular_amount,
                 paid_volume_lots*100 AS expected_volume, paid_amount AS expected_amount
          FROM read_parquet('{d}')
          WHERE right(symbol, 2)='BJ'
            AND (volume_mismatch OR amount_mismatch_gt_100)
        ), post_close AS (
          SELECT m.symbol, cast(m.dt AS DATE) AS dt,
                 sum(m.volume) AS sum_volume, sum(m.amount) AS sum_amount,
                 arg_max(m.volume, m.dt) AS last_volume,
                 arg_max(m.amount, m.dt) AS last_amount,
                 max(m.volume) AS max_volume, max(m.amount) AS max_amount
          FROM read_parquet('{m}') m
          JOIN targets t ON m.symbol=t.symbol AND cast(m.dt AS DATE)=t.dt
          WHERE strftime(m.dt, '%H:%M')>'15:00'
          GROUP BY m.symbol, cast(m.dt AS DATE)
        )
        SELECT (SELECT count(*) FROM targets)::BIGINT AS target_days,
          count(*)::BIGINT AS target_days_with_post_close_rows,
          count(*) FILTER (WHERE
            abs(regular_volume+sum_volume-expected_volume)<=0.5 AND
            abs(regular_amount+sum_amount-expected_amount)<=100)::BIGINT
            AS sum_rows_resolved,
          count(*) FILTER (WHERE
            abs(regular_volume+last_volume-expected_volume)<=0.5 AND
            abs(regular_amount+last_amount-expected_amount)<=100)::BIGINT
            AS last_row_resolved,
          count(*) FILTER (WHERE
            abs(regular_volume+max_volume-expected_volume)<=0.5 AND
            abs(regular_amount+max_amount-expected_amount)<=100)::BIGINT
            AS max_row_resolved
        FROM post_close JOIN targets USING(symbol, dt)
    """))


def _qmt_daily_overlap(con: duckdb.DuckDBPyConnection, warehouse: Path,
                       year: int) -> dict[str, Any] | None:
    """把 QMT 重叠分钟先聚合到日级，区分分钟边界差异与整日源差异。"""
    qmt = warehouse / "minute_bars" / f"year={year}" / "part.parquet"
    paid = warehouse / "paid_minute_quality" / f"year={year}" \
        / "daily_aggregate.parquet"
    independent = warehouse / "tushare_daily" / f"year={year}" / "part.parquet"
    if not qmt.exists():
        return None
    if not paid.exists() or not independent.exists():
        raise ValueError(
            f"{year} 有 QMT 重叠分钟，但缺少付费分钟日聚合或 Tushare 日线"
        )
    q = _sql_path(qmt)
    p = _sql_path(paid)
    t = _sql_path(independent)
    relation = f"""
        WITH qmt AS (
          SELECT symbol, cast(dt AS DATE) AS dt, count(*)::BIGINT AS minute_count,
                 arg_min(open, dt) AS open, max(high) AS high, min(low) AS low,
                 arg_max(close, dt) AS close, sum(volume)*100 AS volume,
                 sum(amount) AS amount
          FROM read_parquet('{q}')
          WHERE strftime(dt, '%H:%M')<='15:00'
          GROUP BY symbol, cast(dt AS DATE)
        ), overlap AS (
          SELECT qmt.*, p.open AS p_open, p.high AS p_high, p.low AS p_low,
                 p.close AS p_close, p.volume AS p_volume, p.amount AS p_amount,
                 t.open AS t_open, t.high AS t_high, t.low AS t_low,
                 t.close AS t_close, t.volume*100 AS t_volume, t.amount AS t_amount
          FROM qmt
          JOIN read_parquet('{p}') p USING(symbol, dt)
          JOIN read_parquet('{t}') t USING(symbol, dt)
        )
    """
    result = _row(con.execute(relation + """
        SELECT count(*)::BIGINT AS stock_days,
          sum(minute_count)::BIGINT AS qmt_rows,
          count(*) FILTER (WHERE
            abs(open-p_open)<=0.005 AND abs(high-p_high)<=0.005 AND
            abs(low-p_low)<=0.005 AND abs(close-p_close)<=0.005
          )::BIGINT AS qmt_paid_ohlc_match_days,
          count(*) FILTER (WHERE abs(volume-p_volume)<=100)::BIGINT
            AS qmt_paid_volume_within_one_lot_days,
          count(*) FILTER (WHERE abs(amount-p_amount)<=100)::BIGINT
            AS qmt_paid_amount_within_100_days,
          count(*) FILTER (WHERE volume>0 AND
            (open<=0 OR high<=0 OR low<=0 OR close<=0 OR amount<=0)
          )::BIGINT AS qmt_invalid_price_or_amount_days,
          count(*) FILTER (WHERE volume>0 AND
            (open<=0 OR high<=0 OR low<=0 OR close<=0 OR amount<=0) AND NOT (
              abs(open-p_open)<=0.005 AND abs(high-p_high)<=0.005 AND
              abs(low-p_low)<=0.005 AND abs(close-p_close)<=0.005)
          )::BIGINT AS qmt_invalid_days_in_ohlc_mismatches,
          count(*) FILTER (WHERE volume>0 AND
            (open<=0 OR high<=0 OR low<=0 OR close<=0 OR amount<=0) AND
            abs(amount-p_amount)>100
          )::BIGINT AS qmt_invalid_days_in_amount_mismatches,
          count(*) FILTER (WHERE NOT (volume>0 AND
            (open<=0 OR high<=0 OR low<=0 OR close<=0 OR amount<=0)) AND NOT (
              abs(open-p_open)<=0.005 AND abs(high-p_high)<=0.005 AND
              abs(low-p_low)<=0.005 AND abs(close-p_close)<=0.005)
          )::BIGINT AS unexplained_ohlc_mismatch_days,
          count(*) FILTER (WHERE NOT (volume>0 AND
            (open<=0 OR high<=0 OR low<=0 OR close<=0 OR amount<=0)) AND
            abs(amount-p_amount)>100
          )::BIGINT AS unexplained_amount_mismatch_days,
          count(*) FILTER (WHERE NOT (volume>0 AND
            (open<=0 OR high<=0 OR low<=0 OR close<=0 OR amount<=0)) AND NOT (
              abs(open-p_open)<=0.005 AND abs(high-p_high)<=0.005 AND
              abs(low-p_low)<=0.005 AND abs(close-p_close)<=0.005) AND
            abs(open-t_open)<=0.005 AND abs(high-t_high)<=0.005 AND
            abs(low-t_low)<=0.005 AND abs(close-t_close)<=0.005
          )::BIGINT AS residual_ohlc_qmt_matches_independent,
          count(*) FILTER (WHERE NOT (volume>0 AND
            (open<=0 OR high<=0 OR low<=0 OR close<=0 OR amount<=0)) AND NOT (
              abs(open-p_open)<=0.005 AND abs(high-p_high)<=0.005 AND
              abs(low-p_low)<=0.005 AND abs(close-p_close)<=0.005) AND
            abs(p_open-t_open)<=0.005 AND abs(p_high-t_high)<=0.005 AND
            abs(p_low-t_low)<=0.005 AND abs(p_close-t_close)<=0.005
          )::BIGINT AS residual_ohlc_paid_matches_independent,
          count(*) FILTER (WHERE NOT (volume>0 AND
            (open<=0 OR high<=0 OR low<=0 OR close<=0 OR amount<=0)) AND
            abs(amount-p_amount)>100 AND abs(amount-t_amount)<=100 AND
            abs(p_amount-t_amount)<=100
          )::BIGINT AS residual_amount_both_within_independent_tolerance,
          count(*) FILTER (WHERE NOT (volume>0 AND
            (open<=0 OR high<=0 OR low<=0 OR close<=0 OR amount<=0)) AND
            abs(amount-p_amount)>100 AND abs(amount-t_amount)>100 AND
            abs(p_amount-t_amount)<=100
          )::BIGINT AS residual_amount_paid_matches_independent,
          count(*) FILTER (WHERE NOT (volume>0 AND
            (open<=0 OR high<=0 OR low<=0 OR close<=0 OR amount<=0)) AND
            abs(amount-p_amount)>100 AND abs(amount-t_amount)<=100 AND
            abs(p_amount-t_amount)>100
          )::BIGINT AS residual_amount_qmt_matches_independent,
          count(*) FILTER (WHERE NOT (volume>0 AND
            (open<=0 OR high<=0 OR low<=0 OR close<=0 OR amount<=0)) AND
            abs(amount-p_amount)>100 AND abs(amount-t_amount)>100 AND
            abs(p_amount-t_amount)>100
          )::BIGINT AS residual_amount_neither_matches_independent,
          count(*) FILTER (WHERE
            abs(open-t_open)<=0.005 AND abs(high-t_high)<=0.005 AND
            abs(low-t_low)<=0.005 AND abs(close-t_close)<=0.005
          )::BIGINT AS qmt_independent_ohlc_match_days,
          count(*) FILTER (WHERE abs(volume-t_volume)<=100)::BIGINT
            AS qmt_independent_volume_within_one_lot_days,
          count(*) FILTER (WHERE abs(amount-t_amount)<=100)::BIGINT
            AS qmt_independent_amount_within_100_days,
          count(*) FILTER (WHERE
            abs(p_open-t_open)<=0.005 AND abs(p_high-t_high)<=0.005 AND
            abs(p_low-t_low)<=0.005 AND abs(p_close-t_close)<=0.005
          )::BIGINT AS paid_independent_ohlc_match_days,
          count(*) FILTER (WHERE abs(p_volume-t_volume)<=100)::BIGINT
            AS paid_independent_volume_within_one_lot_days,
          count(*) FILTER (WHERE abs(p_amount-t_amount)<=100)::BIGINT
            AS paid_independent_amount_within_100_days
        FROM overlap
    """))
    cursor = con.execute(relation + """
        SELECT right(symbol, 2) AS exchange, count(*)::BIGINT AS stock_days,
          count(*) FILTER (WHERE NOT (
            abs(open-p_open)<=0.005 AND abs(high-p_high)<=0.005 AND
            abs(low-p_low)<=0.005 AND abs(close-p_close)<=0.005
          ))::BIGINT AS qmt_paid_ohlc_mismatch_days,
          count(*) FILTER (WHERE abs(volume-p_volume)>100)::BIGINT
            AS qmt_paid_volume_mismatch_days,
          count(*) FILTER (WHERE abs(amount-p_amount)>100)::BIGINT
            AS qmt_paid_amount_mismatch_days
        FROM overlap GROUP BY exchange ORDER BY exchange
    """)
    result["by_exchange"] = [
        dict(zip((column[0] for column in cursor.description), row))
        for row in cursor.fetchall()
    ]
    cursor = con.execute(relation + """
        SELECT symbol, dt, minute_count,
          greatest(abs(open-p_open), abs(high-p_high),
                   abs(low-p_low), abs(close-p_close)) AS max_ohlc_diff,
          abs(volume-p_volume) AS volume_diff_shares,
          abs(amount-p_amount) AS amount_diff_cny
        FROM overlap
        WHERE NOT (abs(open-p_open)<=0.005 AND abs(high-p_high)<=0.005 AND
                   abs(low-p_low)<=0.005 AND abs(close-p_close)<=0.005)
           OR abs(volume-p_volume)>100 OR abs(amount-p_amount)>100
        ORDER BY greatest(
          greatest(abs(open-p_open), abs(high-p_high),
                   abs(low-p_low), abs(close-p_close)),
          abs(amount-p_amount)/10000, abs(volume-p_volume)/10000
        ) DESC, dt, symbol
        LIMIT 20
    """)
    result["largest_residual_samples"] = [
        dict(zip((column[0] for column in cursor.description), row))
        for row in cursor.fetchall()
    ]
    cursor = con.execute(relation + """
        SELECT symbol, dt,
          CASE
            WHEN NOT (abs(open-p_open)<=0.005 AND abs(high-p_high)<=0.005 AND
                      abs(low-p_low)<=0.005 AND abs(close-p_close)<=0.005)
              THEN CASE WHEN abs(open-t_open)<=0.005 AND abs(high-t_high)<=0.005 AND
                             abs(low-t_low)<=0.005 AND abs(close-t_close)<=0.005
                        THEN 'paid_ohlc_deviation' ELSE 'qmt_ohlc_deviation' END
            WHEN abs(amount-t_amount)<=100 AND abs(p_amount-t_amount)<=100
              THEN 'amount_tolerance_triangle'
            WHEN abs(amount-t_amount)>100 AND abs(p_amount-t_amount)<=100
              THEN 'qmt_amount_deviation'
            WHEN abs(amount-t_amount)<=100 AND abs(p_amount-t_amount)>100
              THEN 'paid_amount_deviation'
            ELSE 'amount_neither_matches_independent'
          END AS attribution,
          open AS qmt_open, high AS qmt_high, low AS qmt_low, close AS qmt_close,
          p_open AS paid_open, p_high AS paid_high,
          p_low AS paid_low, p_close AS paid_close,
          t_open AS independent_open, t_high AS independent_high,
          t_low AS independent_low, t_close AS independent_close,
          greatest(abs(open-p_open), abs(high-p_high),
                   abs(low-p_low), abs(close-p_close)) AS max_ohlc_diff,
          abs(amount-p_amount) AS qmt_paid_amount_diff,
          abs(amount-t_amount) AS qmt_independent_amount_diff,
          abs(p_amount-t_amount) AS paid_independent_amount_diff
        FROM overlap
        WHERE NOT (volume>0 AND
          (open<=0 OR high<=0 OR low<=0 OR close<=0 OR amount<=0))
          AND (NOT (abs(open-p_open)<=0.005 AND abs(high-p_high)<=0.005 AND
                    abs(low-p_low)<=0.005 AND abs(close-p_close)<=0.005)
               OR abs(amount-p_amount)>100)
        ORDER BY attribution, dt, symbol
        LIMIT 100
    """)
    result["residual_attribution_samples"] = [
        dict(zip((column[0] for column in cursor.description), row))
        for row in cursor.fetchall()
    ]
    return result


def _override_attribution(warehouse: Path) -> dict[str, Any] | None:
    files = sorted((warehouse / "paid_minute_bars").glob(
        "year=*/date=*/part.parquet"
    ))
    if not files:
        return None
    minutes = pl.concat([pl.read_parquet(path) for path in files], how="vertical_relaxed")
    days = minutes["dt"].dt.date()
    daily_frames = []
    for year in sorted(set(days.dt.year().to_list())):
        path = warehouse / "tushare_daily" / f"year={year}" / "part.parquet"
        if path.exists():
            daily_frames.append(pl.read_parquet(
                path,
                columns=["symbol", "dt", "open", "high", "low", "close",
                         "volume", "amount"],
            ))
    if not daily_frames:
        raise ValueError("付费分钟日期覆盖存在，但缺少重叠的 Tushare 日线")
    symbols = minutes["symbol"].unique()
    daily = pl.concat(daily_frames).filter(
        pl.col("symbol").is_in(symbols.implode())
        & pl.col("dt").is_between(days.min(), days.max())
    )
    comparison = compare_with_daily(minutes, daily)
    aggregate = daily_from_minutes(minutes)
    reference = daily.select(
        "symbol", "dt", *(pl.col(name).alias(f"{name}_d")
                           for name in ("open", "high", "low", "close")),
        (pl.col("volume") * 100).alias("volume_d"),
        pl.col("amount").alias("amount_d"),
    )
    joined = aggregate.join(reference, on=["symbol", "dt"], how="left").join(
        comparison, on=["symbol", "dt"], how="left"
    )
    price_diff = pl.max_horizontal([
        (pl.col(name) - pl.col(f"{name}_d")).abs()
        for name in ("open", "high", "low", "close")
    ])
    volume_diff = (pl.col("volume") - pl.col("volume_d")).abs()
    amount_diff = (pl.col("amount") - pl.col("amount_d")).abs()
    summary = joined.select(
        pl.len().alias("stock_days"),
        pl.col("gross").is_not_null().sum().alias("gross_days"),
        pl.col("minor").sum().alias("minor_days"),
        (price_diff > 0.005).sum().alias("ohlc_difference_days"),
        (volume_diff > 0.5).sum().alias("volume_difference_days"),
        ((volume_diff > 0.5) & (volume_diff <= 100)).sum()
            .alias("volume_rounding_le_one_lot_days"),
        (amount_diff > 100).sum().alias("amount_difference_days"),
    ).row(0, named=True)
    samples = joined.filter(pl.col("gross").is_not_null() | pl.col("minor")).with_columns(
        price_diff.alias("max_ohlc_diff"),
        volume_diff.alias("volume_diff_shares"),
        amount_diff.alias("amount_diff_cny"),
    ).select(
        "symbol", "dt", "gross", "minor", "max_ohlc_diff",
        "volume_diff_shares", "amount_diff_cny",
    ).sort(
        ["max_ohlc_diff", "volume_diff_shares", "amount_diff_cny"],
        descending=True,
    ).head(20).to_dicts()
    summary["largest_samples"] = samples
    summary["material_volume_or_amount_samples"] = joined.filter(
        (volume_diff > 100) | (amount_diff > 100)
    ).with_columns(
        price_diff.alias("max_ohlc_diff"),
        volume_diff.alias("volume_diff_shares"),
        amount_diff.alias("amount_diff_cny"),
    ).select(
        "symbol", "dt", "gross", "minor", "max_ohlc_diff",
        "volume_diff_shares", "amount_diff_cny",
    ).sort(["dt", "symbol"]).to_dicts()
    return summary


def analyze(warehouse: Path, years: list[int], *,
            verify_bj_post_close: bool = False,
            verify_qmt_overlap: bool = False,
            verify_overrides: bool = False) -> dict[str, Any]:
    con = duckdb.connect()
    con.execute("SET memory_limit='3GB'")
    con.execute("SET threads=4")
    reports = []
    for year in years:
        report = _daily_attribution(con, warehouse, year)
        if verify_bj_post_close:
            report["bj_post_close_verification"] = _bj_post_close(
                con, warehouse, year
            )
        if verify_qmt_overlap:
            qmt = _qmt_daily_overlap(con, warehouse, year)
            if qmt is not None:
                report["qmt_daily_overlap"] = qmt
        reports.append(report)
    total_fields = (
        "discrepancy_rows", "ohlc_days", "volume_days", "amount_days",
        "ohlc_paid_daily_matches_independent",
        "volume_paid_daily_matches_independent",
        "volume_rounding_le_one_lot", "volume_small_residual", "volume_material",
        "amount_paid_daily_matches_independent", "amount_bj", "amount_non_bj",
    )
    totals = {field: sum(int(report[field]) for report in reports)
              for field in total_fields}
    result = {
        "analysis": "D-20 paid minute cross-source attribution",
        "warehouse": str(warehouse),
        "generated_at": datetime.now().astimezone().isoformat(),
        "read_only": True,
        "totals": totals,
        "years": reports,
        "conclusions": {
            "independent_daily_source": "Tushare daily",
            "price": "paid minute aggregation differs from two agreeing daily sources",
            "volume_rounding": "absolute difference <= 100 shares",
            "volume_material": "absolute difference > max(1000 shares, 0.1% daily volume)",
            "bj_post_close": (
                "candidate encodings tested by sum, last and max; "
                "zero matches remain unresolved"
            ),
        },
    }
    if verify_overrides:
        override = _override_attribution(warehouse)
        if override is not None:
            result["date_overrides"] = override
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--years", type=int, nargs="+", default=list(range(2020, 2026)))
    parser.add_argument("--verify-bj-post-close", action="store_true")
    parser.add_argument("--verify-qmt-overlap", action="store_true")
    parser.add_argument("--verify-overrides", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = analyze(
        args.warehouse, args.years,
        verify_bj_post_close=args.verify_bj_post_close,
        verify_qmt_overlap=args.verify_qmt_overlap,
        verify_overrides=args.verify_overrides,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
        temporary.write_text(payload)
        os.replace(temporary, args.output)
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
