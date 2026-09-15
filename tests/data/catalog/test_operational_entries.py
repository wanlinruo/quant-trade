"""运行时类条目。无外部真值不等于可以不说明验证边界。"""

from collections import Counter

from qsys.data.catalog.operational import DATASETS, METRICS
from qsys.data.catalog.types import Computed, Kind, MetricStatus, Uncovered

BY_ID = {m.id: m for m in METRICS}


def test_owns_the_five_runtime_datasets():
    assert DATASETS == frozenset({
        "live_equity",
        "live_fills",
        "live_rejects",
        "live_recon",
        "ingest_log",
    })


def test_registers_all_runtime_columns():
    assert dict(Counter(m.dataset for m in METRICS)) == {
        "ingest_log": 7,
        "live_equity": 4,
        "live_fills": 8,
        "live_recon": 7,
        "live_rejects": 6,
    }
    assert len(METRICS) == 32


def test_all_are_operational_status():
    for metric in METRICS:
        assert metric.status is MetricStatus.OPERATIONAL, metric.id


def test_uncovered_entries_state_a_real_reason():
    for metric in METRICS:
        if isinstance(metric.checks, Uncovered):
            assert len(metric.checks.reason) >= 8, metric.id


def test_ingest_log_ok_column_warns_about_empty_writes():
    assert "空结果" in BY_ID["ingest_log.ok"].meaning


def test_recon_layer_lists_its_three_values():
    meaning = BY_ID["live_recon.layer"].meaning
    assert all(value in meaning for value in ("intraday", "eod", "sim_vs_live"))


def test_total_value_does_not_claim_a_nonexistent_runtime_source_field():
    """SessionJournal 只接 cash/market_value，总资产由两者相加。"""
    metric = BY_ID["live_equity.total_value"]
    assert metric.kind is Kind.DERIVED
    assert metric.lineage == Computed(
        ("live_equity.cash", "live_equity.market_value"),
        "to_cents(cash + market_value)",
    )
