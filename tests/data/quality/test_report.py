from datetime import date

import pyarrow as pa
import pytest

from qsys.core.calendar import AShareCalendar
from qsys.data import schema
from qsys.data.quality.report import (
    DataQualityError,
    QualityReport,
    assert_usable,
    run_quality_checks,
)
from qsys.data.quality.rules import Severity, Violation
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

SESSIONS = [date(2024, 1, 2), date(2024, 1, 3)]
CAL = AShareCalendar(SESSIONS)


def _seed(warehouse: Warehouse, *, complete: bool = True) -> None:
    days = SESSIONS if complete else SESSIONS[:1]
    n = len(days)
    warehouse.write(
        "bars_daily",
        pa.table(
            {
                "symbol": ["600036.SH"] * n,
                "dt": days,
                "open": [10.0] * n,
                "high": [10.5] * n,
                "low": [9.8] * n,
                "close": [10.2] * n,
                "volume": [1000] * n,
                "amount": [10200.0] * n,
            },
            schema=schema.BARS_DAILY,
        ),
        year=2024,
    )
    warehouse.write(
        "instruments",
        pa.table(
            {
                "symbol": ["600036.SH"],
                "name": ["招商银行"],
                "market": ["SH"],
                "list_date": [date(2002, 4, 9)],
                "delist_date": [None],
                "board": ["main"],
            },
            schema=schema.INSTRUMENTS,
        ),
    )


def test_clean_data_produces_no_violations(warehouse: Warehouse):
    _seed(warehouse)
    report = run_quality_checks(
        WarehouseQuery(warehouse), CAL, ["600036.SH"], SESSIONS[0], SESSIONS[-1]
    )
    assert report.violations == ()
    assert report.is_blocked is False


def test_missing_session_blocks(warehouse: Warehouse):
    _seed(warehouse, complete=False)
    report = run_quality_checks(
        WarehouseQuery(warehouse), CAL, ["600036.SH"], SESSIONS[0], SESSIONS[-1]
    )
    assert report.is_blocked is True
    assert [v.rule for v in report.blocking] == ["completeness"]


def test_report_separates_warnings_from_blocking():
    report = QualityReport(
        violations=(
            Violation("completeness", Severity.BLOCKING, "600036.SH", SESSIONS[0], "x"),
            Violation("price_limit", Severity.WARNING, "600036.SH", SESSIONS[1], "y"),
        )
    )
    assert len(report.blocking) == 1
    assert len(report.warnings) == 1
    assert report.is_blocked is True


def test_summary_lists_counts_by_rule():
    report = QualityReport(
        violations=(
            Violation("completeness", Severity.BLOCKING, "600036.SH", SESSIONS[0], "x"),
            Violation("completeness", Severity.BLOCKING, "000001.SZ", SESSIONS[0], "x"),
            Violation("price_limit", Severity.WARNING, "600036.SH", SESSIONS[1], "y"),
        )
    )
    text = report.summary()
    assert "completeness" in text
    assert "2" in text
    assert "price_limit" in text


def test_assert_usable_raises_on_blocking():
    report = QualityReport(
        violations=(Violation("completeness", Severity.BLOCKING, "600036.SH", SESSIONS[0], "x"),)
    )
    with pytest.raises(DataQualityError, match="completeness"):
        assert_usable(report)


def test_assert_usable_passes_on_warnings_only():
    report = QualityReport(
        violations=(Violation("price_limit", Severity.WARNING, "600036.SH", SESSIONS[0], "y"),)
    )
    assert_usable(report)


def test_assert_usable_passes_on_clean_report():
    assert_usable(QualityReport(violations=()))
