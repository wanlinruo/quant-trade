"""#599 diagnostic messages only. No trading adapter or operating-system I/O.

The correlation ID is not authentication. Transport ACL/peer identity and QMT
support require independent evidence; this codec never authorizes trading.
"""
import hashlib
import json
import re

PROTOCOL = "qsys.external-echo.v1"
_FIELDS = {"protocol", "kind", "run_id", "request_id", "seq",
           "issued_at_ms", "expires_at_ms", "payload"}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-finite JSON constant")


def _identifier(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{32}", value) is not None


def decode_ping(raw, expected_run_id, now_ms):
    if not _identifier(expected_run_id) or type(now_ms) is not int or now_ms < 0:
        raise ValueError("invalid receiver binding")
    if type(raw) is not bytes or not 1 <= len(raw) <= 4096:
        raise ValueError("invalid frame")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                       parse_constant=_reject_constant)
    if type(value) is not dict or set(value) != _FIELDS:
        raise ValueError("invalid diagnostic fields")
    if value["protocol"] != PROTOCOL or value["kind"] != "ping":
        raise ValueError("unsupported diagnostic operation")
    if value["run_id"] != expected_run_id or not _identifier(value["request_id"]):
        raise ValueError("correlation mismatch")
    for key in ("seq", "issued_at_ms", "expires_at_ms"):
        if type(value[key]) is not int or value[key] < 0:
            raise ValueError("invalid integer")
    start, end = value["issued_at_ms"], value["expires_at_ms"]
    if not 0 < end - start <= 5000 or not start <= now_ms < end:
        raise ValueError("diagnostic outside explicit time window")
    if type(value["payload"]) is not str or len(value["payload"].encode("utf-8")) > 256:
        raise ValueError("invalid diagnostic payload")
    return value


class EchoReceiver:
    def __init__(self, run_id):
        if not _identifier(run_id):
            raise ValueError("invalid run ID")
        self.run_id = run_id
        self._next_seq = 1
        self._seen = {}

    def receive(self, raw, now_ms):
        value = decode_ping(raw, self.run_id, now_ms)
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        previous = self._seen.get(value["request_id"])
        if previous is not None:
            if previous[0] != digest:
                raise ValueError("request ID reused with changed content")
            return previous[1]
        if value["seq"] != self._next_seq:
            raise ValueError("out of sequence")
        if len(self._seen) >= 64:
            raise ValueError("bounded diagnostic session exhausted")
        reply = json.dumps({"protocol": PROTOCOL, "kind": "echo",
                            "run_id": self.run_id,
                            "request_id": value["request_id"], "seq": value["seq"],
                            "payload": value["payload"], "received_at_ms": now_ms,
                            "authorizes_trading": False}, sort_keys=True,
                           ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._seen[value["request_id"]] = (digest, reply)
        self._next_seq += 1
        return reply
