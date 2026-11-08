import importlib.util
import json
import mmap
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.scripts.test_qmt_authorized_order_executor import (
    ACCOUNT, KEY, RUN, FakeApi, load as load_executor, plan_dict)

REFERENCE = Path(__file__).resolve().parents[2] / 'docs' / 'reference'
EXPECT = {'q601-01234567-a': 'resting', 'q601-01234567-b': 'fill'}


def load_driver():
    spec = importlib.util.spec_from_file_location(
        'qsys_authorized_order_driver', REFERENCE / 'qmt-authorized-order-driver.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Broker(FakeApi):
    """Fake QMT trading API that answers through the executor's native callbacks."""

    def __init__(self, executor_module, fill_a=False, fill_b=True, reject=None):
        super().__init__()
        self.module, self.fill_a, self.fill_b, self.reject = executor_module, fill_a, fill_b, reject
        self.pending = []

    def passorder(self, op, order_type, account, symbol, pr_type, price, qty, name, quick, remark, C):
        super().passorder(op, order_type, account, symbol, pr_type, price, qty, name, quick, remark, C)
        if remark == self.reject:
            args = SimpleNamespace(strategyName=f'{name}_&&&_{remark}', orderCode=symbol, prType=pr_type)
            self.pending.append(lambda: self.module.orderError_callback(C, args, 'invalid'))
            return
        sysid = f'SYS-{remark[-1]}'
        filled = self.fill_a if remark.endswith('-a') else self.fill_b
        row = SimpleNamespace(m_strRemark=remark, m_strOrderSysID=sysid,
                              m_nOrderStatus=56 if filled else 50, m_strAccountID=account,
                              m_nVolumeTotalOriginal=qty, m_nVolumeTraded=qty if filled else 0)
        self.orders.append(row)
        self.pending.append(lambda: self.module.order_callback(C, row))
        if filled:
            deal = SimpleNamespace(m_strRemark=remark, m_strTradeID='T-' + remark[-1],
                                   m_strOrderSysID=sysid, m_dPrice=price, m_nVolume=qty,
                                   m_strAccountID=account)
            self.deals.append(deal)
            self.pending.append(lambda: self.module.deal_callback(C, deal))

    def cancel(self, sysid, account, account_type, C):
        super().cancel(sysid, account, account_type, C)
        for row in self.orders:
            if row.m_strOrderSysID == sysid:
                row.m_nOrderStatus = 54
                self.pending.append(lambda row=row: self.module.order_callback(C, row))
        return True


def session(tmp_path, ticking=True, **broker_options):
    executor_module = load_executor()
    driver = load_driver()
    memory = mmap.mmap(-1, executor_module.MAP_SIZE)
    plan = executor_module.validate_plan(executor_module.canonical(plan_dict()).decode('ascii'), RUN)
    broker = Broker(executor_module, **broker_options)
    executor = executor_module.Executor(RUN, KEY, plan, broker.table(),
                                        executor_module.ReportRing(memory))
    executor_module._STATE = dict(memory=memory, executor=executor, ticks=0, last_generation=0,
                                  stopped=False, deadline_ms=executor_module.now_ms() + 600000)
    executor_module.MAX_TICKS = 10 ** 6  # one simulated tick per driver sleep

    def sleep(_seconds):
        if ticking:
            executor_module.on_timer(None)
            while broker.pending:
                broker.pending.pop(0)()

    journal = driver.Journal(tmp_path / 'journal.jsonl')
    client = driver.Client(memory, RUN, KEY, journal, sleep=sleep, reply_timeout=0.5)
    return driver, client, broker, sleep, tmp_path / 'journal.jsonl'


def events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def run(driver, client, sleep):
    facts = driver.Facts()
    outcome = driver.run_plan(client, plan_dict(), EXPECT, facts, sleep=sleep,
                              resting_timeout=2, fill_timeout=2, cancel_timeout=2)
    return outcome, facts


def test_full_plan_cancels_resting_order_and_fills_marketable_order(tmp_path):
    driver, client, broker, sleep, journal = session(tmp_path)

    outcome, facts = run(driver, client, sleep)

    assert outcome == {'q601-01234567-a': 'cancelled', 'q601-01234567-b': 'filled'}
    assert [c[0] for c in broker.calls if c[0] != 'query'] == ['passorder', 'cancel', 'passorder']
    assert facts.deals['q601-01234567-b'][0]['m_strTradeID'] == 'T-b'
    rows = events(journal)
    sends = [r for r in rows if r['event'] == 'sending' and r['kind'] == 'step']
    assert [r['seq'] for r in sends] == [1, 2, 3]
    assert any(r['event'] == 'cancel_skipped' and r['step'] == 4 for r in rows)
    assert ACCOUNT not in journal.read_text()


def test_unexpected_fill_of_resting_order_stops_before_next_order(tmp_path):
    driver, client, broker, sleep, _ = session(tmp_path, fill_a=True)

    with pytest.raises(driver.SequenceStop) as stop:
        run(driver, client, sleep)

    assert stop.value.reason == 'resting_order_not_reported'
    assert stop.value.facts['order_status'] == 56
    assert [c[0] for c in broker.calls if c[0] == 'passorder'] == ['passorder']


def test_unfilled_marketable_order_is_cancelled(tmp_path):
    driver, client, broker, sleep, _ = session(tmp_path, fill_b=False)

    outcome, _ = run(driver, client, sleep)

    assert outcome == {'q601-01234567-a': 'cancelled', 'q601-01234567-b': 'cancelled'}
    assert [c[1] for c in broker.calls if c[0] == 'cancel'] == ['SYS-a', 'SYS-b']


def test_order_error_stops_sequence(tmp_path):
    driver, client, broker, sleep, _ = session(tmp_path, reject='q601-01234567-a')

    with pytest.raises(driver.SequenceStop) as stop:
        run(driver, client, sleep)

    assert stop.value.reason == 'resting_order_not_reported'
    assert stop.value.facts['order_error'] is True
    assert not [c for c in broker.calls if c[0] == 'cancel']


def test_missing_reply_is_unknown_and_never_resent(tmp_path):
    driver, client, broker, sleep, journal = session(tmp_path, ticking=False)

    with pytest.raises(driver.TransportUnknown):
        run(driver, client, sleep)

    rows = events(journal)
    assert [r['event'] for r in rows] == ['sending', 'unknown']
    assert not broker.calls


def test_forged_reply_is_not_accepted(tmp_path):
    driver, client, broker, sleep, journal = session(tmp_path, ticking=False)
    memory = client.memory
    forged = json.dumps({'status': 'handled', 'mac': '0' * 64}).encode()
    memory.seek(4096)
    memory.write((1).to_bytes(4, 'little') + len(forged).to_bytes(4, 'little') + forged)

    with pytest.raises(driver.TransportUnknown):
        client.send('snapshot', 1, {'action': 'snapshot', 'n': 1})


def test_driver_import_has_no_side_effects_and_main_is_guarded():
    source = (REFERENCE / 'qmt-authorized-order-driver.py').read_text(encoding='utf-8')
    assert "if __name__ == '__main__':" in source
    for forbidden in ('passorder(', 'XtQuantTrader', 'xtquant', 'cancel_order(', 'requests',
                      'socket'):
        assert forbidden not in source
