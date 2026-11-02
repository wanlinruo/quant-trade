from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts import probe_native_minute_zero as probe

DAY = date(2026, 11, 2)


class Cache:
    def __init__(self):
        self.calls = []

    def get_local_data(self, **kw):
        self.calls.append(kw)
        symbol = kw['stock_list'][0]
        if kw['period'] == 'tick':
            return {symbol: pd.DataFrame()}
        return {symbol: pd.DataFrame({
            'time': [1793583000000, 1793583060000],
            'open': [10., 10.], 'high': [10., 10.], 'low': [10., 10.],
            'close': [10., 10.], 'volume': [0, 5], 'amount': [0., 5000.],
        }, index=['20261102093000', '20261102093100'])}

    def __getattr__(self, name):
        raise AssertionError('forbidden API: ' + name)


def test_bounded_native_calls_keep_zero_and_report_tick_unavailable():
    api = Cache()
    r = probe.observe(api, DAY)
    assert len(api.calls) == len(probe.SYMBOLS) * 2 + len(probe.TICK_SYMBOLS)
    assert all(c['count'] == -1 and c['dividend_type'] == 'none' for c in api.calls)
    assert all(c['start_time'] == '20261102' for c in api.calls if c['period'] == '1m')
    assert all(c['fill_data'] is False for c in api.calls if c['period'] == 'tick')
    assert all(c['false']['rows'][0]['volume'] == 0 for c in r['minute_cases'])
    assert all(c['data']['count'] == 0 for c in r['tick_cases'])


def test_budget_exhausted_before_any_api_call():
    api = Cache()
    times = iter([0, 41])
    with pytest.raises(TimeoutError):
        probe.observe(api, DAY, clock=lambda: next(times))
    assert not api.calls


def test_failed_later_read_preserves_completed_cases_and_attempts():
    api = Cache()
    original = api.get_local_data
    def read(**kw):
        if len(api.calls) == 2:
            raise RuntimeError('private configuration secret')
        return original(**kw)
    api.get_local_data = read
    report = {}
    with pytest.raises(RuntimeError):
        probe.observe(api, DAY, result=report)
    assert len(report['minute_cases']) == 1
    assert len(report['calls']) == 3


def test_null_is_null_and_native_values_are_not_rounded():
    frame = pd.DataFrame({'volume': [None, 0.], 'amount': [0., 1.00000000000001]})
    r = probe.frame_record(frame, ('volume', 'amount'))
    assert r['rows'][0]['volume'] is None
    assert r['rows'][1]['amount'] == 1.00000000000001


@pytest.mark.parametrize('frame', [pd.DataFrame({'volume': [float('inf')]}),
    pd.DataFrame({'volume': ['unknown']}), pd.DataFrame({'other': [1]}),
    pd.DataFrame({'volume': [0, 1]}, index=['duplicate', 'duplicate'])])
def test_invalid_native_values_or_identity_are_not_repaired(frame):
    with pytest.raises((TypeError, ValueError)):
        probe.frame_record(frame, ('volume',))


def test_unclosed_day_fails_before_sdk_import(capsys):
    assert probe.main(['--port', '58610', '--day', '9999-01-01']) == 1
    text = capsys.readouterr().out
    assert 'ValueError' in text and 'completed' not in text


def test_sdk_error_message_is_never_output(monkeypatch, capsys):
    api = SimpleNamespace(connect=lambda **_: (_ for _ in ()).throw(RuntimeError('token=private')))
    monkeypatch.setitem(__import__('sys').modules, 'xtquant', SimpleNamespace(xtdata=api))
    assert probe.main(['--port', '58610', '--day', '2026-10-01']) == 1
    text = capsys.readouterr().out
    assert 'RuntimeError' in text and 'token=' not in text and 'private' not in text


def test_documented_structured_tick_records_keep_native_fields():
    import numpy as np
    api = Cache()
    original = api.get_local_data
    def read(**kw):
        if kw['period'] == 'tick':
            return {kw['stock_list'][0]: np.array([(1793583000000, 10., 0, 0.)],
                dtype=[('time', 'i8'), ('lastPrice', 'f8'), ('volume', 'i8'), ('amount', 'f8')])}
        return original(**kw)
    api.get_local_data = read
    r = probe.observe(api, DAY)
    assert r['tick_cases'][0]['data']['native_container'] == 'ndarray'
    assert r['tick_cases'][0]['data']['rows'][0]['volume'] == 0


def test_unstructured_tick_array_is_rejected_without_inventing_fields():
    import numpy as np
    api = Cache()
    original = api.get_local_data
    def read(**kw):
        return {kw['stock_list'][0]: np.array([1])} if kw['period'] == 'tick' else original(**kw)
    api.get_local_data = read
    with pytest.raises(ValueError, match='unexpected_tick_record_fields'):
        probe.observe(api, DAY)
