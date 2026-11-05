# coding: utf-8
"""#599 diagnostic only: one bounded named-memory ping/echo, no trading.

Uses the standard mmap API without patching hooks or loading native libraries.
Only synthetic fixed text is accepted. No files, sockets, timers or retry loop.
Module presence, this editor test and broker permission are separate evidence.
"""
from datetime import datetime

RUN_ID = ""
PROBE_DATE = ""
CLIENT_BUILD = ""
_PROTOCOL = "qsys.external-mmap-diagnostic.v1"
_PAYLOAD = "QSYS599_DIAGNOSTIC_ONLY"
_RAN = False


def _now_ms():
    return int((datetime.utcnow() - datetime(1970, 1, 1)).total_seconds() * 1000)


def _emit(status, **facts):
    row = dict(protocol=_PROTOCOL, run_id=RUN_ID if _valid_id(RUN_ID) else None,
               status=status, authorizes_trading=False, trading_calls=0,
               account_calls=0, native_dll_calls=0)
    row.update(facts)
    print(repr(row))


def _valid_id(value):
    return isinstance(value, str) and len(value) == 32 and all(
        c in "0123456789abcdef" for c in value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _echo(memory, codec, now_ms):
    memory.seek(0)
    size = int.from_bytes(memory.read(4), "little")
    if not 1 <= size <= 1000:
        raise ValueError("request size")
    request = codec.loads(memory.read(size).decode("utf-8"),
                          object_pairs_hook=_unique_object)
    fields = {"protocol", "run_id", "request_id", "kind", "payload",
              "issued_at_ms", "expires_at_ms"}
    if type(request) is not dict or set(request) != fields:
        raise ValueError("request fields")
    if request["protocol"] != _PROTOCOL or request["run_id"] != RUN_ID:
        raise ValueError("request binding")
    if not _valid_id(request["request_id"]):
        raise ValueError("request ID")
    if request["kind"] != "ping" or request["payload"] != _PAYLOAD:
        raise ValueError("only fixed synthetic payload allowed")
    start, end = request["issued_at_ms"], request["expires_at_ms"]
    if type(start) is not int or type(end) is not int:
        raise ValueError("request time type")
    if not 0 < end - start <= 120000 or not start <= now_ms < end:
        raise ValueError("request time window")
    # No I/O or wait occurs between this validation and committing the reply.
    reply = codec.dumps(dict(protocol=_PROTOCOL, run_id=RUN_ID,
                             request_id=request["request_id"], kind="echo",
                             payload=_PAYLOAD, authorizes_trading=False),
                        sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(reply) > 1000:
        raise ValueError("reply size")
    memory.seek(1028)
    memory.write(reply)
    memory.seek(1024)
    memory.write(len(reply).to_bytes(4, "little"))
    return dict(request_bytes=size, response_bytes=len(reply),
                request_id=request["request_id"])


def init(ContextInfo):
    global _RAN
    if _RAN:
        return
    _RAN = True
    memory = None
    stage = "configuration"
    try:
        if not _valid_id(RUN_ID) or CLIENT_BUILD != "2.1.33.0":
            raise ValueError("explicit diagnostic binding required")
        if PROBE_DATE != datetime.now().strftime("%Y%m%d"):
            raise ValueError("execution date mismatch")
        stage = "imports"
        import json
        import mmap
        stage = "open_named_memory"
        # Windows opens an existing name, or creates one if absent. An absent
        # server yields an empty header and fails validation; never a fallback.
        memory = mmap.mmap(-1, 4096, tagname="Local\\qsys599_diag_" + RUN_ID,
                           access=mmap.ACCESS_WRITE)
        stage = "validate_and_echo"
        facts = _echo(memory, json, _now_ms())
        stage = "close"
        memory.close()
        memory = None
        _emit("echo_written", stage="complete", **facts)
    except Exception as exc:
        _emit("failed", stage=stage, error_type=type(exc).__name__)
    finally:
        if memory is not None:
            try:
                memory.close()
            except Exception as exc:
                _emit("close_failed", error_type=type(exc).__name__)


def handlebar(ContextInfo):
    pass
