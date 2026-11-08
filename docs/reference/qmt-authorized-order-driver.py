"""External driver for one pre-authorized QMT order plan; #601.

Runs on the Windows node beside the session coordinator, which creates the
private named mapping and writes the coordinator marker first.  The driver:

* waits for the QMT executor's run-bound scheduled marker;
* sends each plan step exactly once, journaling (fsync) before sending;
* gates every next step on broker facts (order/deal callbacks or queries)
  reported by the executor, never on ``passorder`` returning;
* treats a missing transport reply as ``unknown``: it stops the sequence and
  only reconciles by read-only snapshots, never resending a step.

Importing this module has no side effects; ``main()`` runs the session.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
import uuid

PROTOCOL = 'qsys.authorized-order.v1'
SCOPE = 'execution:authorized-order'
MAP_SIZE = 65536
REQUEST_OFFSET = 0
RESPONSE_OFFSET = 4096
COORDINATOR_MARKER_OFFSET = 6144
SCHEDULED_MARKER_OFFSET = 7168
REPORT_HEADER_OFFSET = 8192
REPORT_SLOTS_OFFSET = 8200
REPORT_SLOT_SIZE = 1024
REPORT_LIMIT = 50
TERMINAL = {54, 56, 57}
RESTING_REACHED = {50, 54, 55, 56, 57}


class TransportUnknown(Exception):
    """No authenticated reply: the step's effect is unknown and must not be resent."""


class SequenceStop(Exception):
    def __init__(self, reason, **facts):
        super().__init__(reason)
        self.reason, self.facts = reason, facts


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def sign(value, key):
    return hmac.new(key, canonical(value), 'sha256').hexdigest()


