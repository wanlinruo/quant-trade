from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from qsys.agent.async_dispatch import DispatchEnvelope, open_validation_dispatcher
from qsys.agent.dispatch_store import canonical_command
from qsys.live.config import StreamConfig
from qsys.live.messages import OrderCommand
from qsys.live.transport import MemoryTransport
from qsys.live.validation_transport import ValidationTransport

ROOT = Path(__file__).resolve().parents[2]/'docs/reference'
def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/name)
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj
P=load('qmt-synthetic-intent-port.py')
Q=P.protocol
RUN='a'*32
NOW=datetime(2026,11,5,4,tzinfo=timezone.utc)
MS=int(NOW.timestamp()*1000)


def command(**changes):
    values=dict(schema_version=2,session_id='synthetic-session',strategy_id='diagnostic-only',
        client_order_id='synthetic-qsys-intent-one',symbol='600036.SH',side='buy',qty=100,
        order_type='limit',limit_price=10.0,created_at=NOW,expires_at=NOW+timedelta(minutes=5))
    values.update(changes)
    return OrderCommand(**values)


def env(cmd=None):
    raw=canonical_command(cmd or command())
    return DispatchEnvelope(raw,hashlib.sha256(raw.encode()).hexdigest(),'b'*32,'c'*32)


def frame(envelope=None, **changes):
    e=envelope or env()
    value=dict(protocol=Q.PROTOCOL,scope=Q.SCOPE,kind='validate_synthetic_intent',run_id=RUN,
        seq=1,attempt_id=e.attempt_id,session_nonce=e.session_nonce,command_sha256=e.command_sha256,
        command_json=e.command_json,issued_at_ms=MS,expires_at_ms=MS+5000)
    value.update(changes);value['mac']=Q.sign(value)
    return Q.canonical(value)


def reply(raw, receiver=None, now=MS):
    return Q.decode((receiver or Q.Receiver(RUN)).receive(raw,now).decode())


def test_exact_intent_binding_and_nonexecution_are_returned():
    assert Q.COMMAND_FIELDS==set(OrderCommand.model_fields)
    e=env();r=Q.Receiver(RUN);raw=frame(e);result=reply(raw,r)
    assert result['status']=='validated_synthetic'
    assert result['executed'] is False and result['authorizes_trading'] is False
    for name in ('attempt_id','session_nonce','command_sha256'):
        assert result[name]==getattr(e,name)
    assert r.receive(raw,MS)==Q.canonical(result)
    assert r.accepted==1
    assert reply(frame(attempt_id='d'*32),r)['reason']=='intent_conflict'
    assert r.accepted==1


@pytest.mark.parametrize('change,reason',[(dict(run_id='d'*32),'binding_scope'),
    (dict(kind='place_order'),'binding_scope'),(dict(scope='production'),'binding_scope'),
    (dict(seq=True),'identity'),(dict(attempt_id='bad'),'identity'),
    (dict(session_nonce='bad'),'identity'),(dict(command_sha256='d'*64),'command_sha'),
    (dict(expires_at_ms=MS),'transport_expired'),(dict(issued_at_ms=MS+1),'transport_expired'),
    (dict(expires_at_ms=MS+5001),'transport_expired'),(dict(seq=2),'sequence_capacity')])
def test_valid_mac_does_not_bypass_binding_time_or_sequence(change,reason):
    assert reply(frame(**change))['reason']==reason


@pytest.mark.parametrize('changes',[dict(symbol='920000.BJ'),dict(strategy_id='s6'),
    dict(session_id='real-session'),dict(client_order_id='real-order'),dict(qty=200),
    dict(limit_price=9.9),dict(side='sell'),dict(schema_version=1),
    dict(created_at=NOW.replace(tzinfo=None),expires_at=(NOW+timedelta(minutes=5)).replace(tzinfo=None)),
    dict(expires_at=NOW)])
def test_nonfixture_and_expired_command_content_is_not_validated(changes):
    r=Q.Receiver(RUN);result=reply(frame(env(command(**changes))),r)
    assert result['status']=='rejected' and result['executed'] is False
    assert r.accepted==0 and r.seen=={}


@pytest.mark.parametrize('key,value',[('qty',True),('schema_version',True),('limit_price',10),
    ('expires_at',None),('account','do-not-accept-extra-fields')])
