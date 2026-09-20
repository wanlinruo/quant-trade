"""为规范切换生成可追溯的表级五维健康证据。"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import polars as pl

from qsys.data.catalog.health import (
    HealthDimension,
    HealthEvidence,
    HealthReason,
    HealthStatus,
    check_table_contract,
    dataset_check_frame,
    freshness_evidence,
    policy_for,
    write_dataset_checks,
)
from qsys.data.cutover import CANONICAL_CUTOVER_DATASETS
from qsys.data.pipeline.canonical_daily import daily_reconciliation
from qsys.data.pipeline.canonical_minute import canonical_minute_frame
from qsys.data.pipeline.canonical_reference import (
    Reconciliation,
    calendar_reconciliation,
    name_interval_issues,
    reconcile_frames,
    reference_reconciliation,
)
from qsys.data.pipeline.canonical_state import state_reconciliation
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


_STATUS_ORDER = {
    HealthStatus.GREEN: 0,
    HealthStatus.YELLOW: 1,
    HealthStatus.GRAY: 2,
    HealthStatus.RED: 3,
}


def _combine_evidence(items: list[HealthEvidence]) -> HealthEvidence:
    if not items:
        raise ValueError("健康证据不得为空")
    first = items[0]
    status = max((item.status for item in items), key=_STATUS_ORDER.__getitem__)
    expires = [item.expires_at for item in items if item.expires_at is not None]
    return HealthEvidence(
        first.dimension,
        status,
        first.check_id,
        max(item.observed_at for item in items),
        min(expires) if expires else None,
        HealthReason(
            "combined_evidence",
            f"合并 {len(items)} 个分区；违规 {sum(x.violations for x in items)}",
            first.dimension,
            first.check_id,
        ),
        sum(item.violations for item in items),
        sum(item.checked_rows for item in items),
    )


def _reconciliation_evidence(
    dataset: str,
    result: Reconciliation,
    observed_at: datetime,
    *,
    validity_warning: str | None = None,
) -> tuple[HealthEvidence, HealthEvidence]:
    policy = policy_for(dataset)
    if policy is None:
        raise ValueError(f"{dataset} 没有健康策略")
    expires_at = observed_at + policy.evidence_ttl if policy.evidence_ttl else None
    violations = (
        len(result.left_only) + len(result.right_only) + len(result.mismatched)
    )
    if not result.matches:
        validity = lineage = HealthStatus.RED
    else:
        validity = HealthStatus.YELLOW if validity_warning else HealthStatus.GREEN
        lineage = HealthStatus.GREEN
    comparison = (
        f"source/target={result.left_rows}/{result.right_rows}; "
        f"source_only={len(result.left_only)}; target_only={len(result.right_only)}; "
        f"mismatched={len(result.mismatched)}"
    )
    validity_detail = f"{comparison}; {validity_warning}" if validity_warning else comparison
    return (
        HealthEvidence(
            HealthDimension.VALIDITY,
            validity,
            "C1.canonical_recompute",
            observed_at,
            expires_at,
            HealthReason(
                "canonical_recompute",
                validity_detail,
                HealthDimension.VALIDITY,
                "C1.canonical_recompute",
            ),
            violations,
            result.right_rows,
        ),
        HealthEvidence(
            HealthDimension.LINEAGE,
            lineage,
            "C2.source_target_reconciliation",
            observed_at,
            expires_at,
            HealthReason(
                "source_target_reconciliation",
                comparison,
                HealthDimension.LINEAGE,
                "C2.source_target_reconciliation",
            ),
            violations,
            result.right_rows,
        ),
    )


def _published_at(root: Path, dataset: str) -> datetime | None:
    files = tuple((root / dataset).rglob("*.parquet"))
    if not files:
        return None
    return datetime.fromtimestamp(max(path.stat().st_mtime for path in files))


def _round_float_columns(frame: pl.DataFrame, precision: int = 12) -> pl.DataFrame:
    return frame.with_columns(
        pl.col(column).round(precision)
        for column, dtype in frame.schema.items()
        if dtype.is_float()
    )


def _target_frame(
    query: WarehouseQuery, dataset: str, start: date, end: date
) -> pl.DataFrame:
    readers = {
        "security_master": lambda: query.security_master(),
        "security_name_history": lambda: query.security_name_history(),
        "trading_calendar": lambda: query.trading_calendar(start, end),
        "bar_daily": lambda: query.bar_daily(None, start, end),
        "valuation_daily": lambda: query.valuation_daily(None, start, end),
        "share_capital_history": lambda: query.share_capital_history(),
        "adjustment_factor_daily": lambda: query.adjustment_factor_daily(None, start, end),
        "price_limit_daily": lambda: query.price_limit_daily(None, start, end),
        "security_status_daily": lambda: query.security_status_daily(None, start, end),
        "market_flags_daily": lambda: query.market_flags_daily(None, start, end),
        "market_regime_daily": lambda: query.market_regime_daily(start, end),
    }
    return readers[dataset]()


def generate_cutover_health(
    warehouse: Warehouse,
    start: date,
    end: date,
    raw_calendar: pl.DataFrame,
    *,
    market: str = "SSE",
    now: datetime | None = None,
) -> dict[str, HealthStatus]:
    """重算并持久化阶段 11 所需的 12 张规范表健康证据。"""
    if start > end:
        raise ValueError("start must not be after end")
    now = now or datetime.now()
    query = WarehouseQuery(warehouse)
    reconciliations = {
        **reference_reconciliation(warehouse),
        **daily_reconciliation(warehouse, start, end),
        **state_reconciliation(warehouse, start, end),
    }
    reconciliations["market_regime_daily"] = reconcile_frames(
        _round_float_columns(query.market_regime(start, end)),
        _round_float_columns(query.market_regime_daily(start, end)),
        ("dt",),
    )
    reconciliations["trading_calendar"] = calendar_reconciliation(
        raw_calendar,
        query.trading_calendar(start, end, market=market),
        market=market,
    )

    frames: list[pl.DataFrame] = []
    summary: dict[str, HealthStatus] = {}
    run_id = f"cutover-{now:%Y%m%dT%H%M%S%f}"
    for dataset in CANONICAL_CUTOVER_DATASETS:
        if dataset == "bar_minute_1m":
            continue
        target = _target_frame(query, dataset, start, end)
        warning = None
        if dataset == "security_name_history":
            issues = name_interval_issues(target)
            if issues:
                warning = f"名称区间已知问题 {len(issues)} 个，未自动填补"
        evidence = (
            *check_table_contract(dataset, target, now),
            freshness_evidence(dataset, _published_at(warehouse.root, dataset), now),
            *_reconciliation_evidence(
                dataset, reconciliations[dataset], now,
                validity_warning=warning,
            ),
        )
        frames.append(dataset_check_frame(
            run_id, now, dataset, evidence, scope_start=start, scope_end=end
        ))
        summary[dataset] = max(
            (item.status for item in evidence), key=_STATUS_ORDER.__getitem__
        )

    minute_contract: list[HealthEvidence] = []
    minute_diffs: list[Reconciliation] = []
    nonstandard = 0
    calendar = query.trading_calendar(start, end, market=market, open_only=True)
    for day in calendar["dt"].to_list():
        lo = datetime.combine(day, datetime.min.time())
        hi = datetime.combine(day, datetime.max.time())
        source = canonical_minute_frame(warehouse, day)
        target = query.bar_minute_1m(None, lo, hi)
        minute_diffs.append(reconcile_frames(source, target, ("symbol", "ts")))
        minute_contract.extend(check_table_contract("bar_minute_1m", target, now))
        nonstandard += target.group_by("symbol").len().filter(
            ~pl.col("len").is_in([240, 241])
        ).height
    by_dimension = {
        dimension: _combine_evidence([
            item for item in minute_contract if item.dimension is dimension
        ])
        for dimension in (HealthDimension.STRUCTURE, HealthDimension.COMPLETENESS)
    }
    minute_diff = Reconciliation(
        sum(item.left_rows for item in minute_diffs),
        sum(item.right_rows for item in minute_diffs),
        tuple(key for item in minute_diffs for key in item.left_only),
        tuple(key for item in minute_diffs for key in item.right_only),
        tuple(key for item in minute_diffs for key in item.mismatched),
    )
    warning = f"非 240/241 根的标的日 {nonstandard} 个，需保留审阅" if nonstandard else None
    minute_evidence = (
        by_dimension[HealthDimension.STRUCTURE],
        by_dimension[HealthDimension.COMPLETENESS],
        freshness_evidence(
            "bar_minute_1m", _published_at(warehouse.root, "bar_minute_1m"), now
        ),
        *_reconciliation_evidence(
            "bar_minute_1m", minute_diff, now, validity_warning=warning
        ),
    )
    frames.append(dataset_check_frame(
        run_id, now, "bar_minute_1m", minute_evidence,
        scope_start=start, scope_end=end,
    ))
    summary["bar_minute_1m"] = max(
        (item.status for item in minute_evidence), key=_STATUS_ORDER.__getitem__
    )
    write_dataset_checks(warehouse, pl.concat(frames, how="vertical"))
    return summary
