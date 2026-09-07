"""质量报告与 blocking 闸门。

校验不过的数据不许进回测——`assert_usable()` 是这条纪律的执行点。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import structlog

from qsys.core.calendar import TradingCalendar
from qsys.data.quality.rules import (
    Severity,
    Violation,
    check_completeness,
    check_internal_consistency,
    check_price_limits,
)
from qsys.data.query import WarehouseQuery

log = structlog.get_logger(__name__)


class DataQualityError(RuntimeError):
    """存在 blocking 级问题，数据不得进入回测可用集。"""


@dataclass(frozen=True, slots=True)
class QualityReport:
    violations: tuple[Violation, ...]

    @property
    def blocking(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity is Severity.BLOCKING)

    @property
    def warnings(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity is Severity.WARNING)

    @property
    def is_blocked(self) -> bool:
        return len(self.blocking) > 0

    def summary(self) -> str:
        if not self.violations:
            return "quality: clean (0 violations)"
        counts = Counter((v.rule, v.severity.value) for v in self.violations)
        lines = [f"quality: {len(self.blocking)} blocking, {len(self.warnings)} warning"]
        for (rule, severity), n in sorted(counts.items()):
            lines.append(f"  {rule} [{severity}]: {n}")
        return "\n".join(lines)


def run_quality_checks(
    query: WarehouseQuery,
    calendar: TradingCalendar,
    expected_symbols: Sequence[str],
    start: date,
    end: date,
) -> QualityReport:
    bars = query.daily_bars(list(expected_symbols), start, end)
    instruments = query.instruments()
    boards = dict(zip(instruments["symbol"].to_list(), instruments["board"].to_list()))

    violations = [
        *check_completeness(bars, calendar, expected_symbols, start, end),
        *check_price_limits(bars, boards),
        *check_internal_consistency(bars),
    ]
    report = QualityReport(violations=tuple(violations))
    log.info(
        "quality.checked",
        start=str(start),
        end=str(end),
        blocking=len(report.blocking),
        warnings=len(report.warnings),
    )
    return report


def assert_usable(report: QualityReport) -> None:
    if report.is_blocked:
        rules = sorted({v.rule for v in report.blocking})
        raise DataQualityError(
            f"{len(report.blocking)} blocking violations: {', '.join(rules)}"
        )
