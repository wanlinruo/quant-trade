from datetime import date
import json
import shutil

import pandas as pd
import polars as pl
import pyarrow.parquet as pq
import pytest

from scripts.win_download_dividends import export, bundle_snapshot
from qsys.data import schema
from qsys.data.pipeline.dividend_sync import (
    append_events, dividend_universe, import_window, sync_dividends, validate_snapshot,
)
from qsys.data.query import WarehouseQuery


DAY = date(2026, 10, 28)
SYMBOLS = ["000001.SZ", "600017.SH"]


def source(symbol, **_):
    if symbol == SYMBOLS[0]:
        return pd.DataFrame()
    return pd.DataFrame([dict(interest=.033, stockBonus=.1, stockGift=.2,
                             allotNum=0., allotPrice=0., dr=1.01)], index=["20261028"])


def make_bundle(tmp_path, fetch=source, run_id="sync-1"):
    snapshot = tmp_path / "snapshot"
    export(SYMBOLS, snapshot, DAY, DAY, fetch)
    bundle = tmp_path / "bundle"
    bundle_snapshot(snapshot, bundle, SYMBOLS, DAY, DAY, run_id)
    return bundle


def validate(bundle):
    return validate_snapshot(bundle, symbols=SYMBOLS, start=DAY, end=DAY, run_id="sync-1")


def test_complete_bundle_preserves_raw_arithmetic_and_observed_empty(tmp_path):
    bundle = make_bundle(tmp_path)
    frame = validate(bundle)
    assert frame.height == 1
    assert frame["stock_per_share"][0] == .1 + .2
    result = json.loads((bundle / "result.json").read_text())
    assert [r["status"] for r in result["receipts"]] == ["empty", "done"]
    assert {p.name for p in bundle.iterdir()} == {"events.parquet", "result.json"}


def test_export_and_bundle_fsync_use_writable_handles(tmp_path, monkeypatch):
    import os
    original = os.fsync
    def windows_flush(fd):
        # A zero-byte write changes no bytes, but rejects read-only handles on
        # POSIX too, matching the Windows FlushFileBuffers access requirement.
        os.write(fd, b"")
        original(fd)
    monkeypatch.setattr(os, "fsync", windows_flush)
    assert validate(make_bundle(tmp_path)).height == 1


@pytest.mark.parametrize("mutation", ["coverage", "run", "empty", "failed", "rows", "window"])
def test_incomplete_or_misbound_bundle_is_rejected(tmp_path, mutation):
    bundle = make_bundle(tmp_path)
    path = bundle / "result.json"
    result = json.loads(path.read_text())
    if mutation == "coverage":
        result["receipts"].pop()
    elif mutation == "run":
        result["run_id"] = "another-run"
    elif mutation == "empty":
        result["receipts"][1]["status"] = "empty"
    elif mutation == "failed":
        result["receipts"][0]["status"] = "failed"
    elif mutation == "rows":
        result["rows"] += 1
    else:
        result["start"] = "20261027"
    path.write_text(json.dumps(result))
    with pytest.raises(ValueError):
        validate(bundle)


def test_incomplete_source_cannot_be_bundled(tmp_path):
    snapshot = tmp_path / "snapshot"
    export(SYMBOLS, snapshot, DAY, DAY, lambda *_a, **_kw: None)
    with pytest.raises(ValueError, match="incomplete"):
        bundle_snapshot(snapshot, tmp_path / "bundle", SYMBOLS, DAY, DAY, "sync-1")
    assert not (tmp_path / "bundle").exists()


