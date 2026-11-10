"""Executor started before the gateway (#601 F4 boot order, 2026-11-10 field finding).

QMT restarts on its own (08:53 on 2026-11-10) and auto-runs the executor instance
before any gateway exists.  The executor must wait for the coordinator marker and
attach on its timer instead of failing init permanently.
"""
import sys
from types import SimpleNamespace

from qsys.agent.qmt import protocol
from qsys.agent.qmt.memory import SharedMemory
from qmt_fake import ACCOUNT, FakeQmtApi, load_executor

RUN = 'c' * 32


class Buf(bytearray):
    def close(self):
        pass


class Context:
    def __init__(self):
        self.calls = []

    def set_account(self, account):
        self.calls.append(('set_account', account))

    def schedule_run(self, fn, start, count, interval, name):
        self.calls.append(('schedule_run', name))
        return 7

    def cancel_schedule_run(self, name):
        self.calls.append(('cancel_schedule_run', name))
        return True


def boot(monkeypatch, buf):
    fake_mmap = SimpleNamespace(ACCESS_WRITE=2, mmap=lambda fd, size, tagname, access: buf)
    monkeypatch.setitem(sys.modules, 'mmap', fake_mmap)
    mod = load_executor(ENABLED=True, RUN_ID=RUN, CLIENT_BUILD='2.1.33.0', ACCOUNT_ID=ACCOUNT,
                        KEY_HEX='0' * 64)
    for name, fn in FakeQmtApi().as_api().items():
        setattr(mod, name, fn)
    rows = []
    monkeypatch.setattr(mod, 'emit', lambda status, **facts: rows.append(status))
    return mod, rows


def test_init_without_gateway_waits_then_attaches_on_timer(monkeypatch):
    buf = Buf(protocol.MAP_SIZE)
    mem = SharedMemory(buf)
    mod, rows = boot(monkeypatch, buf)
    c = Context()
    mod.init(c)
    assert rows == ['waiting_for_gateway']
    assert ('set_account', ACCOUNT) in c.calls and ('schedule_run', 'qsys601s_' + RUN) in c.calls
    assert mod._active() is None and not mem.scheduled(RUN)
    mod.on_timer(c)                       # still no gateway: stays waiting, quietly
    assert mod._active() is None and rows == ['waiting_for_gateway']
    mem.write_coordinator_marker(RUN)     # gateway arrives later
    mod.on_timer(c)
    assert mod._active() is not None and mem.scheduled(RUN)
    assert rows[-1] == 'scheduled'
    state = mem.executor_state()
    assert state.heartbeat_ms > 0 and state.started_ms > 0 and len(state.epoch) == 32
    assert [k for k, _ in c.calls].count('schedule_run') == 1


def test_init_with_gateway_marker_attaches_immediately(monkeypatch):
    buf = Buf(protocol.MAP_SIZE)
    mem = SharedMemory(buf)
    mem.write_coordinator_marker(RUN)
    mod, rows = boot(monkeypatch, buf)
    mod.init(Context())
    assert rows == ['scheduled'] and mem.scheduled(RUN) and mod._active() is not None


def test_waiting_executor_withdraws_stale_scheduled_marker_and_heartbeat(monkeypatch):
    buf = Buf(protocol.MAP_SIZE)
    mem = SharedMemory(buf)
    done = b'QSYS_QMT_SIGNAL_SCHEDULED:' + RUN.encode()
    buf[protocol.SCHEDULED_MARKER_OFFSET:protocol.SCHEDULED_MARKER_OFFSET + len(done)] = done
    buf[protocol.EXECUTOR_OFFSET + 32:protocol.EXECUTOR_OFFSET + 40] = (123).to_bytes(8, 'little')
    mod, rows = boot(monkeypatch, buf)
    mod.init(Context())
    assert not mem.scheduled(RUN) and mem.executor_state().heartbeat_ms == 0


def test_stop_while_waiting_never_attaches(monkeypatch):
    buf = Buf(protocol.MAP_SIZE)
    mem = SharedMemory(buf)
    mod, rows = boot(monkeypatch, buf)
    c = Context()
    mod.init(c)
    mod.stop(c)
    assert ('cancel_schedule_run', 'qsys601s_' + RUN) in c.calls and rows[-1] == 'stopped'
    mem.write_coordinator_marker(RUN)
    mod.on_timer(c)
    assert mod._active() is None and not mem.scheduled(RUN)
