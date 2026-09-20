from datetime import date

import polars as pl
import pyarrow.parquet as pq
import pytest

from qsys.data import schema
from qsys.data.pipeline.canonical_reference import (
    build_security_master,
    build_security_name_history,
    calendar_reconciliation,
    name_interval_issues,
    normalize_trading_calendar,
    publish_trading_calendar,
    reference_reconciliation,
    reference_preflight,
    require_reference_sources,
    require_calendar_coverage,
)
from qsys.data.query import WarehouseQuery


D1, D2, D3 = date(2026, 9, 18), date(2026, 9, 19), date(2026, 9, 20)


def _seed_reference(warehouse):
    instruments = pl.DataFrame([{
        "symbol": "000001.SZ", "name": "平安银行", "market": "SZ",
        "list_date": date(1991, 4, 3), "delist_date": None,
        "board": "main", "as_of": D3,
    }]).to_arrow().cast(schema.INSTRUMENTS)
    history = pl.DataFrame([{
        "symbol": "000001.SZ", "name": "深发展A",
        "start_date": date(1991, 4, 3), "end_date": date(2012, 8, 1),
        "ann_date": date(2012, 7, 27), "change_reason": "更名",
    }]).to_arrow().cast(schema.NAME_HISTORY)
    warehouse.write("instruments", instruments)
    warehouse.write("name_history", history)


def test_builds_canonical_reference_without_changing_old_tables(warehouse):
    _seed_reference(warehouse)
    assert reference_preflight(warehouse) == ()
    assert build_security_master(warehouse) == 1
    assert build_security_name_history(warehouse) == 1

    query = WarehouseQuery(warehouse)
    assert query.security_master().to_dicts() == query.instruments().to_dicts()
    assert query.security_name_history().to_dicts() == query.name_history().to_dicts()
    assert all(result.matches for result in reference_reconciliation(warehouse).values())


def test_empty_source_does_not_publish_an_empty_replacement(warehouse):
    assert build_security_master(warehouse) == 0
    assert build_security_name_history(warehouse) == 0
    query = WarehouseQuery(warehouse)
    assert query.security_master().height == 0
    assert query.security_name_history().height == 0


def test_preflight_rejects_legacy_master_and_missing_history_before_writes(warehouse):
    legacy = schema.INSTRUMENTS.remove(schema.INSTRUMENTS.get_field_index("as_of"))
    path = warehouse.root / "instruments" / "part.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(legacy.empty_table(), path)
    issues = reference_preflight(warehouse)
    assert any("instruments 为空" in item for item in issues)
    assert any("as_of" in item for item in issues)
    assert "name_history 为空" in issues
    with pytest.raises(RuntimeError, match="预检失败"):
        require_reference_sources(warehouse)
    assert not (warehouse.root / "security_master").exists()


def test_builder_refuses_to_invent_missing_as_of(warehouse):
    legacy = schema.INSTRUMENTS.remove(schema.INSTRUMENTS.get_field_index("as_of"))
    row = pl.DataFrame([{
        "symbol": "000001.SZ", "name": "平安银行", "market": "SZ",
        "list_date": date(1991, 4, 3), "delist_date": None, "board": "main",
    }]).to_arrow().cast(legacy)
    path = warehouse.root / "instruments" / "part.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(row, path)
    with pytest.raises(RuntimeError, match="as_of"):
        build_security_master(warehouse)


def _calendar_rows():
    return pl.DataFrame([
        {"exchange": "SSE", "cal_date": "20260918", "is_open": 1,
         "pretrade_date": "20260917"},
        {"exchange": "SSE", "cal_date": "20260919", "is_open": 0,
         "pretrade_date": "20260918"},
        {"exchange": "SSE", "cal_date": "20260920", "is_open": 0,
         "pretrade_date": "20260918"},
    ])


def test_calendar_keeps_source_closed_days_and_never_infers_weekdays(warehouse):
    assert publish_trading_calendar(warehouse, _calendar_rows()) == 3
    query = WarehouseQuery(warehouse)
    all_days = query.trading_calendar(D1, D3)
    assert all_days["dt"].to_list() == [D1, D2, D3]
    assert all_days["is_open"].to_list() == [True, False, False]
    assert query.trading_calendar(D1, D3, open_only=True)["dt"].to_list() == [D1]
    assert calendar_reconciliation(
        _calendar_rows(), all_days
    ).matches


def test_calendar_incremental_publish_preserves_uncovered_source_days(warehouse):
    publish_trading_calendar(warehouse, _calendar_rows())
    update = pl.DataFrame([{
        "exchange": "SSE", "cal_date": "20260920", "is_open": 1,
        "pretrade_date": "20260918",
    }])
    assert publish_trading_calendar(warehouse, update) == 1
    got = WarehouseQuery(warehouse).trading_calendar(D1, D3)
    assert got["is_open"].to_list() == [True, False, True]


def test_calendar_rejects_incomplete_source_contract():
    raw = pl.DataFrame({"cal_date": ["20260918"], "is_open": [1]})
    try:
        normalize_trading_calendar(raw)
    except ValueError as exc:
        assert "pretrade_date" in str(exc)
    else:
        raise AssertionError("缺少来源字段时必须失败")


def test_calendar_requires_one_source_fact_per_natural_day():
    raw = _calendar_rows().filter(pl.col("cal_date") != "20260919")
    with pytest.raises(RuntimeError, match="2026-09-19"):
        require_calendar_coverage(raw, D1, D3)


def test_name_interval_validator_reports_without_filling():
    frame = pl.DataFrame([
        {"symbol": "A", "start_date": date(2020, 1, 1), "end_date": date(2020, 1, 5)},
        {"symbol": "A", "start_date": date(2020, 1, 5), "end_date": date(2020, 1, 8)},
        {"symbol": "A", "start_date": date(2020, 1, 10), "end_date": None},
    ])
    issues = name_interval_issues(frame)
    assert [item.kind for item in issues] == ["overlap", "gap"]
    assert frame.height == 3