def test_changed_bytes_are_rejected_at_source_and_receiver(tmp_path):
    bundle = make_bundle(tmp_path)
    (bundle / "events.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="digest"):
        validate(bundle)
    (tmp_path / "snapshot" / (SYMBOLS[1] + ".parquet")).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        bundle_snapshot(tmp_path / "snapshot", tmp_path / "another", SYMBOLS, DAY, DAY, "sync-2")


def test_append_preserves_old_rows_and_refuses_entire_conflicting_batch(warehouse, tmp_path):
    fresh = validate(make_bundle(tmp_path))
    assert append_events(warehouse, fresh) == 1
    path = warehouse.path_for("corporate_actions")
    before = path.read_bytes()
    # The Warehouse layout is checked through actual query values as well as bytes.
    old = WarehouseQuery(warehouse).corporate_actions()
    assert append_events(warehouse, fresh) == 0
    assert path.read_bytes() == before
    conflict = pl.concat([
        fresh.with_columns(pl.lit(99.).alias("cash_per_share")),
        fresh.with_columns(pl.lit(SYMBOLS[0]).alias("symbol")),
    ])
    with pytest.raises(ValueError, match="conflicts"):
        append_events(warehouse, conflict)
    assert WarehouseQuery(warehouse).corporate_actions().equals(old)
    assert path.read_bytes() == before


def test_empty_response_does_not_delete_old_events(warehouse, tmp_path):
    fresh = validate(make_bundle(tmp_path))
    append_events(warehouse, fresh)
    assert append_events(warehouse, fresh.head(0)) == 0
    assert WarehouseQuery(warehouse).corporate_actions().equals(fresh)


def test_window_tracks_successful_response_not_last_event(tmp_path):
    checkpoint = tmp_path / "last-imported.json"
    start = date(2026, 10, 1)
    assert import_window(DAY, start, checkpoint) == start
    checkpoint.write_text(json.dumps({"protocol": "qsys-dividend-import-v1",
                                     "initial_start": start.isoformat(), "end": "2026-10-25"}))
    assert import_window(DAY, start, checkpoint) == date(2026, 10, 18)
    with pytest.raises(ValueError, match="backwards"):
        import_window(date(2026, 10, 24), start, checkpoint)
    with pytest.raises(ValueError, match="scope changed"):
        import_window(DAY, date(2026, 10, 2), checkpoint)
    with pytest.raises(ValueError, match="exceeds"):
        import_window(date(2026, 12, 1), start, checkpoint)


def test_native_scope_keeps_native_and_unknown_history_without_alias_mapping():
    import pyarrow as pa
    instruments = pl.from_arrow(pa.Table.from_pylist([
        {"symbol": "T600018.SH", "delist_date": date(2006, 10, 20), "as_of": DAY},
        {"symbol": "600018.SH", "delist_date": None, "as_of": DAY},
        {"symbol": "000001.SZ", "delist_date": date(2026, 10, 27), "as_of": DAY},
    ], schema=schema.INSTRUMENTS))
    actions = pl.DataFrame({"symbol": ["600017.SH", "T600018.SH"]})
    before = actions.clone()
    symbols, excluded = dividend_universe(instruments, actions, DAY)
    assert symbols == ["000001.SZ", "600017.SH", "600018.SH"]
    assert excluded == [{"symbol": "T600018.SH", "delist_date": "2006-10-20", "as_of": "2026-10-28"}]
    assert actions.equals(before)


@pytest.mark.parametrize("delisted", [None, DAY, date(2026, 10, 29)])
def test_non_native_identity_without_past_delisting_is_not_silently_dropped(delisted):
    import pyarrow as pa
    instruments = pl.from_arrow(pa.Table.from_pylist([
        {"symbol": "T600018.SH", "delist_date": delisted, "as_of": DAY},
        {"symbol": "600018.SH", "as_of": DAY},
    ], schema=schema.INSTRUMENTS))
    with pytest.raises(ValueError, match="explicit source verification"):
        dividend_universe(instruments, pl.DataFrame({"symbol": ["600017.SH"]}), DAY)


def test_real_export_bundle_import_checkpoint_and_unknown_delivery(warehouse, tmp_path):
    # Include a symbol absent from today's master via the existing event table.
    fresh = validate(make_bundle(tmp_path / "seed"))
    append_events(warehouse, fresh)
    instruments = pl.from_arrow(schema.INSTRUMENTS.empty_table())
    instruments = pl.DataFrame({"symbol": [SYMBOLS[0]]}).join(instruments, on="symbol", how="left")
    warehouse.write("instruments", instruments.to_arrow().cast(schema.INSTRUMENTS))
    script = tmp_path / "approved.py"
    script.write_text("# approved test script")
    import hashlib
    calls = []

    class Transport:
        fail = False

        def probe(self):
            return {"scripts": {"dividend": hashlib.sha256(script.read_bytes()).hexdigest()}}

        def prepare(self, job, manifest):
            calls.append(("prepare", job))
            assert manifest.read_text().splitlines()[1:] == [s + ",2026-10-28" for s in SYMBOLS]

        def run_snapshot(self, job, script, run_id, start, end, *, timeout):
            calls.append(("run", job))
            assert timeout == 900
            if self.fail:
                raise TimeoutError("unknown delivery")
            out = tmp_path / run_id
            export(SYMBOLS, out / "snapshot", start, end, source)
            self.bundle = out / "bundle"
            bundle_snapshot(out / "snapshot", self.bundle, SYMBOLS, start, end, run_id)

        def receive(self, job, destination):
            shutil.copytree(self.bundle, destination)

    transport = Transport()
    work = tmp_path / "work"
    assert sync_dividends(warehouse, DAY, initial_start=DAY, work_root=work,
                          transport=transport, script=script, run_id="sync-1") == 0
    checkpoint = work / "dividends" / "last-imported.json"
    before = checkpoint.read_bytes()
    assert json.loads(before)["scope_sha256"]
    transport.fail = True
    with pytest.raises(TimeoutError):
        sync_dividends(warehouse, DAY, initial_start=DAY, work_root=work,
                       transport=transport, script=script, run_id="sync-2")
    assert checkpoint.read_bytes() == before
    assert len([c for c in calls if c[0] == "run"]) == 2
    assert (work / "dividends" / "dividend_20261028_sync-2" / "requested.json").exists()
    with pytest.raises(FileExistsError):
        sync_dividends(warehouse, DAY, initial_start=DAY, work_root=work,
                       transport=transport, script=script, run_id="sync-2")
    assert len([c for c in calls if c[0] == "run"]) == 2


def cash_client(cash=.033001, *, mutation=None, calls=None):
    from qsys.data.providers.tushare_client import TushareClient

    def transport(body, timeout):
        request = json.loads(body)
        if calls is not None:
            calls.append(request)
        assert request['api_name'] == 'dividend'
        assert request['params'] == {'ts_code': SYMBOLS[1], 'ex_date': '20261028'}
        fields = ['ts_code', 'ann_date', 'div_proc', 'cash_div_tax', 'ex_date']
        assert request['fields'] == ','.join(fields)
        value = [SYMBOLS[1], '20260917', '实施', cash, '20261028']
        if mutation == 'wrong_symbol':
            value[0] = SYMBOLS[0]
        if mutation == 'wrong_date':
            value[4] = '20261027'
        if mutation == 'proposal':
            value[2] = '预案'
        if mutation == 'request_error':
            raise RuntimeError('PRIVATE_INPUT_MUST_NOT_LEAK')
        rows = [] if mutation == 'missing' else [value]
        if mutation == 'duplicate':
            rows.append(value)
        return {'code': 0, 'data': {'fields': fields, 'items': rows}}

    return TushareClient('synthetic-test-token', transport=transport, max_retries=1)


def test_cash_selection_preserves_raw_bundle_and_every_non_cash_value(tmp_path):
    from qsys.data.pipeline.dividend_sync import select_tushare_cash, _sha
    bundle = make_bundle(tmp_path)
    fresh = validate(bundle)
    original = (bundle / 'events.parquet').read_bytes()
    calls = []
    result = select_tushare_cash(fresh, client=cash_client(calls=calls), audit_dir=tmp_path,
                                 run_id='sync-1', qmt_sha256=_sha(bundle / 'events.parquet'))
    assert result['cash_per_share'].to_list() == [.033001]
    assert result.drop('cash_per_share').equals(fresh.drop('cash_per_share'))
    assert (bundle / 'events.parquet').read_bytes() == original
    assert len(calls) == 1
    assert pq.read_schema(tmp_path / 'cash-selected.parquet').equals(schema.CORPORATE_ACTIONS)
    records = [json.loads(line) for line in (tmp_path / 'cash-source.jsonl').read_text().splitlines()]
    assert records[0]['qmt_sha256'] == _sha(bundle / 'events.parquet')
    assert records[-1]['status'] == 'accepted'


@pytest.mark.parametrize('cash,mutation', [
    (None, None), (True, None), ('0.033001', None), (-.1, None), (float('inf'), None),
    (float('nan'), None), (10**400, None), (2**53 + 1, None),
    (.033001, 'missing'), (.033001, 'duplicate'), (.033001, 'wrong_symbol'),
    (.033001, 'wrong_date'), (.033001, 'proposal'), (.033001, 'request_error'),
])
def test_cash_quality_refusal_preserves_null_candidate_without_qmt_fallback(tmp_path, cash, mutation):
    from qsys.data.pipeline.dividend_sync import select_tushare_cash, _sha
    bundle = make_bundle(tmp_path)
    fresh = validate(bundle)
    with pytest.raises(ValueError, match='cash quality rejected'):
        select_tushare_cash(fresh, client=cash_client(cash, mutation=mutation), audit_dir=tmp_path,
                            run_id='sync-1', qmt_sha256=_sha(bundle / 'events.parquet'))
    candidate = pl.read_parquet(tmp_path / 'cash-selected.parquet')
    assert candidate['cash_per_share'].to_list() == [None]
    assert candidate.drop('cash_per_share').equals(fresh.drop('cash_per_share'))
    receipts = (tmp_path / 'cash-source.jsonl').read_text()
    assert json.loads(receipts.splitlines()[-1])['status'] == 'rejected'
    assert 'Infinity' not in receipts and ': NaN' not in receipts
    assert 'PRIVATE_INPUT_MUST_NOT_LEAK' not in receipts


def test_cash_empty_snapshot_does_not_request_or_invent_a_cash_event(tmp_path):
    from qsys.data.pipeline.dividend_sync import select_tushare_cash
    class Forbidden:
        def call(self, *args):
            raise AssertionError('no events need cash lookup')
    fresh = pl.from_arrow(schema.CORPORATE_ACTIONS.empty_table())
    assert select_tushare_cash(fresh, client=Forbidden(), audit_dir=tmp_path,
                               run_id='empty', qmt_sha256='a' * 64).is_empty()


def test_cash_budget_exhaustion_keeps_null_candidate_and_makes_no_more_requests(tmp_path, monkeypatch):
    from qsys.data.pipeline import dividend_sync
    fresh = validate(make_bundle(tmp_path))
    clock = iter([0., 121.])
    monkeypatch.setattr(dividend_sync, 'monotonic', lambda: next(clock))
    class Forbidden:
        def call(self, *args):
            raise AssertionError('expired cash lookup must not start a request')
    with pytest.raises(ValueError, match='cash quality rejected'):
        dividend_sync.select_tushare_cash(fresh, client=Forbidden(), audit_dir=tmp_path,
                                          run_id='expired', qmt_sha256='a' * 64)
    assert pl.read_parquet(tmp_path / 'cash-selected.parquet')['cash_per_share'].to_list() == [None]
    assert 'cash_source_deadline_exceeded' in (tmp_path / 'cash-source.jsonl').read_text()


def test_cash_policy_cannot_revert_checkpoint_or_run_without_client(warehouse, tmp_path):
    root = tmp_path / 'dividends'
    root.mkdir()
    checkpoint = root / 'last-imported.json'
    checkpoint.write_text(json.dumps({'protocol': 'qsys-dividend-import-v1',
                                      'initial_start': DAY.isoformat(), 'end': DAY.isoformat(),
                                      'cash_source': 'tushare'}))
    before = checkpoint.read_bytes()
    for source, client, message in [('qmt', None, 'silently revert'),
                                    ('tushare', None, 'client required')]:
        with pytest.raises(ValueError, match=message):
            sync_dividends(warehouse, DAY, initial_start=DAY, work_root=tmp_path,
                           transport=None, script=None, run_id='blocked',
                           cash_source=source, cash_client=client)
    assert checkpoint.read_bytes() == before
    assert sorted(p.name for p in root.iterdir()) == ['last-imported.json']


@pytest.mark.parametrize('failure', [None, 'existing_conflict', 'cash_lookup'])
def test_real_snapshot_cash_source_import_and_conflict_checkpoint_boundary(warehouse, tmp_path, failure):
    import hashlib
    fresh = validate(make_bundle(tmp_path / 'seed'))
    if failure == 'existing_conflict':
        append_events(warehouse, fresh)
    baseline = WarehouseQuery(warehouse).corporate_actions()
    import pyarrow as pa
    instruments = pa.Table.from_pylist([{'symbol': symbol, 'as_of': DAY} for symbol in SYMBOLS],
                                       schema=schema.INSTRUMENTS)
    warehouse.write('instruments', instruments)
    script = tmp_path / 'approved.py'
    script.write_text('# approved unchanged QMT downloader')
    class Transport:
        def probe(self):
            return {'scripts': {'dividend': hashlib.sha256(script.read_bytes()).hexdigest()}}
        def prepare(self, job, manifest):
            pass
        def run_snapshot(self, job, script, run_id, start, end, **kwargs):
            path = tmp_path / 'exported'
            export(SYMBOLS, path / 'snapshot', start, end, source)
            self.bundle = path / 'bundle'
            bundle_snapshot(path / 'snapshot', self.bundle, SYMBOLS, start, end, run_id)
            self.raw_bytes = (self.bundle / 'events.parquet').read_bytes()
        def receive(self, job, destination):
            shutil.copytree(self.bundle, destination)
    transport = Transport()
    work = tmp_path / 'work'
    kwargs = dict(initial_start=DAY, work_root=work, transport=transport, script=script,
                  run_id='cash-1', cash_source='tushare',
                  cash_client=cash_client(mutation='request_error' if failure == 'cash_lookup' else None))
    checkpoint = work / 'dividends' / 'last-imported.json'
    if failure:
        message = 'existing dividend event conflicts' if failure == 'existing_conflict' else 'cash quality rejected'
        with pytest.raises(ValueError, match=message):
            sync_dividends(warehouse, DAY, **kwargs)
        assert not checkpoint.exists()
        assert WarehouseQuery(warehouse).corporate_actions().equals(baseline)
    else:
        assert sync_dividends(warehouse, DAY, **kwargs) == 1
        assert WarehouseQuery(warehouse).corporate_actions()['cash_per_share'].to_list() == [.033001]
        receipt = json.loads(checkpoint.read_text())
        assert receipt['cash_source'] == 'tushare'
        assert len(receipt['cash_receipts_sha256']) == len(receipt['cash_selected_sha256']) == 64
    assert (transport.bundle / 'events.parquet').read_bytes() == transport.raw_bytes
    policy = work / 'dividends' / 'cash-source-policy.json'
    binding = policy.read_bytes()
    # N/N-1兼容基线恢复旧默认参数时，即使首个Tushare导入失败也不能换回QMT。
    with pytest.raises(ValueError, match='silently revert'):
        sync_dividends(warehouse, DAY, initial_start=DAY, work_root=work,
                       transport=None, script=None, run_id='compensated-default')
    assert policy.read_bytes() == binding


def test_compatible_cli_checks_bound_policy_before_calendar_or_raw_writes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from qsys.data import tushare_sync_cli
    from qsys.data.pipeline.dividend_sync import prepare_cash_source
    class ForbiddenClient:
        def call(self, *args):
            raise AssertionError('policy must be checked before any API')
    work = tmp_path / 'work'
    prepare_cash_source(work, cash_source='tushare', cash_client=ForbiddenClient(), run_id='activated')
    binding = (work / 'dividends' / 'cash-source-policy.json').read_bytes()
    monkeypatch.setattr(tushare_sync_cli, 'load_tushare_config', lambda _: SimpleNamespace(token='synthetic'))
    monkeypatch.setattr(tushare_sync_cli, 'TushareClient', lambda *args, **kwargs: ForbiddenClient())
    runs = tmp_path / 'runs'
    warehouse = tmp_path / 'warehouse'
    monkeypatch.setattr('sys.argv', ['qsys-tushare-sync', '--warehouse', str(warehouse),
        '--service-runs', str(runs), '--dividend-sync', '--dividend-initial-start', DAY.isoformat(),
        '--late-minute-work-root', str(work), '--late-minute-win-transport', 'gateway',
        '--late-minute-win-known-hosts', str(tmp_path / 'hosts'),
        '--dividend-win-script-source', str(tmp_path / 'approved.py')])
    with pytest.raises(ValueError, match='silently revert'):
        tushare_sync_cli.main()
    assert not warehouse.exists()
    assert (work / 'dividends' / 'cash-source-policy.json').read_bytes() == binding
    records = [json.loads(p.read_text()) for p in runs.rglob('*.json')]
    assert len(records) == 1 and records[0]['status'] == 'failed'


def test_cli_cash_policy_cannot_be_silently_ignored(monkeypatch, capsys):
    from qsys.data import tushare_sync_cli
    monkeypatch.setattr('sys.argv', ['qsys-tushare-sync', '--dividend-cash-source', 'tushare'])
    with pytest.raises(SystemExit) as exc:
        tushare_sync_cli.main()
    assert exc.value.code == 2
    assert '--dividend-cash-source requires --dividend-sync' in capsys.readouterr().err
