from datetime import datetime, timedelta

import pytest

from qsys.agent.async_dispatch import DispatchAcknowledgement, open_qmt_dispatcher
from qsys.live.config import StreamConfig
from qsys.live.messages import DispatchStatusEvent, OrderCommand
from qsys.live.transport import MemoryTransport

T = datetime(2026, 11, 9, 9, 25, 10)
S = StreamConfig()
SCOPE = 'qmt:0123456789abcdef'


class Port:
    def __init__(self):
        self.sent = []

    def send(self, envelope):
        self.sent.append(envelope)
        return DispatchAcknowledgement(command_sha256=envelope.command_sha256,
                                       attempt_id=envelope.attempt_id,
                                       session_nonce=envelope.session_nonce,
                                       receiver_instance='qmt:x', received_at=T)

    def close(self):
        pass


@pytest.fixture
def root(tmp_path):
    value = tmp_path / 'qsys-qmt-dispatch-live'
    value.mkdir(mode=0o700)
    (value / '.qsys-qmt-dispatch').write_text(SCOPE)
    return value


def options(root, transport, port, **changes):
    values = dict(enabled=True, root=root, transport=transport, streams=S, port=port,
                  account_scope=SCOPE, strategy_id='structural',
                  session_of=lambda: T.date().isoformat(), clock=lambda: T + timedelta(seconds=1))
    values.update(changes)
    return values


def cmd():
    return OrderCommand(schema_version=2, client_order_id='q261109001abcdef012345',
                        session_id=T.date().isoformat(), strategy_id='structural',
                        symbol='600036.SH', side='buy', qty=100, order_type='limit',
                        limit_price=10.0, created_at=T, expires_at=T + timedelta(seconds=60))


def test_qmt_factory_requires_explicit_enable(root):
    with pytest.raises(ValueError, match='disabled'):
        with open_qmt_dispatcher(**options(root, MemoryTransport(), Port(), enabled=False)):
            pass


@pytest.mark.parametrize('mutate', ['mode', 'name', 'marker', 'scope'])
def test_qmt_factory_rejects_shared_or_unbound_root(tmp_path, root, mutate):
    kwargs = options(root, MemoryTransport(), Port())
    if mutate == 'mode':
        root.chmod(0o755)
    elif mutate == 'name':
        other = tmp_path / 'shared'
        root.rename(other)
        kwargs['root'] = other
    elif mutate == 'marker':
        (root / '.qsys-qmt-dispatch').write_text('qmt:ffffffffffffffff')
    else:
        kwargs['account_scope'] = 'validation:account'
    with pytest.raises(ValueError):
        with open_qmt_dispatcher(**kwargs):
            pass


def test_qmt_factory_uses_real_transport_and_daily_session(root):
    transport, port = MemoryTransport(), Port()
    with open_qmt_dispatcher(**options(root, transport, port)) as dispatcher:
        dispatcher.dispatch(cmd())
    states = [DispatchStatusEvent.from_payload(m.payload).state
              for m in transport.all_messages(S.dispatch_status)]
    assert states == ['dispatching', 'transport_received'] and len(port.sent) == 1


def test_qmt_factory_session_follows_session_of(root):
    transport, port = MemoryTransport(), Port()
    day = {'v': '2026-11-08'}
    with open_qmt_dispatcher(**options(root, transport, port, session_of=lambda: day['v'])) as d:
        from qsys.agent.async_dispatch import PreDispatchRejection
        with pytest.raises(PreDispatchRejection):
            d.dispatch(cmd())
        day['v'] = T.date().isoformat()
        d.dispatch(cmd().model_copy(update={'client_order_id': 'q261109002abcdef012345'}))
    assert len(port.sent) == 1
