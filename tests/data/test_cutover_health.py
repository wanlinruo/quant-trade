from datetime import datetime

from qsys.data.catalog.health import HealthStatus
from qsys.data.cutover_health import _combine_evidence, _reconciliation_evidence
from qsys.data.pipeline.canonical_reference import Reconciliation


NOW = datetime(2026, 9, 20, 20)


def test_reconciliation_supplies_validity_and_lineage_without_hiding_warning():
    result = Reconciliation(10, 10, (), (), ())
    validity, lineage = _reconciliation_evidence(
        "security_name_history", result, NOW,
        validity_warning="存在 2 个区间问题",
    )

    assert validity.status is HealthStatus.YELLOW
    assert lineage.status is HealthStatus.GREEN
    assert "2 个区间问题" in validity.reason.message


def test_reconciliation_failure_is_red_for_validity_and_lineage():
    result = Reconciliation(10, 9, (("A",),), (), ())

    evidence = _reconciliation_evidence("bar_daily", result, NOW)

    assert {item.status for item in evidence} == {HealthStatus.RED}
    assert all(item.violations == 1 for item in evidence)


def test_combined_partition_evidence_keeps_worst_status():
    green = _reconciliation_evidence(
        "bar_minute_1m", Reconciliation(10, 10, (), (), ()), NOW
    )[0]
    red = _reconciliation_evidence(
        "bar_minute_1m", Reconciliation(10, 9, (("A",),), (), ()), NOW
    )[0]

    combined = _combine_evidence([green, red])

    assert combined.status is HealthStatus.RED
    assert combined.checked_rows == 19
    assert combined.violations == 1