def test_wire_values_are_never_coerced_or_extra_fields_ignored(key,value):
    obj=command().model_dump(mode='json');obj[key]=value
    raw=Q.canonical(obj).decode()
    assert reply(frame(command_json=raw,command_sha256=hashlib.sha256(raw.encode()).hexdigest()))['status']=='rejected'


def test_mac_duplicates_and_nonfinite_json_cannot_be_accepted():
    value=Q.decode(frame().decode());value['mac']='0'*64
    assert reply(Q.canonical(value))['reason']=='signature'
    with pytest.raises(ValueError,match='duplicate'):
        reply(b'{"seq":1,'+frame()[1:])
    with pytest.raises(ValueError,match='nonfinite'):
        reply(frame().replace(b'"seq":1',b'"seq":NaN'))
    with pytest.raises(ValueError):
        Q.Receiver(RUN,key=bytes([18]*32))


def test_receiver_has_no_codec_or_authenticator_extension_hooks():
    with pytest.raises(TypeError):
        Q.Receiver(RUN, codec=json)
    with pytest.raises(TypeError):
        Q.Receiver(RUN, authenticator=lambda _: True)


class Memory(io.BytesIO):
    def __init__(self,mutate=None,absent=False):
        super().__init__(bytes(8192));self.receiver=Q.Receiver(RUN)
        self.mutate,self.absent,self.packets=mutate,absent,[]
        self.seek(6144);super().write(b'QSYS_SYNTHETIC_INTENT_ONLY:'+RUN.encode())
    def write(self,raw):
        pos=self.tell();result=super().write(raw)
        if pos==0 and raw!=bytes(4):
            generation=int.from_bytes(raw,'little');value=self.getvalue()
            request=value[8:8+int.from_bytes(value[4:8],'little')];self.packets.append(Q.decode(request.decode()))
            if not self.absent:
                response=self.receiver.receive(request,int(time.time()*1000))
                if self.mutate:
                    obj=Q.decode(response.decode());self.mutate(obj);response=Q.canonical(obj)
                self.seek(4096);super().write(generation.to_bytes(4,'little')+len(response).to_bytes(4,'little')+response)
        return result


class QmtMemory(io.BytesIO):
    def __init__(self):
        super().__init__(bytes(8192))
        self.callback = None
        self.close_calls = 0
        self.seek(6144)
        super().write(b'QSYS_SYNTHETIC_INTENT_ONLY:'+RUN.encode())

    def write(self, raw):
        pos = self.tell()
        result = super().write(raw)
        if pos == 0 and raw != bytes(4) and self.callback is not None:
            self.callback()
        return result

    def close(self):
        # QMT and the external port have separate handles to the same named
        # mapping. Closing either handle must not invalidate the other view.
        self.close_calls += 1


class Context:
    def __init__(self):
        self.scheduled = []
        self.cancelled = []

    def schedule_run(self, *args):
        self.scheduled.append(args)
        return 'synthetic-task'

    def cancel_schedule_run(self, group):
        self.cancelled.append(group)
        return True


def fresh():
    now=datetime.now(timezone.utc)
    return env(command(created_at=now,expires_at=now+timedelta(minutes=5)))


def test_qmt_schedule_receiver_validates_full_intent_without_trading(monkeypatch, capsys):
    memory = QmtMemory()
    context = Context()
    fake_mmap = SimpleNamespace(ACCESS_WRITE=1, mmap=lambda *args, **kwargs: memory)
    monkeypatch.setitem(sys.modules, 'mmap', fake_mmap)
    monkeypatch.setattr(Q, 'RUN_ID', RUN)
    monkeypatch.setattr(Q, 'PROBE_DATE', datetime.now().strftime('%Y%m%d'))
    monkeypatch.setattr(Q, 'CLIENT_BUILD', '2.1.33.0')
    monkeypatch.setattr(Q, '_STATE', None)
    monkeypatch.setattr(Q, '_RAN', False)

    Q.init(context)
    assert len(context.scheduled) == 1
    assert context.scheduled[0][-1] == 'qsys602_' + RUN
    memory.callback = lambda: Q.on_timer(context)
    port = P.SyntheticIntentPort(memory, RUN, enabled=True)
    envelope = fresh()

    ack = port.send(envelope)

    assert ack.command_sha256 == envelope.command_sha256
    assert Q._STATE['receiver'].accepted == 1
    assert Q._STATE['packets'] == 1 and Q._STATE['stopped'] is True
    assert context.cancelled == ['qsys602_' + RUN]
    output = capsys.readouterr().out
    assert "'trading_calls': 0" in output
    assert "'account_calls': 0" in output
    assert "'executed': False" in output
    assert "'authorizes_trading': False" in output


