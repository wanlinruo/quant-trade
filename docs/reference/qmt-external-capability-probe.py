# coding: utf-8
"""#599: manual editor capability probe; no account, I/O or trading calls.

Use a new diagnostic model, explicit build/date/run ID, and editor debug mode.
Module imports and callable presence do not establish an allowed signal channel.
No receiver, timer, network connection, file operation or native DLL call exists.
"""
import sys
from datetime import datetime

CLIENT_BUILD = ""
PROBE_DATE = ""
RUN_ID = ""  # Non-secret correlation ID; 32 lowercase hexadecimal characters.
_PROTOCOL = "qsys-qmt-external-capabilities-v1"
_MODULES = ("json", "socket", "ctypes", "mmap", "win32pipe", "win32file")
_RAN = False


def _emit(check, status, **facts):
    public_id = RUN_ID if isinstance(RUN_ID, str) and len(RUN_ID) == 32 and all(
        c in "0123456789abcdef" for c in RUN_ID) else None
    row = dict(protocol=_PROTOCOL, run_id=public_id, check=check, status=status)
    row.update(facts)
    print(repr(row))


def init(ContextInfo):
    global _RAN
    if _RAN:
        return
    _RAN = True
    try:
        if not isinstance(CLIENT_BUILD, str) or not CLIENT_BUILD.strip() or len(CLIENT_BUILD) > 80:
            raise ValueError("explicit build required")
        if len(RUN_ID) != 32 or any(c not in "0123456789abcdef" for c in RUN_ID):
            raise ValueError("explicit correlation ID required")
        if len(PROBE_DATE) != 8 or not PROBE_DATE.isdigit():
            raise ValueError("explicit date required")
        datetime.strptime(PROBE_DATE, "%Y%m%d")
        if PROBE_DATE != datetime.now().strftime("%Y%m%d"):
            raise ValueError("execution date mismatch")
    except Exception as exc:
        _emit("configuration", "rejected", error_type=type(exc).__name__)
        return
    _emit("environment", "observed", client_build=CLIENT_BUILD,
          probe_date=PROBE_DATE, python_version=tuple(sys.version_info[:3]),
          open_type=type(open).__name__)
    for name in _MODULES:
        try:
            module = __import__(name)
            _emit("module", "imported", name=name,
                  win32_loader_present=callable(getattr(module, "WinDLL", None)))
        except Exception as exc:
            # Raw exceptions may contain private paths or configuration values.
            _emit("module", "unavailable", name=name, error_type=type(exc).__name__)
    _emit("interfaces", "presence_only",
          native={name: callable(globals().get(name)) for name in (
              "passorder", "get_trade_detail_data", "get_history_trade_detail_data")},
          context={name: callable(getattr(ContextInfo, name, None)) for name in (
              "run_time", "schedule_run")})
    _emit("scope", "incomplete", module_attempts=len(_MODULES),
          account_calls=0, trading_calls=0, file_operations=0,
          network_connections=0, native_dll_calls=0, ipc_created=0,
          external_signals_received=0, authorizes_trading=False,
          channel_verified=False, broker_permission_verified=False)


def handlebar(ContextInfo):
    pass
