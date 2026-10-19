"""为规范切换生成可追溯的表级五维健康证据。"""

from __future__ import annotations

from datetime import date, datetime
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from qsys.data.catalog.health import (
    HealthDimension,
    HealthEvidence,
    HealthReason,
    HealthStatus,
    check_table_contract,
    check_dependency,
    dataset_check_frame,
    freshness_evidence,
    policy_for,
    relation_check_frame,
    write_dataset_checks,
    write_relation_checks,
)
from qsys.data.catalog import DEPENDENCIES
from qsys.data.catalog.types import DependencyRequirement
from qsys.data.cutover import CANONICAL_CUTOVER_DATASETS
from qsys.data.pipeline.canonical_daily import daily_reconciliation
from qsys.data.pipeline.canonical_minute import canonical_minute_frame
from qsys.data.pipeline.canonical_reference import (
    Reconciliation,
    calendar_reconciliation,
    name_interval_issues,
    reconcile_frames,
    reference_reconciliation,
    require_calendar_coverage,
)
from qsys.data.pipeline.canonical_state import state_reconciliation
from qsys.data.query import WarehouseQuery
from qsys.data.query_budget import connect_warehouse_query
from qsys.data.storage import Warehouse


def refresh_session_health(warehouse: Warehouse, day: date, config: str) -> dict[str, HealthStatus]:
    """Refresh the 12 supported canonical tables for one real trading day.

    Other tables retain missing evidence. This does not publish business data,
    change cutover switches, or extend the existing evidence TTL.
    """
    from qsys.data.providers.tushare import TushareProvider
    from qsys.data.providers.tushare_client import TushareClient
    from qsys.live.config import load_tushare_config

    raw = TushareProvider(TushareClient(load_tushare_config(config).token)).trade_cal(
        day, day, exchange="SSE"
    )
    require_calendar_coverage(raw, day, day, market="SSE")
    # The canonical generator iterates only actual open sessions for minutes.
    from qsys.data.pipeline.canonical_reference import normalize_trading_calendar
    calendar = normalize_trading_calendar(raw, market="SSE")
    if calendar.filter(pl.col("is_open")).height != 1:
        raise ValueError(f"{day} is not a confirmed trading day")
    return generate_cutover_health(warehouse, day, day, raw)


_STATUS_ORDER = {
    HealthStatus.GREEN: 0,
    HealthStatus.YELLOW: 1,
    HealthStatus.GRAY: 2,
    HealthStatus.RED: 3,
}


@dataclass(frozen=True)
class ReconciliationCounts:
    left_rows: int
    right_rows: int
    left_only_count: int
    right_only_count: int
    mismatch_count: int

    @property
    def matches(self) -> bool:
        return not (self.left_only_count or self.right_only_count or self.mismatch_count)


