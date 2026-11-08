"""Default-off QMT thin-executor validation candidate; #601.

This standard-library receiver has no account, broker or I/O capability.
The public test key is not production authentication. No callback can be supplied.

The checked-in source is inert.  A reviewed installation must set ``ENABLED``
to the literal boolean ``True`` and bind an unpredictable ``RUN_ID`` plus the
exact client build.  Unlike the one-day probe, the candidate is not tied to an
installation date, so the same reviewed model can be exercised after a UI
session disconnect or client restart.  It still only validates the fixed
synthetic command and can never trade.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re

PROTOCOL = 'qsys.synthetic-intent-validation.v1'
SCOPE = 'validation:synthetic-intent'
KEY = bytes([17] * 32)  # Explicitly public diagnostic fixture.
COMMAND_FIELDS = {'schema_version','session_id','strategy_id','client_order_id','symbol',
                  'side','qty','order_type','limit_price','created_at','expires_at'}
FIELDS = {'protocol','scope','kind','run_id','seq','attempt_id','session_nonce',
          'command_sha256','command_json','issued_at_ms','expires_at_ms','mac'}
RUN_ID = ''
CLIENT_BUILD = ''
ENABLED = False
INSTANCE_MODE = 'validation-only'
PACKET_LIMIT = 1
SCHEDULED_MARKER_OFFSET = 7168
_STATE = None
_RAN = False


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('duplicate_key')
        obj[key] = value
    return obj


def reject_constant(value):
    raise ValueError('nonfinite_json')


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)


def valid_id(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value) is not None


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def scheduled_marker(run_id):
    if not valid_id(run_id):
        raise ValueError('explicit scheduled marker binding required')
    return b'QSYS_SYNTHETIC_SCHEDULED:' + run_id.encode('ascii')


def sign(value, key=KEY):
    return hmac.new(key, canonical(value), 'sha256').hexdigest()


def command(raw, now_ms):
    if type(raw) is not str or len(raw.encode('utf-8')) > 2000:
        raise ValueError('command_size')
    obj = decode(raw)
    if type(obj) is not dict or set(obj) != COMMAND_FIELDS:
        raise ValueError('command_fields')
    if canonical(obj).decode('ascii') != raw:
        raise ValueError('command_canonical')
    if type(obj['schema_version']) is not int or obj['schema_version'] != 2:
        raise ValueError('command_schema')
    if obj['session_id'] != 'synthetic-session' or obj['strategy_id'] != 'diagnostic-only':
        raise ValueError('command_session_strategy')
    if (type(obj['client_order_id']) is not str
            or not re.fullmatch(r'synthetic-qsys-[a-z0-9_-]{1,64}', obj['client_order_id'])):
        raise ValueError('synthetic_identity')
    if (obj['symbol'] != '600036.SH' or obj['side'] != 'buy' or obj['order_type'] != 'limit'
            or type(obj['qty']) is not int or obj['qty'] != 100
            or type(obj['limit_price']) is not float or obj['limit_price'] != 10.0):
        raise ValueError('synthetic_fixture_only')
    times = []
    for key in ('created_at', 'expires_at'):
        if type(obj[key]) is not str or not 1 <= len(obj[key]) <= 64:
            raise ValueError('command_time')
        try:
            value = datetime.fromisoformat(obj[key])
            if value.utcoffset() != timedelta(0):
                raise ValueError('explicit UTC required')
            times.append(int(value.timestamp() * 1000))
        except (ValueError, OverflowError, OSError):
            raise ValueError('command_time') from None
    if not 0 < times[1]-times[0] <= 600000 or not times[0] <= now_ms < times[1]:
        raise ValueError('command_expired')
    return obj


class Receiver:
    def __init__(self, run_id, key=KEY):
        if not valid_id(run_id) or key != KEY:
            raise ValueError('explicit synthetic receiver binding required')
        self.run_id, self.key = run_id, key
        self.next_seq, self.accepted, self.seen = 1, 0, {}

    def reply(self, obj, reason):
        row = dict(protocol=PROTOCOL, run_id=self.run_id,
            attempt_id=obj.get('attempt_id') if valid_id(obj.get('attempt_id')) else None,
            session_nonce=obj.get('session_nonce') if valid_id(obj.get('session_nonce')) else None,
            command_sha256=obj.get('command_sha256') if valid_sha(obj.get('command_sha256')) else None,
            seq=obj.get('seq') if type(obj.get('seq')) is int else None,
            status='validated_synthetic' if reason == 'ok' else 'rejected', reason=reason,
            executed=False, authorizes_trading=False, accepted_count=self.accepted)
        row['mac'] = sign(row, self.key)
        return canonical(row)

    def receive(self, raw, now_ms):
        if type(raw) is not bytes or not 1 <= len(raw) <= 4000 or type(now_ms) is not int:
            raise ValueError('diagnostic_frame')
        obj = decode(raw.decode('ascii'))
        if type(obj) is not dict:
            raise ValueError('diagnostic_object')
        if set(obj) != FIELDS:
            return self.reply(obj, 'fields')
        mac = obj.pop('mac')
        if not valid_sha(mac) or not hmac.compare_digest(mac, sign(obj, self.key)):
            return self.reply(obj, 'signature')
        if (obj['protocol'] != PROTOCOL or obj['scope'] != SCOPE
                or obj['kind'] != 'validate_synthetic_intent' or obj['run_id'] != self.run_id):
            return self.reply(obj, 'binding_scope')
        if (not valid_id(obj['attempt_id']) or not valid_id(obj['session_nonce'])
                or not valid_sha(obj['command_sha256']) or type(obj['seq']) is not int):
            return self.reply(obj, 'identity')
        start, end = obj['issued_at_ms'], obj['expires_at_ms']
        if (type(start) is not int or type(end) is not int or not 0 < end-start <= 5000
                or not start <= now_ms < end):
            return self.reply(obj, 'transport_expired')
        try:
            cmd = command(obj['command_json'], now_ms)
        except ValueError as exc:
            return self.reply(obj, str(exc))
        if hashlib.sha256(obj['command_json'].encode('ascii')).hexdigest() != obj['command_sha256']:
            return self.reply(obj, 'command_sha')
        normalized = canonical(dict(obj, mac=mac))
        old = self.seen.get(cmd['client_order_id'])
        if old is not None:
            return old[1] if old[0] == normalized else self.reply(obj, 'intent_conflict')
        if obj['seq'] != self.next_seq or self.accepted >= 8:
            return self.reply(obj, 'sequence_capacity')
        self.accepted += 1
        self.next_seq += 1
        response = self.reply(obj, 'ok')
        self.seen[cmd['client_order_id']] = (normalized, response)
        return response


def now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def emit(status, **facts):
    row = dict(protocol=PROTOCOL, run_id=RUN_ID if valid_id(RUN_ID) else None,
               instance_mode=INSTANCE_MODE,
               status=status, account_calls=0, market_calls=0, trading_calls=0,
               executed=False, authorizes_trading=False)
    row.update(facts)
    print(repr(row))


def finish(C, reason):
    state = _STATE
    if state is None or state['stopped']:
        return
    state['stopped'] = True
    cancelled = None
    try:
        cancelled = C.cancel_schedule_run('qsys601_' + RUN_ID)
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
        memory = state['memory']
        memory.seek(0)
        generation = int.from_bytes(memory.read(4), 'little')
        if generation == 0 or generation == state['last_generation']:
            return
        size = int.from_bytes(memory.read(4), 'little')
        if not 1 <= size <= 4000:
            raise ValueError('request frame size')
        raw = memory.read(size)
        memory.seek(0)
        if int.from_bytes(memory.read(4), 'little') != generation:
            return
        reply = state['receiver'].receive(raw, now_ms())
        if len(reply) > 2000:
            raise ValueError('response frame size')
        memory.seek(4096)
        memory.write(bytes(4))
        memory.seek(4104)
        memory.write(reply)
        memory.seek(4100)
        memory.write(len(reply).to_bytes(4, 'little'))
        memory.seek(4096)
        memory.write(generation.to_bytes(4, 'little'))
        state['last_generation'] = generation
        state['packets'] += 1
        summary = decode(reply.decode('ascii'))
        emit('packet', generation=generation, result=summary['reason'],
             accepted_count=state['receiver'].accepted,
             command_sha256=summary['command_sha256'])
        if state['packets'] >= PACKET_LIMIT:
            finish(C, 'diagnostic_packet_limit')
    except Exception as exc:
        emit('callback_failed', error_type=type(exc).__name__)
        finish(C, 'error')
    finally:
        elapsed = (state['clock']() - started) * 1000
        state['max_ms'] = max(state['max_ms'], elapsed)
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
        if ENABLED is not True:
            emit('disabled')
            return
        if (INSTANCE_MODE != 'validation-only' or not valid_id(RUN_ID)
                or CLIENT_BUILD != '2.1.33.0'):
            raise ValueError('validation binding')
        if PACKET_LIMIT != 1:
            raise ValueError('single diagnostic packet required')
        if (not callable(getattr(C, 'schedule_run', None))
                or not callable(getattr(C, 'cancel_schedule_run', None))):
            raise ValueError('finite cancellable scheduler required')
        stage = 'imports'
        import mmap
        from time import perf_counter
        stage = 'open_named_memory'
        memory = mmap.mmap(-1, 8192, tagname='Local\\qsys_synthetic_intent_' + RUN_ID,
                           access=mmap.ACCESS_WRITE)
        memory.seek(6144)
        marker = b'QSYS_SYNTHETIC_INTENT_ONLY:' + RUN_ID.encode('ascii')
        if memory.read(len(marker)) != marker:
            raise ValueError('existing synthetic coordinator marker required')
        _STATE = dict(memory=memory, receiver=Receiver(RUN_ID), ticks=0, packets=0,
                      last_generation=0, max_ms=0.0, stopped=False,
                      deadline_ms=now_ms()+35000, clock=perf_counter)
        stage = 'schedule_run'
        task_id = C.schedule_run(on_timer, datetime.now()+timedelta(seconds=2),
                                 24, timedelta(seconds=1), 'qsys601_' + RUN_ID)
        stage = 'publish_scheduled_marker'
        memory.seek(SCHEDULED_MARKER_OFFSET)
        memory.write(scheduled_marker(RUN_ID))
        emit('scheduled', task_id=task_id, finite_repeat_argument=24,
             packet_limit=PACKET_LIMIT)
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
