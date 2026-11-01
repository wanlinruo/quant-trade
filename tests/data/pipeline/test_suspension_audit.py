"""跨真实写入口回归；样本为fixture，不作为市场数据证据。"""
import json
from datetime import date, timedelta

import polars as pl
import pytest

from qsys.data.pipeline import suspension_audit
from qsys.data.pipeline.premarket_suspensions import sync_premarket_suspensions
from qsys.data.pipeline.tushare_reference import ingest_suspensions
from qsys.data.query import WarehouseQuery

DAY = date(2026, 10, 28)


class Provider:
    def __init__(self, *symbols, kind='S', error=None):
        self.symbols, self.kind, self.error = symbols, kind, error

    def suspend_d(self, day):
        if self.error:
            raise self.error
        if not self.symbols:
            return pl.DataFrame()
        return pl.DataFrame({
            'ts_code': list(self.symbols),
            'trade_date': [day.strftime('%Y%m%d')] * len(self.symbols),
            'suspend_type': [self.kind] * len(self.symbols),
            'suspend_timing': [None] * len(self.symbols),
        })


def records(warehouse, day=DAY):
    return [json.loads(p.read_text()) for p in
            (warehouse.root / 'suspend_daily' / '_observations' / day.isoformat())
            .glob('*.observed.json')]


def rows(warehouse, day=DAY):
    return WarehouseQuery(warehouse).suspensions(None, day, day)


def test_two_entries_preserve_old_new_evidence_and_unresolved_ids(warehouse):
    sync_premarket_suspensions(Provider('fixture-a', 'fixture-b'), warehouse, DAY)
    refused = sync_premarket_suspensions(Provider('fixture-a'), warehouse, DAY)
    assert not refused.accepted and rows(warehouse).height == 2
    before = {p: p.read_bytes() for p in warehouse.root.rglob('*.json')}
    observed = []
    assert ingest_suspensions(Provider('fixture-a'), warehouse, [DAY],
                              on_observation=observed.append) == 1
    accepted = sync_premarket_suspensions(Provider('fixture-a'), warehouse, DAY)
    assert accepted.accepted and rows(warehouse).height == 1
    assert refused.observation_id in accepted.unresolved_ids
    assert set(observed[0].unresolved_ids) <= set(accepted.unresolved_ids)
    assert all(p.read_bytes() == content for p, content in before.items())
    replacement = next(r for r in records(warehouse)
                       if r['observation_id'] == observed[0].observation_id)
    assert len(replacement['previous']['rows']) == 2
    assert len(replacement['incoming']['rows']) == 1
    assert replacement['removed'] == [{'symbol': 'fixture-b', 'dt': '2026-10-28',
                                       'suspend_type': 'S', 'suspend_timing': None}]
    assert replacement['partition_before']['sha256']
    assert replacement['historical_observations_reconstructed'] is False


@pytest.mark.parametrize('new', [Provider('fixture-b'), Provider('fixture-a', kind='R'), Provider()])
def test_equal_count_key_value_changes_and_empty_are_audited(warehouse, new):
    ingest_suspensions(Provider('fixture-a'), warehouse, [DAY])
    got = []
    ingest_suspensions(new, warehouse, [DAY], on_observation=got.append)
    record = next(r for r in records(warehouse) if r['observation_id'] == got[0].observation_id)
    assert len(record['removed']) == 1
    assert got[0].unresolved_ids
    assert rows(warehouse).height == len(new.symbols)  # 原整日替换策略保持


def test_same_response_and_first_addition_are_not_unresolved(warehouse):
    got = []
    for _ in range(2):
        ingest_suspensions(Provider('fixture-a'), warehouse, [DAY], on_observation=got.append)
    assert all(not a.unresolved_ids for a in got)
    assert rows(warehouse).height == 1


def test_multiday_preserves_other_days_and_exception_before_batch_writes(warehouse):
    other = DAY - timedelta(days=1)
    ingest_suspensions(Provider('fixture-other'), warehouse, [other])
    ingest_suspensions(Provider('fixture-a'), warehouse, [DAY, DAY + timedelta(days=1)])
    assert rows(warehouse, other)['symbol'].to_list() == ['fixture-other']
    assert len(records(warehouse, other)) == 1
    class FailsSecond(Provider):
        def suspend_d(self, day):
            if day != DAY:
                raise RuntimeError('fixture-source-failure')
            return super().suspend_d(day)
    before = {p: p.read_bytes() for p in warehouse.root.rglob('*') if p.is_file()}
    with pytest.raises(RuntimeError, match='fixture-source-failure'):
        ingest_suspensions(FailsSecond('fixture-new'), warehouse, [DAY, other])
    assert all(p.read_bytes() == content for p, content in before.items())
    assert set(p for p in warehouse.root.rglob('*') if p.is_file()) == set(before)


