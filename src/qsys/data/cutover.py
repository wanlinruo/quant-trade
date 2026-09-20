"""规范行情门面切换开关与阶段 11 只读门禁。

门禁只生成证据，不切换服务、不停止任务、不修改仓库。生产消费者只有显式
选择 ``canonical`` 才会读取新表；两种模式都禁止静默回退。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import StrEnum
import os
import re
from typing import Sequence

import polars as pl

from qsys.data import schema
from qsys.data.access import facade_reconciliation
from qsys.data.catalog.health import (
    HealthDimension,
    HealthEvidence,
    HealthReason,
    HealthStatus,
    aggregate_health,
    policy_for,
)
from qsys.data.query import WarehouseQuery


class FacadeMode(StrEnum):
    LEGACY = "legacy"
    CANONICAL = "canonical"


@dataclass(frozen=True, slots=True)
class CutoverConfig:
    mode: FacadeMode = FacadeMode.LEGACY

    @classmethod
    def from_env(cls, name: str = "QSYS_MARKET_DATA_FACADE") -> "CutoverConfig":
        raw = os.environ.get(name, FacadeMode.LEGACY.value)
        try:
            return cls(FacadeMode(raw))
        except ValueError as exc:
            raise ValueError(
                f"{name} 只能是 legacy 或 canonical，实际为 {raw!r}"
            ) from exc


def add_facade_argument(parser) -> None:
    """为入口脚本登记显式读取门面覆盖；未传时继续读取统一环境变量。"""
    parser.add_argument(
        "--market-data-facade",
        choices=tuple(mode.value for mode in FacadeMode),
        default=None,
        help="行情读取门面；未传时读取 QSYS_MARKET_DATA_FACADE",
    )


def apply_facade_argument(value: str | None) -> CutoverConfig:
    """应用 CLI 覆盖并返回最终配置，非法环境值仍失败关闭。"""
    if value is not None:
        os.environ["QSYS_MARKET_DATA_FACADE"] = FacadeMode(value).value
    return CutoverConfig.from_env()


class CutoverMarketData:
    """保持旧八列契约的显式双路门面；默认路径永远是 legacy。"""

    def __init__(self, query: WarehouseQuery, config: CutoverConfig) -> None:
        self._query = query
        self.config = config

    def daily_bars(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        if self.config.mode is FacadeMode.LEGACY:
            return self._query.daily_bars(symbols, start, end)
        return self._query.historical_daily_bars(
            symbols, start, end, columns=schema.DAILY_BARS.names
        )

    def minute_bars(
        self, symbols: Sequence[str] | None, start: datetime, end: datetime
    ) -> pl.DataFrame:
        if self.config.mode is FacadeMode.LEGACY:
            # 规范表允许外部源出现小数手数。双路门面必须向消费者提供同一
            # 物理类型，否则切换前后的策略输入和性能比较都不是同一契约。
            return self._query.minute_bars(symbols, start, end).with_columns(
                pl.col("volume").cast(pl.Float64)
            )
        return (
            self._query.historical_minute_bars(
                symbols, start, end,
                columns=("symbol", "ts", "open", "high", "low", "close",
                         "volume", "amount"),
            )
            .rename({"ts": "dt"})
        )

    def adjustment_factors(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        if self.config.mode is FacadeMode.LEGACY:
            return self._query.adj_factors(symbols).filter(
                pl.col("dt").is_between(start, end)
            )
        return self._query.adjustment_factor_daily(symbols, start, end).select(
            "symbol", "dt", pl.col("price_factor").alias("cum_factor"),
            pl.col("share_factor").alias("cum_share_factor"),
        )

    def daily_flags(self, start: date, end: date) -> pl.DataFrame:
        if self.config.mode is FacadeMode.LEGACY:
            return self._query.daily_flags(start, end)
        return self._query.market_flags_daily(None, start, end)

    def market_regime(
        self, start: date | None = None, end: date | None = None
    ) -> pl.DataFrame:
        if self.config.mode is FacadeMode.LEGACY:
            return self._query.market_regime(start, end)
        return self._query.market_regime_daily(start, end)

    def instruments(self) -> pl.DataFrame:
        if self.config.mode is FacadeMode.LEGACY:
            return self._query.instruments()
        return self._query.security_master()

    def valuation(self, start: date, end: date) -> pl.DataFrame:
        if self.config.mode is FacadeMode.LEGACY:
            return self._query.valuation(start, end)
        return self._query.valuation_daily(None, start, end).select(
            "symbol", "dt",
            (pl.col("market_cap") / 1e8).alias("market_cap"),
            (pl.col("circulating_market_cap") / 1e8)
            .alias("circulating_market_cap"),
            "turnover_ratio",
        ).join(
            self._query.bar_daily(
                None, start, end, columns=("symbol", "dt", "close")
            ),
            on=["symbol", "dt"], how="left",
        ).select(schema.VALUATION_SNAPSHOT.names)

    def price_limits(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        if self.config.mode is FacadeMode.LEGACY:
            return self._query.price_limits(symbols, start, end)
        return self._query.price_limit_daily(symbols, start, end)

    def suspended_symbols(self, symbols: Sequence[str], day: date) -> set[str]:
        if self.config.mode is FacadeMode.LEGACY:
            rows = self._query.suspensions(symbols, day, day)
            return set(rows.filter(
                pl.col("suspend_type") == "S"
            )["symbol"].to_list())
        rows = self._query.security_status_daily(symbols, day, day)
        return set(rows.filter(
            pl.col("is_suspended") == True  # noqa: E712
        )["symbol"].to_list())

    def intraday_bars(
        self, symbols: Sequence[str], day: date
    ) -> pl.DataFrame:
        if self.config.mode is FacadeMode.LEGACY:
            return self._query.intraday_bars(symbols, day)
        lo = datetime.combine(day, datetime.min.time())
        hi = datetime.combine(day, datetime.max.time())
        return self._query.bar_minute_1m(symbols, lo, hi).rename({"ts": "dt"})


@dataclass(frozen=True, slots=True)
class BenchmarkComparison:
    name: str
    legacy_ms: float
    canonical_ms: float
    legacy_scanned_files: int
    canonical_scanned_files: int
    legacy_scanned_columns: int
    canonical_scanned_columns: int
    legacy_peak_mb: float
    canonical_peak_mb: float
    max_regression_ratio: float = 1.0
    legacy_rows: int = 0
    canonical_rows: int = 0

    @property
    def passed(self) -> bool:
        return (
            self.legacy_ms >= 0
            and self.canonical_ms >= 0
            and self.legacy_scanned_files >= 0
            and self.canonical_scanned_files >= 0
            and self.legacy_scanned_columns >= 0
            and self.canonical_scanned_columns >= 0
            and self.legacy_peak_mb >= 0
            and self.canonical_peak_mb >= 0
            and self.max_regression_ratio > 0
            and self.legacy_rows > 0
            and self.canonical_rows == self.legacy_rows
            and self.canonical_ms <= self.legacy_ms * self.max_regression_ratio
            and self.canonical_scanned_files <= self.legacy_scanned_files
            and self.canonical_scanned_columns <= self.legacy_scanned_columns
            and self.canonical_peak_mb
            <= self.legacy_peak_mb * self.max_regression_ratio
        )


@dataclass(frozen=True, slots=True)
class StrategyComparison:
    matches: bool
    compared_cases: int
    legacy_digest: str
    canonical_digest: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class GateCheck:
    id: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class CutoverAudit:
    commit_sha: str
    start: date
    end: date
    checks: tuple[GateCheck, ...]

    @property
    def ready(self) -> bool:
        return all(check.passed for check in self.checks)

    def to_dict(self) -> dict:
        return {
            "commit_sha": self.commit_sha,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "ready": self.ready,
            "checks": [asdict(check) for check in self.checks],
        }


CANONICAL_CUTOVER_DATASETS = (
    "security_master", "security_name_history", "trading_calendar",
    "bar_daily", "valuation_daily", "share_capital_history",
    "adjustment_factor_daily", "price_limit_daily", "security_status_daily",
    "market_flags_daily", "market_regime_daily", "bar_minute_1m",
)


def _latest(rows: list[dict]) -> list[dict]:
    out: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["dimension"], row["check_id"])
        if key not in out or row["checked_at"] > out[key]["checked_at"]:
            out[key] = row
    return list(out.values())


def _dataset_health(query: WarehouseQuery, dataset: str, now: datetime):
    rows = _latest(query.dataset_checks(dataset).to_dicts())
    evidence = tuple(
        HealthEvidence(
            HealthDimension(row["dimension"]), HealthStatus(row["status"]),
            row["check_id"], row["evidence_at"], row["expires_at"],
            HealthReason(
                "persisted_result", row["detail"],
                HealthDimension(row["dimension"]), row["check_id"],
            ),
            row["violations"], row["checked_rows"],
        )
        for row in rows
    )
    return aggregate_health(policy_for(dataset), evidence, now)


def _required_relation_health(
    query: WarehouseQuery,
    start: date,
    end: date,
    required_datasets: tuple[str, ...],
    now: datetime,
) -> tuple[bool, str]:
    # 延迟导入避免 catalog 聚合入口与 cutover 模块形成初始化环。
    from qsys.data.cutover_health import canonical_required_relations

    required = set(required_datasets)
    expected = tuple(
        edge for edge in canonical_required_relations()
        if edge.upstream in required and edge.downstream in required
    )
    if not expected:
        return True, "当前必需数据集之间没有登记的阻断关系"

    failures: list[str] = []
    for edge in expected:
        rows = query.relation_checks(edge.upstream, edge.downstream).filter(
            (pl.col("scope_start") == start) & (pl.col("scope_end") == end)
        )
        if rows.height == 0:
            failures.append(f"{edge.upstream}->{edge.downstream}=missing")
            continue
        latest = rows.sort("checked_at", descending=True).row(0, named=True)
        expired = latest["expires_at"] is not None and latest["expires_at"] < now
        if latest["status"] in (HealthStatus.GRAY.value, HealthStatus.RED.value) or expired:
            suffix = "/expired" if expired else ""
            failures.append(
                f"{edge.upstream}->{edge.downstream}={latest['status']}{suffix}"
            )
    return (
        not failures,
        f"{len(expected)} 条必需关系证据有效" if not failures else "; ".join(failures),
    )


def audit_cutover(
    query: WarehouseQuery,
    start: date,
    end: date,
    *,
    commit_sha: str,
    release_clean: bool,
    performance: tuple[BenchmarkComparison, ...] | None,
    strategy: StrategyComparison | None,
    required_datasets: tuple[str, ...] = CANONICAL_CUTOVER_DATASETS,
    min_cycle_sessions: int = 5,
    now: datetime | None = None,
) -> CutoverAudit:
    """逐项验证切换条件；失败只形成报告，不产生任何外部状态变更。"""
    if start > end:
        raise ValueError("start must not be after end")
    now = now or datetime.now()
    checks: list[GateCheck] = []

    revision_ok = bool(re.fullmatch(r"[0-9a-f]{40}", commit_sha)) and release_clean
    checks.append(GateCheck(
        "release_revision", revision_ok,
        f"commit={commit_sha}; worktree={'clean' if release_clean else 'dirty'}",
    ))

    reconciled = facade_reconciliation(query, start, end)
    daily = reconciled["daily"]
    minute = reconciled["minute"]
    nonempty = daily.left_rows > 0 and minute.left_rows > 0
    checks.append(GateCheck(
        "canonical_backfill", nonempty and daily.right_rows > 0 and minute.right_rows > 0,
        f"daily old/new={daily.left_rows}/{daily.right_rows}; "
        f"minute old/new={minute.left_rows}/{minute.right_rows}",
    ))
    checks.append(GateCheck(
        "facade_reconciliation", nonempty and daily.matches and minute.matches,
        f"daily matches={daily.matches}, left_only={len(daily.left_only)}, "
        f"right_only={len(daily.right_only)}, mismatched={len(daily.mismatched)}; "
        f"minute matches={minute.matches}, left_only={len(minute.left_only)}, "
        f"right_only={len(minute.right_only)}, mismatched={len(minute.mismatched)}",
    ))

    unhealthy: list[str] = []
    for dataset in required_datasets:
        assessment = _dataset_health(query, dataset, now)
        states = {state for _, state in assessment.dimensions}
        if (
            not assessment.dimensions
            or HealthStatus.GRAY in states
            or HealthStatus.RED in states
        ):
            unhealthy.append(f"{dataset}={assessment.status.value}")
    checks.append(GateCheck(
        "required_health", not unhealthy,
        "全部必需维度非灰且无红" if not unhealthy else "; ".join(unhealthy),
    ))

    relations_ok, relations_detail = _required_relation_health(
        query, start, end, required_datasets, now
    )
    checks.append(GateCheck(
        "required_relations", relations_ok, relations_detail
    ))

    performance_ok = bool(performance) and all(item.passed for item in performance)
    performance_detail = "缺少性能基准证据" if not performance else "; ".join(
        f"{item.name}={item.canonical_ms:.3f}/{item.legacy_ms:.3f}ms "
        f"rows={item.canonical_rows}/{item.legacy_rows}, "
        f"files={item.canonical_scanned_files}/{item.legacy_scanned_files}, "
        f"columns={item.canonical_scanned_columns}/{item.legacy_scanned_columns}, "
        f"peak={item.canonical_peak_mb:.1f}/{item.legacy_peak_mb:.1f}MB "
        f"(limit {item.max_regression_ratio:.2f}x)"
        for item in performance
    )
    checks.append(GateCheck("performance", performance_ok, performance_detail))

    strategy_ok = bool(
        strategy and strategy.matches and strategy.compared_cases > 0
        and bool(strategy.legacy_digest)
        and strategy.legacy_digest == strategy.canonical_digest
    )
    checks.append(GateCheck(
        "strategy_inputs", strategy_ok,
        (
            f"cases={strategy.compared_cases}; "
            f"legacy={strategy.legacy_digest}; canonical={strategy.canonical_digest}; "
            f"{strategy.detail}"
        ) if strategy else "缺少策略输入对账证据",
    ))

    lo = datetime.combine(start, datetime.min.time())
    hi = datetime.combine(end, datetime.max.time())
    daily_days = set(query.bar_daily(None, start, end)["dt"].unique().to_list())
    minute_frame = query.bar_minute_1m(None, lo, hi)
    minute_days = set(minute_frame["ts"].dt.date().unique().to_list())
    calendar = query.trading_calendar(start, end, open_only=True)
    expected = set(calendar["dt"].to_list()) if calendar.height else daily_days
    complete_days = expected & daily_days & minute_days
    cycle_ok = len(complete_days) >= min_cycle_sessions and expected <= complete_days
    checks.append(GateCheck(
        "observation_cycle", cycle_ok,
        f"完整交易日={len(complete_days)}; 需要>={min_cycle_sessions}; "
        f"日历交易日={len(expected)}",
    ))

    rollback_ok = daily.left_rows > 0 and minute.left_rows > 0
    checks.append(GateCheck(
        "rollback_path", rollback_ok,
        "legacy/canonical 显式双路可选，旧日线和分钟数据可读"
        if rollback_ok else "旧日线或分钟数据不可用，不能保证回滚",
    ))
    return CutoverAudit(commit_sha, start, end, tuple(checks))
