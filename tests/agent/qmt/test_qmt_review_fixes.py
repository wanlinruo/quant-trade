"""Whole-branch review findings (#601): each test reproduces one finding."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from qsys.agent.fill_tracker import BrokerOrder, FillTracker
from qsys.agent.qmt import protocol
from qsys.agent.qmt.memory import ReportGap, SharedMemory
from qsys.backtest.fees import AShareFeeModel
from qmt_fake import ACCOUNT, FakeQmtApi, load_executor
from test_qmt_executor_source import NOW, place, send, make
from test_qmt_reconcile import CID, Rig, T, lost_dispatch


# I1 -------------------------------------------------------------------------
def test_status_52_is_partially_filled_not_terminal():
    order = BrokerOrder('S1', CID, '510300.SH', 'buy', 100, 40, 52)
    out = FillTracker(AShareFeeModel()).observe([], [order], seen_trade_ids=frozenset(),
                                                closed_order_ids=frozenset(), now=datetime(2026, 11, 9))
    assert [s.status for s in out.statuses] == ['partially_filled']
    assert out.rejects == () and out.closed_order_ids == ()


def test_executor_counts_52_as_active():
    mod, ex, api, buf, clock = make()
    api.order(CID, status=52, traded=40)
    assert ex.snapshot()['active_orders'] == 1


# I2 -------------------------------------------------------------------------
def test_executor_restart_continues_numbering_and_never_writes_gateway_cursor():
    buf = bytearray(protocol.MAP_SIZE)
    mem = SharedMemory(buf)
    api = FakeQmtApi()
    mod = load_executor(ACCOUNT_ID=ACCOUNT)
    ex = mod.Executor('a' * 32, bytes(32), api.as_api(), buf, lambda: NOW)
    ex.record_order('callback', api.order(CID))
    assert len(mem.read_reports()) == 1
    ex2 = mod.Executor('a' * 32, bytes(32), api.as_api(), buf, lambda: NOW)
    assert int.from_bytes(buf[protocol.CONTROL_OFFSET + 16:protocol.CONTROL_OFFSET + 24], 'little') == 1
    ex2.record_order('callback', api.order('q261109002abcdef012345'))
    assert [r['m_strRemark'] for r in mem.read_reports()] == ['q261109002abcdef012345']


def test_cursor_ahead_of_latest_is_a_gap():
    buf = bytearray(protocol.MAP_SIZE)
    buf[protocol.CONTROL_OFFSET + 16:protocol.CONTROL_OFFSET + 24] = (5).to_bytes(8, 'little')
    buf[protocol.EXECUTOR_OFFSET + 40:protocol.EXECUTOR_OFFSET + 48] = (2).to_bytes(8, 'little')
    with pytest.raises(ReportGap):
        SharedMemory(buf).read_reports()


# I3 -------------------------------------------------------------------------
def test_daily_buy_cap_survives_executor_restart():
    api = FakeQmtApi()
    api.order(CID, volume=2000, price=4.0, date='20251109')       # 8000 today (NOW is 2025-11-09 CST)
    mod, ex, api, buf, clock = make(api)
    receipt = send(ex, clock, command=place(qty=1000, price=3.0, cid='q251109002abcdef012345'))
    assert receipt['reason'] == 'daily_buy_limit'


# I4 -------------------------------------------------------------------------
def test_order_query_none_fails_snapshot():
    mod, ex, api, buf, clock = make()
    api.unavailable = ('ORDER',)
    receipt = send(ex, clock, kind='snapshot')
    assert receipt['result']['status'] == 'call_exception'


def _absent_after(rig, d, steps):
    r = rig.reconciler(d)
    out = []
    for seconds in steps:
        rig.advance(seconds)
        out += r.run_once()
    return out


def test_foreign_remark_orders_block_absent_release(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as d:
        lost_dispatch(rig, d)
        rig.api.order('q2611', status=50)                         # remark truncated by the counter
        assert _absent_after(rig, d, [121, 6, 6]) == []
        assert d.store.has_unresolved()


def test_previous_day_unknown_is_never_absent(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as d:
        lost_dispatch(rig, d)
        assert _absent_after(rig, d, [24 * 3600, 6, 6]) == []
        assert d.store.has_unresolved()


# I5 -------------------------------------------------------------------------
def test_oversize_order_row_keeps_status_and_remark():
    buf = bytearray(protocol.MAP_SIZE)
    mem = SharedMemory(buf)
    api = FakeQmtApi()
    ex = load_executor(ACCOUNT_ID=ACCOUNT).Executor('a' * 32, bytes(32), api.as_api(), buf, lambda: NOW)
    row = api.order(CID, status=57)
    row.m_strErrorMsg = '委托失败' * 40
    row.m_strCancelInfo = '撤单信息' * 40
    ex.record_order('callback', row)
    (got,) = mem.read_reports()
    assert got['m_strRemark'] == CID and got['m_nOrderStatus'] == 57
    assert got['m_nVolumeTotalOriginal'] == 100


# I6 -------------------------------------------------------------------------
def test_kill_cancel_failure_is_retried():
    mod, ex, api, buf, clock = make()
    api.order(CID, status=50)
    calls = []

    def flaky(*args):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError('native')
        return True
    ex.api['cancel'] = flaky
    buf[protocol.CONTROL_OFFSET + 8:protocol.CONTROL_OFFSET + 12] = (1).to_bytes(4, 'little')
    with pytest.raises(RuntimeError):
        ex.tick(None)
    ex.tick(None)
    ex.tick(None)
    assert len(calls) == 2


# M6 → Important -----------------------------------------------------------------
def test_seen_attempts_are_pruned_after_transport_ttl():
    mod, ex, api, buf, clock = make()
    for i in range(5):
        send(ex, clock, kind='ping', attempt=f'{i:032x}', seq=i + 1)
    clock.now += 2 * protocol.TRANSPORT_TTL_MS + 1
    send(ex, clock, kind='ping', attempt='f' * 32, seq=9)
    assert len(ex.seen) == 1


# M10 → Important ----------------------------------------------------------------
def test_order_error_message_redacts_long_digit_runs():
    buf = bytearray(protocol.MAP_SIZE)
    mem = SharedMemory(buf)
    ex = load_executor(ACCOUNT_ID=ACCOUNT).Executor('a' * 32, bytes(32), FakeQmtApi().as_api(),
                                                    buf, lambda: NOW)
    ex.record_error(SimpleNamespace(strategyName='qsys601', userOrderId=CID, orderCode='510300.SH'),
                    f'账户{ACCOUNT}资金不足')
    (row,) = mem.read_reports()
    assert ACCOUNT not in row['message']


# M2 / M3 (fixed before F5) --------------------------------------------------------
def test_burst_of_orders_keeps_gateway_heartbeat_fresh():
    from qsys.agent.qmt.port import QmtIpcPort
    buf = bytearray(protocol.MAP_SIZE)
    mem = SharedMemory(buf)
    clock = {'ms': NOW}
    ex = load_executor(ACCOUNT_ID=ACCOUNT).Executor('a' * 32, bytes(32), FakeQmtApi().as_api(), buf,
                                                    lambda: clock['ms'])
    mem.beat(NOW)

    def sleep(_):
        clock['ms'] += 1000                       # each order waits about one executor tick
        ex.tick(None)
    port = QmtIpcPort(mem, run_id='a' * 32, key=bytes(32), clock_ms=lambda: clock['ms'], sleep=sleep)
    for _ in range(15):
        assert port.request('ping')['status'] == 'accepted'
    assert not ex.gateway_stale(clock['ms'])


def test_receipt_timeout_exceeds_transport_ttl():
    import inspect
    from qsys.agent.qmt.port import QmtIpcPort
    default = inspect.signature(QmtIpcPort).parameters['receipt_timeout_s'].default
    assert default * 1000 > protocol.TRANSPORT_TTL_MS
