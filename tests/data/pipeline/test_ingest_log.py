from datetime import date

from qsys.data.pipeline.ingest_log import IngestLog
from qsys.data.storage import Warehouse


def test_record_appends_entry(warehouse: Warehouse):
    log = IngestLog(warehouse)
    log.record("bars_daily", date(2024, 1, 2), "mootdx", 5000, True)
    entries = log.entries(2024)
    assert entries.height == 1
    assert entries["dataset"].to_list() == ["bars_daily"]
    assert entries["row_count"].to_list() == [5000]
    assert entries["ok"].to_list() == [True]


def test_record_is_idempotent_on_same_key(warehouse: Warehouse):
    log = IngestLog(warehouse)
    log.record("bars_daily", date(2024, 1, 2), "mootdx", 5000, True)
    log.record("bars_daily", date(2024, 1, 2), "mootdx", 5100, True)
    entries = log.entries(2024)
    assert entries.height == 1
    assert entries["row_count"].to_list() == [5100]


def test_different_providers_are_separate_entries(warehouse: Warehouse):
    log = IngestLog(warehouse)
    log.record("bars_daily", date(2024, 1, 2), "mootdx", 5000, True)
    log.record("bars_daily", date(2024, 1, 2), "akshare", 4900, True)
    assert log.entries(2024).height == 2


def test_failure_is_recorded_with_message(warehouse: Warehouse):
    log = IngestLog(warehouse)
    log.record("bars_daily", date(2024, 1, 2), "mootdx", 0, False, "connection reset")
    entries = log.entries(2024)
    assert entries["ok"].to_list() == [False]
    assert entries["message"].to_list() == ["connection reset"]


def test_entries_on_empty_year_returns_empty_frame(warehouse: Warehouse):
    assert IngestLog(warehouse).entries(2024).height == 0


def test_entries_are_partitioned_by_year(warehouse: Warehouse):
    log = IngestLog(warehouse)
    log.record("bars_daily", date(2023, 12, 29), "mootdx", 1, True)
    log.record("bars_daily", date(2024, 1, 2), "mootdx", 2, True)
    assert log.entries(2023).height == 1
    assert log.entries(2024).height == 1
