"""Full fixed synthetic intent port. Diagnostic ACK is NEVER broker acceptance."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import mmap
import os
from pathlib import Path
import time

from qsys.agent.async_dispatch import DispatchAcknowledgement

_path = Path(__file__).with_name('qmt-synthetic-intent-protocol.py')
_spec = importlib.util.spec_from_file_location('synthetic_intent_protocol', _path)
protocol = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(protocol)


class SyntheticIntentPort:
    def __init__(self, memory, run_id, *, enabled=False, timeout=3.0):
        if enabled is not True or not protocol.valid_id(run_id) or not 0 < timeout <= 5:
            raise ValueError('explicit bounded synthetic intent port required')
        memory.seek(6144)
        marker = b'QSYS_SYNTHETIC_INTENT_ONLY:' + run_id.encode('ascii')
        if memory.read(len(marker)) != marker:
            raise ValueError('existing synthetic coordinator marker required')
        self.memory, self.run_id, self.timeout = memory, run_id, timeout
        self.seq, self.closed = 0, False
        self.attempts, self.receipts = set(), []

    @classmethod
    def open_windows(cls, run_id, *, enabled=False, timeout=3.0):
        if enabled is not True or os.name != 'nt' or not protocol.valid_id(run_id):
            raise ValueError('explicit Windows synthetic port required')
        memory = mmap.mmap(-1, 8192, tagname='Local\\qsys_synthetic_intent_' + run_id,
                           access=mmap.ACCESS_WRITE)
        try:
            return cls(memory, run_id, enabled=True, timeout=timeout)
        except BaseException:
            memory.close()
            raise

    def send(self, envelope):
        if self.closed or self.seq >= 8 or envelope.attempt_id in self.attempts:
            raise ValueError('synthetic port closed, exhausted or duplicate attempt')
        now = int(time.time() * 1000)
        protocol.command(envelope.command_json, now)
        if (not protocol.valid_id(envelope.attempt_id) or not protocol.valid_id(envelope.session_nonce)
                or hashlib.sha256(envelope.command_json.encode('ascii')).hexdigest() != envelope.command_sha256):
            raise ValueError('synthetic envelope identity/SHA')
        value = dict(protocol=protocol.PROTOCOL, scope=protocol.SCOPE, kind='validate_synthetic_intent',
            run_id=self.run_id, seq=self.seq+1, attempt_id=envelope.attempt_id,
            session_nonce=envelope.session_nonce, command_sha256=envelope.command_sha256,
            command_json=envelope.command_json, issued_at_ms=now, expires_at_ms=now+5000)
        value['mac'] = protocol.sign(value)
        raw = protocol.canonical(value)
        if len(raw) > 4000:
            raise ValueError('synthetic request size')
        self.seq += 1
        self.attempts.add(envelope.attempt_id)
        m = self.memory
        m.seek(0); m.write(bytes(4))
        m.seek(8); m.write(raw)
        m.seek(4); m.write(len(raw).to_bytes(4, 'little'))
        m.seek(0); m.write(self.seq.to_bytes(4, 'little'))
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            m.seek(4096)
            generation, size = int.from_bytes(m.read(4),'little'), int.from_bytes(m.read(4),'little')
            if generation != self.seq:
                time.sleep(0.01)  # External process only, never QMT callback code.
                continue
            if not 1 <= size <= 2000:
                raise ValueError('synthetic response size')
            response = m.read(size)
            m.seek(4096)
            if int.from_bytes(m.read(4),'little') != generation:
                continue
            self.validate_reply(response, envelope)
            self.receipts.append(dict(request=value, response=protocol.decode(response.decode('ascii')),
                request_sha256=hashlib.sha256(raw).hexdigest(),
                response_sha256=hashlib.sha256(response).hexdigest()))
            return DispatchAcknowledgement(command_sha256=envelope.command_sha256,
                attempt_id=envelope.attempt_id, session_nonce=envelope.session_nonce,
                receiver_instance='validated-synthetic:' + self.run_id, received_at=datetime.now(timezone.utc))
        raise TimeoutError('synthetic validation response absent; no retry')

    def scheduled(self):
        """Return whether the bound QMT model accepted its finite schedule."""
        if self.closed:
            raise ValueError('synthetic port closed')
        self.memory.seek(protocol.SCHEDULED_MARKER_OFFSET)
        marker = protocol.scheduled_marker(self.run_id)
        return self.memory.read(len(marker)) == marker

    def wait_scheduled(self, *, timeout=120.0):
        """Wait read-only for one run-bound marker; never sends or retries a packet."""
        if type(timeout) not in (int, float) or type(timeout) is bool or not 0 < timeout <= 180:
            raise ValueError('bounded scheduled marker wait required')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.scheduled():
                return True
            time.sleep(0.05)
        raise TimeoutError('QMT scheduled marker absent; no send')

    def validate_reply(self, raw, envelope):
        value = protocol.decode(raw.decode('ascii'))
        fields = {'protocol','run_id','seq','attempt_id','session_nonce','command_sha256',
                  'status','reason','executed','authorizes_trading','accepted_count','mac'}
        if type(value) is not dict or set(value) != fields:
            raise ValueError('synthetic response fields')
        mac = value.pop('mac')
        if not protocol.valid_sha(mac) or not protocol.hmac.compare_digest(mac, protocol.sign(value)):
            raise ValueError('synthetic response signature')
        if (value['protocol'] != protocol.PROTOCOL or value['run_id'] != self.run_id
                or type(value['seq']) is not int or value['seq'] != self.seq
                or value['attempt_id'] != envelope.attempt_id or value['session_nonce'] != envelope.session_nonce
                or value['command_sha256'] != envelope.command_sha256
                or value['status'] != 'validated_synthetic' or value['reason'] != 'ok'
                or value['executed'] is not False or value['authorizes_trading'] is not False
                or type(value['accepted_count']) is not int or value['accepted_count'] != self.seq):
            raise ValueError('synthetic response binding/non-execution mismatch')

    def close(self):
        if not self.closed:
            self.closed = True
            self.memory.close()
