import pytest

from qsys.agent.qmt import protocol
from qsys.agent.qmt.memory import ReportGap, SharedMemory
from qmt_fake import FakeQmtApi, load_executor

RUN = 'a' * 32
KEY = bytes(range(32))


def pair(clock=lambda: 1_000):
    buf = bytearray(protocol.MAP_SIZE)
    mem = SharedMemory(buf)
    mem.beat(clock())
    mod = load_executor(ACCOUNT_ID='100000001')
    ex = mod.Executor(RUN, KEY, FakeQmtApi().as_api(), buf, clock)
    return mem, ex, buf


def test_exchange_returns_only_matching_generation():
    mem, ex, buf = pair()
    ticks = []

    def sleep(_):
        ticks.append(1)
        ex.tick(None)
    raw = protocol.build_request(run_id=RUN, seq=1, kind='ping', command={}, attempt_id='b' * 32,
                                 session_nonce='c' * 32, now_ms=1_000, key=KEY)
    reply = mem.exchange(raw, timeout_s=1.0, sleep=sleep)
    assert protocol.parse_receipt(reply, KEY)['status'] == 'accepted'
    # a stale response from the previous generation is never returned for a new request
    raw2 = protocol.build_request(run_id=RUN, seq=2, kind='ping', command={}, attempt_id='d' * 32,
                                  session_nonce='c' * 32, now_ms=1_000, key=KEY)
    assert mem.exchange(raw2, timeout_s=0.0, sleep=lambda _: None) is None


def test_read_reports_advances_cursor_once():
    mem, ex, buf = pair()
    api = FakeQmtApi()
    ex.record_order('callback', api.order('q261109001abcdef012345'))
    ex.record_order('callback', api.order('q261109002abcdef012345'))
    rows = mem.read_reports()
    assert [r['m_strRemark'] for r in rows] == ['q261109001abcdef012345', 'q261109002abcdef012345']
    assert mem.read_reports() == []


def test_executor_restart_resets_cursor_and_new_epoch_is_visible():
    mem, ex, buf = pair()
    api = FakeQmtApi()
    ex.record_order('callback', api.order('q261109001abcdef012345'))
    mem.read_reports()
    first = mem.executor_state().epoch
    mod = load_executor(ACCOUNT_ID='100000001')
    ex2 = mod.Executor(RUN, KEY, api.as_api(), buf, lambda: 2_000)
    ex2.record_order('callback', api.order('q261109003abcdef012345'))
    assert mem.executor_state().epoch != first
    assert [r['m_strRemark'] for r in mem.read_reports()] == ['q261109003abcdef012345']


def test_reports_overwritten_beyond_ring_are_detected():
    mem, ex, buf = pair()
    slot = protocol.RING_OFFSET + 1 * protocol.RING_SLOT_SIZE
    buf[protocol.EXECUTOR_OFFSET + 40:protocol.EXECUTOR_OFFSET + 48] = (1).to_bytes(8, 'little')
    buf[slot + 4:slot + 12] = (241).to_bytes(8, 'little')
    with pytest.raises(ReportGap):
        mem.read_reports()


def test_kill_and_beat_words_visible_to_executor_reader():
    mem, ex, buf = pair()
    mem.set_kill(True)
    assert ex.kill_engaged()
    mem.set_kill(False)
    assert not ex.kill_engaged()
    mem.beat(50_000)
    assert not ex.gateway_stale(55_000) and ex.gateway_stale(60_001)


def test_markers():
    mem, ex, buf = pair()
    mem.write_coordinator_marker(RUN)
    assert bytes(buf[protocol.COORDINATOR_MARKER_OFFSET:protocol.COORDINATOR_MARKER_OFFSET + 16]) == b'QSYS_QMT_SIGNAL:'
    assert not mem.scheduled(RUN)
    done = b'QSYS_QMT_SIGNAL_SCHEDULED:' + RUN.encode()
    buf[protocol.SCHEDULED_MARKER_OFFSET:protocol.SCHEDULED_MARKER_OFFSET + len(done)] = done
    assert mem.scheduled(RUN)
