"""#601 synthetic ping port for AsyncOrderDispatcher; NEVER an order adapter.

Only the fixed synthetic fixture is accepted. No market/account/order fields
are sent to QMT; the ACK means this ping was echoed, not that an order arrived.
Public HMAC fixture tests integrity/correlation, not production authentication.
"""
from datetime import datetime
import hashlib
import hmac
import json
import mmap
import os
import re
import time

from qsys.agent.async_dispatch import DispatchAcknowledgement
from qsys.agent.dispatch_store import canonical_command
from qsys.live.messages import OrderCommand

PROTOCOL = 'qsys.external-queue-diagnostic.v1'
KEY = bytes([17] * 32)  # Public diagnostic fixture, never a credential.


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate diagnostic key')
        result[key] = value
    return result


def signature(value):
    return hmac.new(KEY, canonical(value), 'sha256').hexdigest()


def valid_id(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value) is not None


def synthetic(envelope):
    if not valid_id(envelope.attempt_id) or not valid_id(envelope.session_nonce):
        raise ValueError('explicit diagnostic attempt/nonce required')
    raw = envelope.command_json
    if hashlib.sha256(raw.encode('ascii')).hexdigest() != envelope.command_sha256:
        raise ValueError('diagnostic envelope SHA mismatch')
    value = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(value, dict) or set(value) != set(OrderCommand.model_fields):
        raise ValueError('explicit synthetic intent fields required')
    command = OrderCommand.model_validate(value)
    if (canonical_command(command) != raw or command.schema_version != 2
            or command.session_id != 'synthetic-session' or command.strategy_id != 'diagnostic-only'
            or not re.fullmatch(r'synthetic-qsys601-[a-z0-9_-]{1,64}', command.client_order_id)
            or command.symbol != '600036.SH' or command.side != 'buy'
            or command.qty != 100 or command.order_type != 'limit' or command.limit_price != 10.0
            or command.expires_at is None):
        raise ValueError('only the fixed synthetic diagnostic intent is accepted')
    return command


class DiagnosticPingPort:
    def __init__(self, memory, run_id, *, enabled=False, timeout=3.0):
        if enabled is not True or not valid_id(run_id) or not 0 < timeout <= 5:
            raise ValueError('explicit bounded diagnostic port required')
        memory.seek(6144)
        marker = b'QSYS599_RECOVERY_ONLY:' + run_id.encode('ascii')
        if memory.read(len(marker)) != marker:
            raise ValueError('existing coordinator marker required')
        self.memory, self.run_id, self.timeout = memory, run_id, timeout
        self.seq = 0
        self.closed = False
        self.attempts = set()
        self.receipts = []

    @classmethod
    def open_windows(cls, run_id, *, enabled=False, timeout=3.0):
        if enabled is not True or os.name != 'nt' or not valid_id(run_id):
            raise ValueError('explicit Windows diagnostic port required')
        memory = mmap.mmap(-1, 8192, tagname='Local\\qsys599_queue_' + run_id,
                           access=mmap.ACCESS_WRITE)
        try:
            return cls(memory, run_id, enabled=True, timeout=timeout)
        except BaseException:
            memory.close()
            raise

    def send(self, envelope):
        if self.closed or self.seq >= 8 or envelope.attempt_id in self.attempts:
            raise ValueError('diagnostic port closed, exhausted or duplicate attempt')
        synthetic(envelope)  # Must succeed before any write.
        issued = int(time.time() * 1000)
        self.seq += 1
        self.attempts.add(envelope.attempt_id)
        value = dict(protocol=PROTOCOL, run_id=self.run_id, request_id=envelope.attempt_id,
            seq=self.seq, kind='ping', payload='QSYS599_DIAGNOSTIC_ONLY',
            issued_at_ms=issued, expires_at_ms=issued + 5000)
        value['mac'] = signature(value)
        raw = canonical(value)
        m = self.memory
        m.seek(0); m.write(bytes(4))
        m.seek(8); m.write(raw)
        m.seek(4); m.write(len(raw).to_bytes(4, 'little'))
        m.seek(0); m.write(self.seq.to_bytes(4, 'little'))
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            m.seek(4096)
            generation = int.from_bytes(m.read(4), 'little')
            size = int.from_bytes(m.read(4), 'little')
            if generation != self.seq:
                time.sleep(0.01)  # External process only; never a QMT callback.
                continue
            if not 1 <= size <= 2000:
                raise ValueError('diagnostic response size')
            response = m.read(size)
            m.seek(4096)
            if int.from_bytes(m.read(4), 'little') != generation:
                continue
            self._validate(response, envelope)
            self.receipts.append(dict(response_sha256=hashlib.sha256(response).hexdigest(),
                request_sha256=hashlib.sha256(raw).hexdigest(), seq=self.seq,
                attempt_id=envelope.attempt_id, receiver_instance=self.run_id))
            return DispatchAcknowledgement(command_sha256=envelope.command_sha256,
                attempt_id=envelope.attempt_id, session_nonce=envelope.session_nonce,
                receiver_instance='diagnostic-ping:' + self.run_id, received_at=datetime.now())
        raise TimeoutError('diagnostic echo not observed; no retry')

    def _validate(self, raw, envelope):
        value = json.loads(raw.decode('ascii'), object_pairs_hook=unique_object)
        expected = {'protocol','run_id','request_id','seq','status','reason',
                    'accepted_count','authorizes_trading','mac'}
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError('diagnostic response fields')
        mac = value.pop('mac')
        if not isinstance(mac, str) or not hmac.compare_digest(mac, signature(value)):
            raise ValueError('diagnostic response signature')
        if (value['protocol'] != PROTOCOL or value['run_id'] != self.run_id
                or value['request_id'] != envelope.attempt_id or type(value['seq']) is not int
                or value['seq'] != self.seq or value['status'] != 'echo' or value['reason'] != 'ok'
                or value['authorizes_trading'] is not False
                or type(value['accepted_count']) is not int or value['accepted_count'] != self.seq):
            raise ValueError('diagnostic response binding/scope mismatch')

    def close(self):
        if not self.closed:
            self.closed = True
            self.memory.close()
