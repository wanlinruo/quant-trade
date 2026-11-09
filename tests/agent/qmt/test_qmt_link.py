from qsys.agent.qmt import protocol
from qsys.agent.qmt.link import QmtLink
from qsys.agent.qmt.memory import SharedMemory
from qmt_fake import ACCOUNT, FakeQmtApi, load_executor


class Reconciler:
    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def run_once(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError('query')
        return []


def test_kill_sync_sets_word_and_executor_cancels_once():
    buf = bytearray(protocol.MAP_SIZE)
    mem = SharedMemory(buf)
    api = FakeQmtApi()
    api.order('q261109001abcdef012345', status=50)
    ex = load_executor(ACCOUNT_ID=ACCOUNT).Executor('a' * 32, bytes(32), api.as_api(), buf,
                                                    lambda: 5_000)
    rec = Reconciler()
    link = QmtLink(mem, rec, clock_ms=lambda: 5_000)
    link(True)
    ex.tick(None)
    ex.tick(None)
    assert [c[0] for c in api.calls] == ['cancel'] and rec.calls == 1
    assert not ex.gateway_stale(5_000)
    link(False)
    assert not ex.kill_engaged()


def test_reconciler_failure_does_not_stop_heartbeat():
    mem = SharedMemory(bytearray(protocol.MAP_SIZE))
    link = QmtLink(mem, Reconciler(fail=True), clock_ms=lambda: 7_000)
    link(False)
    assert mem.buf[protocol.CONTROL_OFFSET:protocol.CONTROL_OFFSET + 8] == (7_000).to_bytes(8, 'little')
