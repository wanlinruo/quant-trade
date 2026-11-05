# coding: utf-8
"""#599: bounded schedule_run + authenticated SYNTHETIC messages only.

No account, market data, order, file, socket or native DLL interface is called.
A public fixture key is bound in a diagnostic copy, never a production identity.
This in-memory cache is NOT durable idempotency and does not authorize trading.
"""
from datetime import datetime, timedelta, timezone

RUN_ID = ""
PROBE_DATE = ""
CLIENT_BUILD = ""
AUTH_KEY_HEX = ""  # Explicit public test fixture; never a production credential.
PACKET_LIMIT = 8  # Only bounded diagnostic scenarios: one, three or eight.
PROTOCOL = "qsys.external-queue-diagnostic.v1"
PAYLOAD = "QSYS599_DIAGNOSTIC_ONLY"
_STATE = None
_RAN = False


def valid_id(value):
    return isinstance(value, str) and len(value) == 32 and all(
        c in "0123456789abcdef" for c in value)


def canonical(codec, value):
    return codec.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False).encode("ascii")


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate key")
        obj[key] = value
    return obj


class Receiver:
    """Fixed ping acceptance only. At most eight in-memory IDs per test."""
    def __init__(self, run_id, key, codec, authenticator):
        self.run_id, self.key = run_id, key
        self.codec, self.auth = codec, authenticator
        self.next_seq = 1
        self.seen = {}
        self.accepted = 0

    def sign(self, value):
        return self.auth.new(self.key, canonical(self.codec, value), 'sha256').hexdigest()

    def reply(self, value, reason):
        row = dict(protocol=PROTOCOL, run_id=self.run_id,
                   request_id=value.get('request_id') if valid_id(value.get('request_id')) else None,
                   seq=value.get('seq') if type(value.get('seq')) is int else None,
                   status='echo' if reason == 'ok' else 'rejected', reason=reason,
                   accepted_count=self.accepted, authorizes_trading=False)
        row['mac'] = self.sign(row)
        return canonical(self.codec, row)

    def receive(self, raw, now_ms):
        if type(raw) is not bytes or not 1 <= len(raw) <= 2000:
            raise ValueError('diagnostic frame size')
        value = self.codec.loads(raw.decode('ascii'), object_pairs_hook=unique_object)
        if type(value) is not dict:
            raise ValueError('diagnostic object')
        fields = {'protocol', 'run_id', 'request_id', 'seq', 'kind', 'payload',
                  'issued_at_ms', 'expires_at_ms', 'mac'}
        if set(value) != fields:
            return self.reply(value, 'fields')
        signature = value.pop('mac')
        if not isinstance(signature, str) or len(signature) != 64 or not self.auth.compare_digest(signature, self.sign(value)):
            return self.reply(value, 'authentication')
        if value['protocol'] != PROTOCOL or value['run_id'] != self.run_id:
            return self.reply(value, 'binding')
        if not valid_id(value['request_id']) or type(value['seq']) is not int or value['seq'] < 1:
            return self.reply(value, 'identity')
        if value['kind'] != 'ping' or value['payload'] != PAYLOAD:
            return self.reply(value, 'synthetic_only')
        start, end = value['issued_at_ms'], value['expires_at_ms']
        if type(now_ms) is not int or type(start) is not int or type(end) is not int:
            return self.reply(value, 'time_type')
        if not 0 < end - start <= 120000 or not start <= now_ms < end:
            return self.reply(value, 'expired')
        normalized = canonical(self.codec, value)
        old = self.seen.get(value['request_id'])
        if old is not None:
            return old[1] if old[0] == normalized else self.reply(value, 'request_conflict')
        if value['seq'] != self.next_seq:
            return self.reply(value, 'sequence')
        if len(self.seen) >= 8:
            return self.reply(value, 'capacity')
        self.accepted += 1
        self.next_seq += 1
        response = self.reply(value, 'ok')
        self.seen[value['request_id']] = (normalized, response)
        return response


def now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def emit(status, **facts):
    row = dict(protocol=PROTOCOL, run_id=RUN_ID if valid_id(RUN_ID) else None,
               status=status, account_calls=0, trading_calls=0,
               authorizes_trading=False, durable_dedup_verified=False)
    row.update(facts)
    print(repr(row))