def _minute_reconciliation(source: pl.DataFrame, target: pl.DataFrame) -> ReconciliationCounts:
    """Aggregate one partition with the configured DuckDB spill/memory budget.

    No per-row Python dictionaries or full-history minute temporary tables.
    Null versus a real value is a mismatch, including rows present on one side.
    """
    connection = connect_warehouse_query()
    try:
        connection.register("health_source", source.to_arrow())
        connection.register("health_target", target.to_arrow())
        columns = [column for column in source.columns if column not in ("symbol", "ts")]
        different = " OR ".join(f's."{c}" IS DISTINCT FROM t."{c}"' for c in columns)
        counts = connection.execute(f"""
            SELECT count(*) FILTER (WHERE s.present AND t.present IS NULL),
                   count(*) FILTER (WHERE t.present AND s.present IS NULL),
                   count(*) FILTER (WHERE s.present AND t.present AND ({different}))
            FROM (SELECT *, true AS present FROM health_source) s
            FULL OUTER JOIN (SELECT *, true AS present FROM health_target) t
            ON s.symbol IS NOT DISTINCT FROM t.symbol AND s.ts IS NOT DISTINCT FROM t.ts
        """).fetchone()
        return ReconciliationCounts(source.height, target.height, *counts)
    finally:
        connection.close()


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
    result: Reconciliation | ReconciliationCounts,
    observed_at: datetime,
    *,
    validity_warning: str | None = None,
) -> tuple[HealthEvidence, HealthEvidence]:
    policy = policy_for(dataset)
    if policy is None:
        raise ValueError(f"{dataset} 没有健康策略")
    expires_at = observed_at + policy.evidence_ttl if policy.evidence_ttl else None
    if isinstance(result, ReconciliationCounts):
        left_only, right_only, mismatched = (
            result.left_only_count, result.right_only_count, result.mismatch_count)
    else:
        left_only, right_only, mismatched = (
            len(result.left_only), len(result.right_only), len(result.mismatched))
    violations = left_only + right_only + mismatched
    if not result.matches:
        validity = lineage = HealthStatus.RED
    else:
        validity = HealthStatus.YELLOW if validity_warning else HealthStatus.GREEN
        lineage = HealthStatus.GREEN
    comparison = (
        f"source/target={result.left_rows}/{result.right_rows}; "
        f"source_only={left_only}; target_only={right_only}; "
        f"mismatched={mismatched}"
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


def canonical_required_relations():
    """返回规范表之间、会阻断切换的已登记依赖。"""
    canonical = set(CANONICAL_CUTOVER_DATASETS)
    return tuple(
        dependency for dependency in DEPENDENCIES
        if dependency.upstream in canonical
        and dependency.downstream in canonical
        and dependency.requirement is DependencyRequirement.REQUIRED
    )


def _check_canonical_relation(
    dependency,
    upstream: pl.DataFrame,
    downstream: pl.DataFrame,
    observed_at: datetime,
    *,
    upstream_published_at: datetime | None = None,
    downstream_published_at: datetime | None = None,
):
    # security_status_daily 合法包含没有日线的停牌日。这里要验证的是每根
    # bar 都有状态，而不是每条状态都有 bar；刷新顺序仍按原始血缘方向。
    frames = (
        (downstream, upstream)
        if (dependency.upstream, dependency.downstream)
        == ("bar_daily", "security_status_daily")
        else (upstream, downstream)
    )
    return check_dependency(
        dependency, *frames, observed_at,
        upstream_published_at=upstream_published_at,
        downstream_published_at=downstream_published_at,
    )


def generate_cutover_relations(
    warehouse: Warehouse,
    start: date,
    end: date,
    *,
    now: datetime | None = None,
    run_id: str | None = None,
) -> dict[tuple[str, str], HealthStatus]:
    """重算并持久化规范表之间的 Join、基数和孤儿行证据。"""
    if start > end:
        raise ValueError("start must not be after end")
    now = now or datetime.now()
    run_id = run_id or f"cutover-rel-{now:%Y%m%dT%H%M%S%f}"
    query = WarehouseQuery(warehouse)
    frames: list[pl.DataFrame] = []
    summary: dict[tuple[str, str], HealthStatus] = {}
    cache: dict[str, pl.DataFrame] = {}

    def target(dataset: str) -> pl.DataFrame:
        if dataset not in cache:
            cache[dataset] = _target_frame(query, dataset, start, end)
        return cache[dataset]

    for dependency in canonical_required_relations():
        result = _check_canonical_relation(
            dependency,
            target(dependency.upstream),
            target(dependency.downstream),
            now,
            upstream_published_at=_published_at(warehouse.root, dependency.upstream),
            downstream_published_at=_published_at(warehouse.root, dependency.downstream),
        )
        frames.append(relation_check_frame(
            run_id, now, dependency, result,
            scope_start=start, scope_end=end,
        ))
        summary[(dependency.upstream, dependency.downstream)] = result.evidence.status
    if frames:
        write_relation_checks(warehouse, pl.concat(frames, how="vertical"))
    return summary


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
    minute_diffs: list[ReconciliationCounts] = []
    nonstandard = 0
    calendar = query.trading_calendar(start, end, market=market, open_only=True)
    for day in calendar["dt"].to_list():
        lo = datetime.combine(day, datetime.min.time())
        hi = datetime.combine(day, datetime.max.time())
        source = canonical_minute_frame(warehouse, day)
        target = query.bar_minute_1m(None, lo, hi)
        minute_diffs.append(_minute_reconciliation(source, target))
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
    minute_diff = ReconciliationCounts(
        sum(item.left_rows for item in minute_diffs),
        sum(item.right_rows for item in minute_diffs),
        sum(item.left_only_count for item in minute_diffs),
        sum(item.right_only_count for item in minute_diffs),
        sum(item.mismatch_count for item in minute_diffs),
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
    generate_cutover_relations(
        warehouse, start, end, now=now, run_id=run_id
    )
    return summary
