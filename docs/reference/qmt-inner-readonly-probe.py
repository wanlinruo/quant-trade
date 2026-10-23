# coding: utf-8
"""#533: paste into the NEW QMT editor; diagnostic only, never a trader.

Fill the three metadata fields below. No account configuration is required.
Prints public market samples only; does not query account or order records.
History reads local QMT data only, with filling and subscription disabled.
This file deliberately uses Python 3.6 syntax and no qsys/third-party imports.
"""

import sys
from datetime import datetime


CLIENT_BUILD = ""  # Copy the new client's displayed version, not a guessed value.
PROBE_DATE = ""  # Actual local run date: YYYYMMDD.
HISTORY_END = ""  # Explicit, already closed trading day: YYYYMMDD.
SYMBOLS = ("600000.SH",)  # Public sample only; at most two symbols.
FIELDS = ("open", "high", "low", "close", "volume", "amount")

_CONFIG_OK = False
_RAN = False
_PROTOCOL = "qsys-qmt-inner-probe-v1"


def _emit(check, status, **facts):
    record = {"protocol": _PROTOCOL, "check": check, "status": status}
    record.update(facts)
    # repr avoids any optional serializer dependency. Copy output manually.
    print(repr(record))


def _validate_config(today):
    if not CLIENT_BUILD.strip():
        raise ValueError("client version must be recorded")
    for value in (PROBE_DATE, HISTORY_END):
        if len(value) != 8 or not value.isdigit():
            raise ValueError("explicit YYYYMMDD dates required")
        datetime.strptime(value, "%Y%m%d")
    if PROBE_DATE != today or HISTORY_END >= PROBE_DATE:
        raise ValueError("run date must be today and history must be closed")
    if not 1 <= len(SYMBOLS) <= 2 or len(set(SYMBOLS)) != len(SYMBOLS):
        raise ValueError("one or two unique public symbols required")
    for symbol in SYMBOLS:
        parts = symbol.split(".")
        if (len(parts) != 2 or len(parts[0]) != 6
                or not parts[0].isdigit() or parts[1] not in ("SH", "SZ", "BJ")):
            raise ValueError("invalid public symbol")


def init(ContextInfo):
    global _CONFIG_OK, _RAN
    _CONFIG_OK = False
    _RAN = False
    try:
        _validate_config(datetime.now().strftime("%Y%m%d"))
    except Exception as exc:
        _emit("configuration", "rejected", error_type=type(exc).__name__)
        return
    _CONFIG_OK = True
    _emit("environment", "observed", client_build=CLIENT_BUILD,
          probe_date=PROBE_DATE, history_end=HISTORY_END,
          python_version=tuple(sys.version_info[:3]))
    # Existence is not proof of permission, freshness, or successful execution.
    names = ("is_last_bar", "get_full_tick", "get_market_data_ex", "run_time")
    _emit("context_methods", "presence_only",
          methods={name: callable(getattr(ContextInfo, name, None)) for name in names})
    names = ("get_trade_detail_data", "get_history_trade_detail_data")
    _emit("query_functions", "presence_only",
          functions={name: callable(globals().get(name)) for name in names})


def _tick_sample(ContextInfo):
    try:
        raw = ContextInfo.get_full_tick(list(SYMBOLS))
        if not isinstance(raw, dict):
            _emit("tick", "unexpected_type", returned_type=type(raw).__name__)
            return
        fields = ("time", "timetag", "lastPrice", "lastClose", "volume", "amount")
        for symbol in SYMBOLS:
            item = raw.get(symbol)
            if not isinstance(item, dict) or not item:
                _emit("tick", "missing", symbol=symbol)
                continue
            _emit("tick", "sample_only", symbol=symbol,
                  fields_present=[key for key in fields if key in item],
                  raw={key: item[key] for key in fields if key in item})
    except Exception as exc:
        # Exception strings may contain node/account details; print type only.
        _emit("tick", "error", error_type=type(exc).__name__)


def _history_sample(ContextInfo, period, count):
    try:
        raw = ContextInfo.get_market_data_ex(
            list(FIELDS), list(SYMBOLS), period=period,
            start_time="", end_time=HISTORY_END + "150000", count=count,
            dividend_type="none", fill_data=False, subscribe=False,
        )
        if not isinstance(raw, dict):
            _emit(period, "unexpected_type", returned_type=type(raw).__name__)
            return
        for symbol in SYMBOLS:
            frame = raw.get(symbol)
            if frame is None:
                _emit(period, "missing", symbol=symbol)
                continue
            missing = [name for name in FIELDS if name not in frame.columns]
            if missing:
                _emit(period, "missing_columns", symbol=symbol, columns=missing)
                continue
            if len(frame) == 0:
                _emit(period, "empty", symbol=symbol)
                continue
            _emit(period, "sample_only", symbol=symbol, row_count=len(frame),
                  raw=frame.loc[:, list(FIELDS)].tail(2).to_dict(orient="split"))
    except Exception as exc:
        # In particular, do not retry with older signatures/default fill_data.
        _emit(period, "error", error_type=type(exc).__name__)


def handlebar(ContextInfo):
    global _RAN
    if not _CONFIG_OK or _RAN:
        return
    try:
        if not ContextInfo.is_last_bar():
            return
    except Exception as exc:
        _RAN = True
        _emit("last_bar", "error", error_type=type(exc).__name__)
        return
    # Module state limits repeated calls within this run, not across restarts.
    _RAN = True
    _tick_sample(ContextInfo)
    _history_sample(ContextInfo, "1d", 101)
    _history_sample(ContextInfo, "1m", 2)
    _emit("scope", "incomplete",
          not_verified=["broker_account", "orders", "callbacks", "timer_accuracy",
                        "truth_inputs", "persistence", "restart_recovery",
                        "data_units", "data_freshness", "production_readiness"])