def finish(C, reason):
    state = _STATE
    if state is None or state['stopped']:
        return
    state['stopped'] = True
    cancelled = None
    try:
        cancelled = C.cancel_schedule_run('qsys599_' + RUN_ID)
    except Exception as exc:
        emit('cancel_failed', error_type=type(exc).__name__)
    finally:
        try:
            state['memory'].close()
        except Exception as exc:
            emit('close_failed', error_type=type(exc).__name__)
    emit('finished', reason=reason, ticks=state['ticks'], packets=state['packets'],
         accepted_count=state['receiver'].accepted, cache_entries=len(state['receiver'].seen),
         max_callback_ms=state['max_ms'], cancel_result=cancelled)


def on_timer(C):
    state = _STATE
    if state is None or state['stopped']:
        return
    started = state['clock']()
    state['ticks'] += 1
    try:
        if state['ticks'] > 24 or now_ms() >= state['deadline_ms']:
            finish(C, 'bound')
            return
        m = state['memory']
        m.seek(0)
        generation = int.from_bytes(m.read(4), 'little')
        if generation == 0 or generation == state['last_generation']:
            return
        size = int.from_bytes(m.read(4), 'little')
        if not 1 <= size <= 2000:
            raise ValueError('request frame size')
        raw = m.read(size)
        m.seek(0)
        if int.from_bytes(m.read(4), 'little') != generation:
            return  # Writer has not committed a stable packet; no blocking wait.
        reply = state['receiver'].receive(raw, now_ms())
        if len(reply) > 2000:
            raise ValueError('response frame size')
        m.seek(4096)
        m.write(bytes(4))
        m.seek(4104)
        m.write(reply)
        m.seek(4100)
        m.write(len(reply).to_bytes(4, 'little'))
        m.seek(4096)
        m.write(generation.to_bytes(4, 'little'))
        state['last_generation'] = generation
        state['packets'] += 1
        summary = state['receiver'].codec.loads(reply.decode('ascii'))
        emit('packet', generation=generation, result=summary['reason'],
             accepted_count=state['receiver'].accepted)
        if state['packets'] >= PACKET_LIMIT:
            finish(C, 'diagnostic_packet_limit')
    except Exception as exc:
        emit('callback_failed', error_type=type(exc).__name__)
        finish(C, 'error')
    finally:
        elapsed = (state['clock']() - started) * 1000
        state['max_ms'] = max(state['max_ms'], elapsed)
        # The final marker includes final callback/cancel/close time.
        if state['stopped']:
            emit('callback_bound', max_callback_ms=state['max_ms'])
        elif state['ticks'] >= 24:
            finish(C, 'finite_tick_limit')


def init(C):
    global _STATE, _RAN
    if _RAN:
        return
    _RAN = True
    memory = None
    stage = 'configuration'
    try:
        if not valid_id(RUN_ID) or CLIENT_BUILD != '2.1.33.0' or PROBE_DATE != datetime.now().strftime('%Y%m%d'):
            raise ValueError('diagnostic binding')
        if len(AUTH_KEY_HEX) != 64 or any(c not in '0123456789abcdef' for c in AUTH_KEY_HEX):
            raise ValueError('explicit diagnostic key required')
        if not callable(getattr(C, 'schedule_run', None)) or not callable(getattr(C, 'cancel_schedule_run', None)):
            raise ValueError('finite cancellable scheduler required')
        if type(PACKET_LIMIT) is not int or PACKET_LIMIT not in (1, 3, 8):
            raise ValueError('explicit finite diagnostic packet bound')
        stage = 'imports'
        import json
        import hmac
        import mmap
        from time import perf_counter
        receiver = Receiver(RUN_ID, bytes.fromhex(AUTH_KEY_HEX), json, hmac)
        stage = 'open_named_memory'
        memory = mmap.mmap(-1, 8192, tagname='Local\\qsys599_queue_' + RUN_ID,
                           access=mmap.ACCESS_WRITE)
        _STATE = dict(memory=memory, receiver=receiver, ticks=0, packets=0,
                      last_generation=0, max_ms=0.0, stopped=False,
                      deadline_ms=now_ms() + 35000, clock=perf_counter)
        stage = 'schedule_run'
        task_id = C.schedule_run(on_timer, datetime.now() + timedelta(seconds=2),
                                 24, timedelta(seconds=1), 'qsys599_' + RUN_ID)
        emit('scheduled', task_id=task_id, finite_repeat_argument=24, packet_limit=PACKET_LIMIT)
    except Exception as exc:
        emit('init_failed', stage=stage, error_type=type(exc).__name__)
        if _STATE is not None:
            finish(C, 'init_failed')
        elif memory is not None:
            memory.close()


def stop(C):
    finish(C, 'model_stop')


def handlebar(C):
    pass