def now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class Journal:
    def __init__(self, path):
        self.path = path

    def append(self, event, **facts):
        row = dict(at=datetime.now(timezone.utc).isoformat(), event=event, **facts)
        with open(self.path, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        return row


class Client:
    def __init__(self, memory, run_id, key, journal, clock=now_ms, sleep=time.sleep,
                 reply_timeout=5.0):
        self.memory, self.run_id, self.key, self.journal = memory, run_id, key, journal
        self.clock, self.sleep, self.reply_timeout = clock, sleep, reply_timeout
        self.session_nonce = uuid.uuid4().hex
        self.generation, self.report_index, self.snapshots = 0, 0, 0

    def marker_present(self, offset, marker):
        self.memory.seek(offset)
        return self.memory.read(len(marker)) == marker

    def wait_scheduled(self, timeout):
        marker = b'QSYS_ORDER_SCHEDULED:' + self.run_id.encode('ascii')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.marker_present(SCHEDULED_MARKER_OFFSET, marker):
                return True
            self.sleep(0.2)
        return False

    def send(self, kind, seq, command):
        command_json = canonical(command).decode('ascii')
        issued = self.clock()
        frame = dict(protocol=PROTOCOL, scope=SCOPE, kind=kind, run_id=self.run_id, seq=seq,
                     attempt_id=uuid.uuid4().hex, session_nonce=self.session_nonce,
                     command_sha256=hashlib.sha256(command_json.encode('ascii')).hexdigest(),
                     command_json=command_json, issued_at_ms=issued, expires_at_ms=issued + 4000)
        frame['mac'] = sign(frame, self.key)
        payload = canonical(frame)
        if len(payload) > 4000:
            raise ValueError('request frame too large')
        self.generation += 1
        self.journal.append('sending', kind=kind, seq=seq, attempt_id=frame['attempt_id'],
                            command_sha256=frame['command_sha256'], generation=self.generation)
        self.memory.seek(REQUEST_OFFSET + 4)
        self.memory.write(len(payload).to_bytes(4, 'little') + payload)
        self.memory.seek(REQUEST_OFFSET)
        self.memory.write(self.generation.to_bytes(4, 'little'))
        deadline = time.monotonic() + self.reply_timeout
        while time.monotonic() < deadline:
            self.memory.seek(RESPONSE_OFFSET)
            if int.from_bytes(self.memory.read(4), 'little') == self.generation:
                size = int.from_bytes(self.memory.read(4), 'little')
                if not 1 <= size <= 2000:
                    break
                raw = self.memory.read(size)
                reply = json.loads(raw)
                mac = reply.pop('mac', None)
                if (not isinstance(mac, str) or not hmac.compare_digest(mac, sign(reply, self.key))
                        or reply.get('attempt_id') != frame['attempt_id']):
                    break
                self.journal.append('reply', kind=kind, seq=seq, attempt_id=frame['attempt_id'],
                                    status=reply.get('status'), reason=reply.get('reason'),
                                    result=reply.get('result'))
                return reply
            self.sleep(0.05)
        self.journal.append('unknown', kind=kind, seq=seq, attempt_id=frame['attempt_id'])
        raise TransportUnknown(f'{kind}:{seq}')

    def read_reports(self):
        self.memory.seek(REPORT_HEADER_OFFSET)
        count = min(int.from_bytes(self.memory.read(4), 'little'), REPORT_LIMIT)
        overflow = int.from_bytes(self.memory.read(4), 'little')
        rows = []
        while self.report_index < count:
            self.memory.seek(REPORT_SLOTS_OFFSET + self.report_index * REPORT_SLOT_SIZE)
            size = int.from_bytes(self.memory.read(4), 'little')
            raw = self.memory.read(size) if 1 <= size <= REPORT_SLOT_SIZE - 4 else b'{}'
            row = json.loads(raw)
            self.journal.append('fact', index=self.report_index, fact=row)
            rows.append(row)
            self.report_index += 1
        if overflow:
            self.journal.append('report_overflow')
        return rows

    def snapshot(self):
        self.snapshots += 1
        return self.send('snapshot', self.snapshots, {'action': 'snapshot', 'n': self.snapshots})


class Facts:
    def __init__(self):
        self.status, self.deals, self.errors, self.positions = {}, {}, {}, []

    def absorb(self, rows):
        for row in rows:
            remark = row.get('m_strRemark')
            if row.get('kind') == 'order' and isinstance(row.get('m_nOrderStatus'), int):
                self.status[remark] = row['m_nOrderStatus']
            elif row.get('kind') == 'deal':
                self.deals.setdefault(remark, []).append(row)
            elif row.get('kind') == 'order_error':
                self.errors.setdefault(remark, []).append(row)
            elif row.get('kind') == 'position':
                self.positions.append(row)


def wait_for(client, facts, condition, timeout, sleep=time.sleep, snapshot_every=10.0):
    """Poll reported facts; request a read-only snapshot periodically."""
    deadline = time.monotonic() + timeout
    next_snapshot = time.monotonic() + snapshot_every
    while True:
        facts.absorb(client.read_reports())
        if condition():
            return True
        now = time.monotonic()
        if now >= deadline:
            client.snapshot()
            facts.absorb(client.read_reports())
            return condition()
        if now >= next_snapshot:
            client.snapshot()
            next_snapshot = now + snapshot_every
        sleep(0.2)


def run_plan(client, plan, expect, facts, sleep=time.sleep, resting_timeout=30.0,
             fill_timeout=60.0, cancel_timeout=30.0):
    """Execute the authorized plan once; returns per-order outcomes or raises SequenceStop."""
    outcome = {}
    for step in plan['steps']:
        cid = step['client_order_id']
        if step['action'] == 'place':
            reply = client.send('step', step['step'], step)
            result = reply.get('result') or {}
            if reply.get('status') != 'handled' or result.get('status') not in (
                    'passorder_called', 'already_present'):
                raise SequenceStop('place_not_handled', step=step['step'], reply=reply)
            if facts.errors.get(cid):
                raise SequenceStop('order_error', step=step['step'])
            if expect[cid] == 'resting':
                wait_for(client, facts, lambda: facts.status.get(cid) in RESTING_REACHED
                         or bool(facts.errors.get(cid)), resting_timeout, sleep)
                status = facts.status.get(cid)
                if status != 50:
                    raise SequenceStop('resting_order_not_reported', step=step['step'],
                                       order_status=status, order_error=bool(facts.errors.get(cid)))
                outcome[cid] = 'reported'
            else:
                wait_for(client, facts, lambda: (facts.status.get(cid) == 56 and facts.deals.get(cid))
                         or facts.status.get(cid) == 57 or bool(facts.errors.get(cid)),
                         fill_timeout, sleep)
                status = facts.status.get(cid)
                if status == 56 and facts.deals.get(cid):
                    outcome[cid] = 'filled'
                elif status == 57 or facts.errors.get(cid):
                    raise SequenceStop('fill_order_rejected', step=step['step'], order_status=status)
                else:
                    outcome[cid] = 'unfilled'
        else:
            if facts.status.get(cid) in TERMINAL:
                client.journal.append('cancel_skipped', step=step['step'],
                                      order_status=facts.status.get(cid))
                continue
            reply = client.send('step', step['step'], step)
            result = reply.get('result') or {}
            if reply.get('status') != 'handled' or result.get('status') not in (
                    'cancel_signal_sent', 'cancel_not_needed'):
                raise SequenceStop('cancel_not_handled', step=step['step'], reply=reply)
            wait_for(client, facts, lambda: facts.status.get(cid) in TERMINAL | {53},
                     cancel_timeout, sleep)
            status = facts.status.get(cid)
            if status not in TERMINAL | {53}:
                raise SequenceStop('cancel_unconfirmed', step=step['step'], order_status=status)
            outcome[cid] = {54: 'cancelled', 53: 'partially_cancelled', 56: 'filled',
                            57: 'rejected'}[status]
    return outcome


def main():
    import mmap
    audit = Path(__file__).resolve().parent
    fixture = json.loads((audit / 'fixture.json').read_text(encoding='utf-8-sig'))
    run = fixture['first']
    if fixture['mode'] != 'authorized-order':
        raise ValueError('fixture mode')
    plan = json.loads((audit / 'plan.json').read_text(encoding='ascii'))
    key = bytes.fromhex((audit / 'key.hex').read_text(encoding='ascii').strip())
    journal = Journal(audit / 'journal.jsonl')
    memory = mmap.mmap(-1, MAP_SIZE, tagname='Local\\qsys_authorized_order_' + run,
                       access=mmap.ACCESS_WRITE)
    client = Client(memory, run, key, journal)
    if not client.marker_present(COORDINATOR_MARKER_OFFSET,
                                 b'QSYS_AUTHORIZED_ORDER_ONLY:' + run.encode('ascii')):
        raise ValueError('Coordinator marker absent; not sent')
    journal.append('driver_ready', run=run, steps=len(plan['steps']))
    (audit / 'driver-ready.json').write_text(json.dumps({'run': run}), encoding='ascii')
    if not client.wait_scheduled(fixture.get('scheduled_timeout', 300)):
        journal.append('stopped', reason='executor_not_scheduled')
        return 2
    window = (fixture['not_before_ms'], fixture['not_after_ms'])
    if not window[0] <= now_ms() < window[1]:
        journal.append('stopped', reason='outside_authorized_window')
        return 2
    facts = Facts()
    status, outcome, stop = 'completed', {}, None
    try:
        outcome = run_plan(client, plan, fixture['expect'], facts)
    except TransportUnknown as exc:
        status, stop = 'transport_unknown', {'reason': str(exc)}
    except SequenceStop as exc:
        status, stop = 'stopped', {'reason': exc.reason, **exc.facts}
    try:
        client.snapshot()
        time.sleep(1.0)
        facts.absorb(client.read_reports())
    except TransportUnknown:
        journal.append('final_snapshot_unknown')
    result = dict(status=status, run=run, outcome=outcome, stop=stop,
                  final_order_status=facts.status, deals={k: len(v) for k, v in facts.deals.items()},
                  order_errors={k: len(v) for k, v in facts.errors.items()},
                  positions=len(facts.positions), automatic_replay=False)
    journal.append('result', **result)
    (audit / 'order-result.json').write_text(json.dumps(result, sort_keys=True), encoding='ascii')
    expected_ok = all(
        outcome.get(cid) == ('cancelled' if kind == 'resting' else 'filled')
        for cid, kind in fixture['expect'].items())
    return 0 if status == 'completed' and expected_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