def test_qmt_schedule_receiver_requires_existing_marker(monkeypatch, capsys):
    memory = QmtMemory()
    memory.seek(6144)
    memory.write(bytes(64))
    context = Context()
    monkeypatch.setitem(sys.modules, 'mmap', SimpleNamespace(
        ACCESS_WRITE=1, mmap=lambda *args, **kwargs: memory))
    monkeypatch.setattr(Q, 'RUN_ID', RUN)
    monkeypatch.setattr(Q, 'PROBE_DATE', datetime.now().strftime('%Y%m%d'))
    monkeypatch.setattr(Q, 'CLIENT_BUILD', '2.1.33.0')
    monkeypatch.setattr(Q, '_STATE', None)
    monkeypatch.setattr(Q, '_RAN', False)

    Q.init(context)

    assert context.scheduled == []
    assert Q._STATE is None
    assert "'status': 'init_failed'" in capsys.readouterr().out


def test_qmt_receiver_source_has_no_account_market_or_order_interfaces():
    source = (ROOT/'qmt-synthetic-intent-protocol.py').read_text(encoding='utf-8')
    for forbidden in ('passorder', 'get_trade_detail_data', 'get_history_trade_detail_data',
                      'socket', 'requests', 'win32pipe', 'win32file'):
        assert forbidden not in source


def test_port_transmits_complete_canonical_command_and_binds_native_ack():
    m=Memory();p=P.SyntheticIntentPort(m,RUN,enabled=True);e=fresh();ack=p.send(e)
    assert m.packets[0]['command_json']==e.command_json
    assert ack.command_sha256==e.command_sha256 and ack.session_nonce==e.session_nonce
    assert p.receipts[0]['response']['executed'] is False
    with pytest.raises(ValueError,match='duplicate'):p.send(e)
    p.close()


@pytest.mark.parametrize('field,value',[('executed',True),('authorizes_trading',True),
    ('command_sha256','d'*64),('session_nonce','d'*32),('attempt_id','d'*32),('run_id','d'*32)])
def test_even_signed_wrong_or_executing_reply_is_not_an_ack(field,value):
    def mutate(obj):
        obj[field]=value;obj.pop('mac');obj['mac']=Q.sign(obj)
    p=P.SyntheticIntentPort(Memory(mutate),RUN,enabled=True)
    with pytest.raises(ValueError,match='non-execution'):p.send(fresh())
    assert p.receipts==[]


def test_disabled_invalid_hash_and_timeout_do_not_retry():
    with pytest.raises(ValueError):P.SyntheticIntentPort(Memory(),RUN)
    m=Memory(absent=True);p=P.SyntheticIntentPort(m,RUN,enabled=True,timeout=.02);e=fresh()
    with pytest.raises(ValueError,match='SHA'):p.send(replace(e,command_sha256='f'*64))
    assert m.packets==[]
    with pytest.raises(TimeoutError):p.send(e)
    with pytest.raises(ValueError,match='duplicate'):p.send(e)
    assert len(m.packets)==1


def test_actual_ledger_keeps_validation_ack_unknown_on_restart(tmp_path):
    ns='component-validation:synthetic-intent';root=tmp_path/'qsys-async-validation-intent'
    root.mkdir(mode=0o700);(root/'.qsys-async-validation').write_text(ns)
    opts=dict(enabled=True,root=root,transport=ValidationTransport(MemoryTransport(),ns),
        streams=StreamConfig(),account_scope='validation:synthetic',strategy_id='diagnostic-only',
        session_id='synthetic-session',clock=lambda:datetime.now(timezone.utc))
    e=fresh();cmd=OrderCommand.model_validate_json(e.command_json)
    p=P.SyntheticIntentPort(Memory(),RUN,enabled=True)
    with open_validation_dispatcher(**opts,adapter_factory=lambda:p) as d:
        d.dispatch(cmd);before=d.store.read(cmd.client_order_id)
        assert before['state']=='transport_received'
    m=Memory();p=P.SyntheticIntentPort(m,RUN,enabled=True)
    with open_validation_dispatcher(**opts,adapter_factory=lambda:p) as d:
        d.dispatch(cmd);after=d.store.read(cmd.client_order_id)
        assert after['state']=='unknown' and after['receipt_json']==before['receipt_json']
        assert m.packets==[]
