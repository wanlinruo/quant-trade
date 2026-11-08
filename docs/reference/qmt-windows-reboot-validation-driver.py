"""Default-off #601 Windows-reboot validation driver.

The surrounding coordinator creates the private named mapping and is the only
writer of ``first-go.json``.  This driver publishes readiness, waits for the
QMT model's run-bound shared-memory schedule marker, and never sends before the
coordinator gate exists.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import mmap
import os
import sys
import time
from pathlib import Path


audit = Path(__file__).parent
sys.path.insert(0, str(audit / 'candidate.zip'))
from qsys.agent.async_dispatch import open_validation_dispatcher
from qsys.agent.windows_validation import (
    prepare_windows_validation_parent,
    prepare_windows_validation_root,
)
from qsys.live.config import StreamConfig
from qsys.live.messages import OrderCommand
from qsys.live.transport import MemoryTransport
from qsys.live.validation_transport import ValidationTransport


spec = importlib.util.spec_from_file_location(
    'synthetic_port', audit / 'runtime' / 'qmt-synthetic-intent-port.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def write(name, value):
    with (audit / name).open('x', encoding='utf-8') as file:
        json.dump(value, file)
        file.flush()
        os.fsync(file.fileno())


write('first-started.json', dict(pid=os.getpid(), at=datetime.now(timezone.utc).isoformat()))
run = json.loads((audit / 'fixture.json').read_text(encoding='utf-8-sig'))['first']
namespace = 'component-validation:synthetic-intent'
parent = audit / 'qsys-async-validation-parent-synthetic'
root = parent / 'qsys-async-validation-synthetic'
prepare_windows_validation_parent(parent, enabled=True)
prepare_windows_validation_root(root, namespace, enabled=True)
transport = ValidationTransport(MemoryTransport(), namespace)
options = dict(
    enabled=True,
    root=root,
    transport=transport,
    streams=StreamConfig(),
    account_scope='validation:synthetic-intent',
    session_id='synthetic-session',
    strategy_id='diagnostic-only',
    clock=lambda: datetime.now(timezone.utc),
)
now = datetime.now(timezone.utc)
command = OrderCommand(
    schema_version=2,
    session_id='synthetic-session',
    strategy_id='diagnostic-only',
    client_order_id='synthetic-qsys-thin-validation-reboot',
    symbol='600036.SH',
    side='buy',
    qty=100,
    order_type='limit',
    limit_price=10.0,
    created_at=now,
    expires_at=now + timedelta(minutes=5),
)
memory = mmap.mmap(
    -1, 8192, tagname='Local\\qsys_synthetic_intent_' + run, access=mmap.ACCESS_WRITE)
memory.write(bytes(8192))
memory.seek(6144)
memory.write(b'QSYS_SYNTHETIC_INTENT_ONLY:' + run.encode())
port = P.SyntheticIntentPort.open_windows(run, enabled=True, timeout=5)
with open_validation_dispatcher(**options, adapter_factory=lambda: port) as dispatcher:
    write('first-ready.json', dict(
        run=run, pid=os.getpid(), expires_at=command.expires_at.isoformat()))
    port.wait_scheduled(timeout=180)
    write('model-scheduled.json', dict(
        run=run, observed_at=datetime.now(timezone.utc).isoformat(),
        evidence='run-bound-shared-memory-marker'))
    deadline = time.monotonic() + 120
    while not (audit / 'first-go.json').exists():
        if time.monotonic() > deadline:
            raise TimeoutError('No coordinator send gate; not sent')
        time.sleep(.1)
    go = json.loads((audit / 'first-go.json').read_text(encoding='utf-8-sig'))
    if go['run'] != run or go.get('model_scheduled_sha256') != hashlib.sha256(
            (audit / 'model-scheduled.json').read_bytes()).hexdigest():
        raise ValueError('Run/scheduled evidence binding')
    dispatcher.dispatch(command)
    first = dispatcher.store.read(command.client_order_id)
    assert first['state'] == 'transport_received' and len(port.receipts) == 1
    response = port.receipts[0]['response']
    assert response['executed'] is False and response['authorizes_trading'] is False
    assert response['command_sha256'] == first['command_sha256']
    assert response['attempt_id'] == first['attempt_id']
    assert response['session_nonce'] == first['session_nonce']
memory.seek(0)
before = hashlib.sha256(memory.read(8192)).hexdigest()
second = P.SyntheticIntentPort.open_windows(run, enabled=True, timeout=5)
with open_validation_dispatcher(**options, adapter_factory=lambda: second) as dispatcher:
    assert datetime.now(timezone.utc) < command.expires_at
    dispatcher.dispatch(command)
    restored = dispatcher.store.read(command.client_order_id)
    assert restored['state'] == 'unknown' and second.receipts == []
    for field in ('attempt_id', 'session_nonce', 'command_sha256',
                  'command_json', 'receipt_json'):
        assert first[field] == restored[field]
memory.seek(0)
assert hashlib.sha256(memory.read(8192)).hexdigest() == before
memory.close()
write('first-result.json', dict(
    status='success',
    run=run,
    receipts=port.receipts,
    first_row=first,
    restored=restored,
    old_intent_republished=0,
    broker_calls=0,
    account_binding=False,
    qmt_client_restarted=True,
    windows_restarted=True,
))
