from datetime import date
import json

import pandas as pd
import pyarrow.parquet as pq
import pytest

from scripts.win_download_dividends import export

START, END = date(2026, 9, 10), date(2026, 10, 28)
SYMBOL = '600017.SH'


def source(**changes):
    row = dict(interest=.033, stockBonus=0., stockGift=0., allotNum=0., allotPrice=0., dr=1.010791)
    row.update(changes)
    return pd.DataFrame([row], index=['20261027'])


def run(tmp_path, fetch):
    return export([SYMBOL], tmp_path, START, END, fetch)


@pytest.mark.parametrize("missing", [None, float("nan"), pd.NA])
def test_missing_source_remains_null_and_is_not_done(tmp_path, missing):
    counts = run(tmp_path, lambda *a, **k: source(stockGift=missing))
    row = pq.read_table(tmp_path / (SYMBOL + '.parquet')).to_pylist()[0]
    assert row['stock_per_share'] is None
    assert row['rights_per_share'] == 0.0
    assert row['cash_per_share'] == .033
    assert counts['incomplete'] == 1 and counts['done'] == 0


def test_resume_uses_exact_window_and_checks_durable_bytes(tmp_path):
    calls = []
    def fetch(symbol, **kwargs):
        calls.append((symbol, kwargs))
        return source()
    assert run(tmp_path, fetch)['done'] == 1
    assert calls == [(SYMBOL, dict(start_time='20260910', end_time='20261028'))]
    assert run(tmp_path, fetch)['reused'] == 1
    assert len(calls) == 1
    (tmp_path / (SYMBOL + '.parquet')).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='missing or changed'):
        run(tmp_path, fetch)
    assert len(calls) == 1


def test_crash_before_receipt_retries_without_losing_or_duplicating_rows(tmp_path, monkeypatch):
    import scripts.win_download_dividends as exporter
    original = exporter._atomic_json
    def crash_on_receipt(path, value):
        if path.name == SYMBOL + '.json':
            raise OSError('simulated interrupted receipt commit')
        return original(path, value)
    monkeypatch.setattr(exporter, '_atomic_json', crash_on_receipt)
    with pytest.raises(OSError):
        run(tmp_path, lambda *a, **k: source())
    assert pq.read_table(tmp_path / (SYMBOL + '.parquet')).num_rows == 1
    assert not (tmp_path / (SYMBOL + '.json')).exists()
    monkeypatch.setattr(exporter, '_atomic_json', original)
    assert run(tmp_path, lambda *a, **k: source())['done'] == 1
    assert pq.read_table(tmp_path / (SYMBOL + '.parquet')).num_rows == 1


def test_empty_is_observed_separately_from_none_and_can_be_resumed(tmp_path):
    assert run(tmp_path, lambda *a, **k: None)['failed'] == 1
    assert not (tmp_path / (SYMBOL + '.parquet')).exists()
    assert run(tmp_path, lambda *a, **k: pd.DataFrame())['empty'] == 1
    assert json.loads((tmp_path / (SYMBOL + '.json')).read_text())['status'] == 'empty'
    assert run(tmp_path, lambda *a, **k: None)['reused'] == 1


@pytest.mark.parametrize('frame', [
    source(interest='bad'), source(dr=float('inf')),
    source().rename(index={'20261027': '20261027000000'}),
    source().rename(index={'20261027': '20260909'}),
    pd.concat([source(), source()]),
])
def test_invalid_source_does_not_become_valid_event(tmp_path, frame):
    assert run(tmp_path, lambda *a, **k: frame)['failed'] == 1
    assert not (tmp_path / (SYMBOL + '.parquet')).exists()


def test_old_completion_log_and_changed_window_are_rejected(tmp_path):
    (tmp_path / 'done.csv').write_text(SYMBOL + '\n')
    with pytest.raises(ValueError, match='legacy'):
        run(tmp_path, lambda *a, **k: source())
    (tmp_path / 'done.csv').unlink()
    run(tmp_path, lambda *a, **k: source())
    with pytest.raises(ValueError, match='scope changed'):
        export([SYMBOL], tmp_path, START, date(2026, 10, 29), lambda *a, **k: source())
