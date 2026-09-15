"""验证规则的注册表与 runner。

三条硬纪律：规则崩溃也写失败行；checked_rows=0 不算通过；只有规则与指标
共同登记同一已知缺陷时才把失败降为 warning。
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.catalog import REGISTRY
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)
_ARTIFACTS = Path("artifacts/metric_checks")


class Category(StrEnum):
    TRUTHFULNESS = "truthfulness"
    COMPLETENESS = "completeness"
    VALIDITY = "validity"


class Scope(StrEnum):
    INCREMENTAL = "incremental"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class CheckContext:
    query: WarehouseQuery
    warehouse: Warehouse
    start: date
    end: date


@dataclass(frozen=True, slots=True)
class CheckResult:
    violations: int
    checked_rows: int
    detail: str = ""
    sample: pl.DataFrame | None = None
    applicable: bool = True


@dataclass(frozen=True, slots=True)
class Check:
    id: str
    category: Category
    scope: Scope
    metrics: tuple[str, ...]
    known_issues: tuple[str, ...]
    description: str
    run: Callable[[CheckContext], CheckResult]


CHECKS: dict[str, Check] = {}


def register(check: Check) -> Check:
    if check.id in CHECKS:
        raise ValueError(f"重复的规则 id：{check.id}")
    if not check.metrics:
        raise ValueError(f"{check.id} 没有声明覆盖指标")
    unknown = [metric for metric in check.metrics if metric not in REGISTRY]
    if unknown:
        raise ValueError(f"{check.id} 声称覆盖未登记的指标：{unknown}")
    CHECKS[check.id] = check
    return check


def _severity_and_issue(
    check: Check, metric_id: str, passed: bool
) -> tuple[str, str | None]:
    if passed:
        return "warning", None
    registered = set(REGISTRY[metric_id].known_issues)
    matches = sorted(set(check.known_issues) & registered)
    return ("warning", matches[0]) if matches else ("blocking", None)


def run_checks(
    ctx: CheckContext,
    run_id: str,
    now: datetime,
    *,
    scope: Scope | None = None,
    only: Sequence[Check] | None = None,
    artifacts: Path | None = None,
) -> pl.DataFrame:
    """执行规则并返回严格符合 METRIC_CHECKS 的 DataFrame，尚不落盘。"""
    selected = list(only) if only is not None else [
        check for check in CHECKS.values()
        if scope is None or check.scope is scope
    ]
    root = Path(artifacts) if artifacts is not None else _ARTIFACTS
    rows: list[dict[str, object]] = []

    for check in selected:
        sample_path: str | None = None
        try:
            result = check.run(ctx)
        except Exception as exc:  # 闸门自身失效也必须可见
            log.warning("check.crashed", check=check.id, error=str(exc))
            result = CheckResult(
                violations=0,
                checked_rows=0,
                detail=f"runner error: {type(exc).__name__}: {exc}",
            )
            passed = False
        else:
            if not result.applicable:
                passed = True
                result = CheckResult(
                    result.violations,
                    result.checked_rows,
                    (result.detail + " | " if result.detail else "") + "本次范围不适用",
                    result.sample,
                    applicable=False,
                )
            elif result.checked_rows <= 0:
                passed = False
                result = CheckResult(
                    result.violations,
                    result.checked_rows,
                    (result.detail + " | " if result.detail else "")
                    + "没有可检查的行（分母为 0，不算通过）",
                    result.sample,
                )
            else:
                passed = result.violations == 0

        if result.sample is not None and result.sample.height:
            path = root / run_id / f"{check.id}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            result.sample.write_parquet(path)
            sample_path = str(path)

        for metric_id in check.metrics:
            severity, issue = _severity_and_issue(check, metric_id, passed)
            rows.append({
                "run_id": run_id,
                "checked_at": now,
                "metric_id": metric_id,
                "check_id": check.id,
                "category": check.category.value,
                "scope_start": ctx.start,
                "scope_end": ctx.end,
                "passed": passed,
                "severity": severity,
                "known_issue": issue,
                "violations": result.violations,
                "checked_rows": result.checked_rows,
                "detail": result.detail,
                "sample_path": sample_path,
            })

    if not rows:
        return pl.from_arrow(schema.METRIC_CHECKS.empty_table())
    return pl.from_arrow(
        pl.DataFrame(rows).select(schema.METRIC_CHECKS.names)
        .to_arrow().cast(schema.METRIC_CHECKS)
    )


def write_results(warehouse: Warehouse, frame: pl.DataFrame) -> int:
    """按 checked_at 年份纯追加；同一指标/规则的历史结果不覆盖。"""
    if frame.height == 0:
        return 0
    query = WarehouseQuery(warehouse)
    written = 0
    years = sorted(set(frame["checked_at"].dt.year().to_list()))
    for year in years:
        chunk = frame.filter(pl.col("checked_at").dt.year() == year)
        existing = query.metric_checks().filter(
            pl.col("checked_at").dt.year() == year
        )
        merged = pl.concat([existing, chunk], how="vertical")
        warehouse.write(
            "metric_checks",
            merged.select(schema.METRIC_CHECKS.names)
            .to_arrow().cast(schema.METRIC_CHECKS),
            year=year,
        )
        written += chunk.height
    return written


def load_builtin_checks() -> None:
    """显式加载内建规则，避免注册表依赖 pytest 的文件收集顺序。"""
    for module in (
        "qsys.data.catalog.rules_truth",
        "qsys.data.catalog.rules_recompute",
        "qsys.data.catalog.rules_complete",
        "qsys.data.catalog.rules_valid",
    ):
        importlib.import_module(module)


load_builtin_checks()
