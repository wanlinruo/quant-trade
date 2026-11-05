"""Full SYNTHETIC intent validation, never execution; #602.

This standard-library receiver has no account, broker or I/O capability.
The public test key is not production authentication. No callback can be supplied.
"""
from datetime import datetime, timedelta
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