def test_audit_before_write_failure_leaves_market_bytes_unchanged(warehouse, monkeypatch):
    ingest_suspensions(Provider('fixture-a'), warehouse, [DAY])
    path = warehouse.path_for('suspend_daily', year=DAY.year)
    before = path.read_bytes()
    def fail(*args):
        raise OSError('fixture-audit-disk-failure')
    monkeypatch.setattr(suspension_audit, '_save_new', fail)
    with pytest.raises(OSError, match='fixture-audit-disk-failure'):
        ingest_suspensions(Provider(), warehouse, [DAY])
    assert path.read_bytes() == before


def test_write_succeeded_but_receipt_failed_remains_unresolved(warehouse, monkeypatch):
    original = suspension_audit._save_new
    def fail_result(path, value):
        if path.name.endswith('.result.json'):
            raise OSError('fixture-result-failure')
        original(path, value)
    with monkeypatch.context() as m:
        m.setattr(suspension_audit, '_save_new', fail_result)
        with pytest.raises(OSError, match='fixture-result-failure'):
            ingest_suspensions(Provider('fixture-a'), warehouse, [DAY])
    assert rows(warehouse).height == 1  # 不冒充写前失败或自动重放
    pending = records(warehouse)[0]['observation_id']
    got = []
    ingest_suspensions(Provider('fixture-a'), warehouse, [DAY], on_observation=got.append)
    assert pending in got[0].unresolved_ids


def test_write_failure_retains_attempt_and_propagates(warehouse, monkeypatch):
    ingest_suspensions(Provider('fixture-a'), warehouse, [DAY])
    before = warehouse.path_for('suspend_daily', year=DAY.year).read_bytes()
    with monkeypatch.context() as m:
        def fail(*args, **kwargs):
            raise PermissionError('fixture-write-failure')
        m.setattr(warehouse, 'write', fail)
        with pytest.raises(PermissionError):
            ingest_suspensions(Provider('fixture-b'), warehouse, [DAY])
    assert warehouse.path_for('suspend_daily', year=DAY.year).read_bytes() == before
    results = [json.loads(p.read_text()) for p in warehouse.root.rglob('*.result.json')]
    assert any(r['status'] == 'attempt_failed' for r in results)
    got = []
    ingest_suspensions(Provider('fixture-a'), warehouse, [DAY], on_observation=got.append)
    assert got[0].unresolved_ids


def test_corrupt_history_does_not_silently_disappear(warehouse):
    ingest_suspensions(Provider('fixture-a'), warehouse, [DAY])
    observed = next(warehouse.root.rglob('*.observed.json'))
    observed.write_text('{corrupt')
    before = warehouse.path_for('suspend_daily', year=DAY.year).read_bytes()
    with pytest.raises(json.JSONDecodeError):
        ingest_suspensions(Provider('fixture-b'), warehouse, [DAY])
    assert warehouse.path_for('suspend_daily', year=DAY.year).read_bytes() == before


def test_premarket_same_count_change_warns_even_when_accepted(warehouse):
    sync_premarket_suspensions(Provider('fixture-a'), warehouse, DAY)
    changed = sync_premarket_suspensions(Provider('fixture-b'), warehouse, DAY,
                                         entry='daily_archive')
    assert changed.accepted and changed.unresolved_ids
    record = next(r for r in records(warehouse) if r['observation_id'] == changed.observation_id)
    assert record['entry'] == 'daily_archive'
    assert rows(warehouse)['symbol'].to_list() == ['fixture-b']


def test_premarket_audit_failure_happens_before_accepted_market_write(warehouse, monkeypatch):
    sync_premarket_suspensions(Provider('fixture-a'), warehouse, DAY)
    target = warehouse.path_for('suspend_daily', year=DAY.year)
    baseline = target.read_bytes()
    def fail(*args):
        raise OSError('fixture-audit-failed')
    monkeypatch.setattr(suspension_audit, '_save_new', fail)
    with pytest.raises(OSError, match='fixture-audit-failed'):
        sync_premarket_suspensions(Provider('fixture-b'), warehouse, DAY)
    assert target.read_bytes() == baseline


def test_duplicate_row_removal_is_not_lost_in_set_difference(warehouse):
    ingest_suspensions(Provider('fixture-a', 'fixture-a'), warehouse, [DAY])
    got = []
    ingest_suspensions(Provider('fixture-a'), warehouse, [DAY], on_observation=got.append)
    record = next(r for r in records(warehouse) if r['observation_id'] == got[0].observation_id)
    assert len(record['removed']) == 1 and got[0].unresolved_ids


def test_existing_receipt_cannot_be_overwritten(tmp_path):
    path = tmp_path / 'receipt.json'
    suspension_audit._save_new(path, {'first': True})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        suspension_audit._save_new(path, {'second': True})
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('*.tmp'))
