# coding: utf-8
"""#546: manual native editor diagnostic, compatible with Python 3.6/3.12.

Fill explicit metadata before use. One local, unadjusted daily history query
for at most two public symbols; no downloading, subscription, KV or trading.
This probes the existing get_market_data_ex path, not the three Panel APIs.
"""
import sys
from datetime import datetime

CLIENT_BUILD = ""
PROBE_DATE = ""  # Actual execution day, YYYYMMDD.
HISTORY_END = ""  # Explicit closed trading day, YYYYMMDD.
SYMBOLS = ("600000.SH", "000001.SZ")
FIELDS = ("open", "close", "volume", "amount")
_PROTOCOL = "qsys-qmt-runtime-container-v1"
_READY = False
_RAN = False
_CALLS = 0


def _emit(check, status, **facts):
    record = dict(protocol=_PROTOCOL, check=check, status=status)
    record.update(facts)
    print(repr(record))


def init(ContextInfo):
    global _READY
    _READY = False
    try:
        if not CLIENT_BUILD.strip():
            raise ValueError("explicit client build required")
        for value in (PROBE_DATE, HISTORY_END):
            if len(value) != 8 or not value.isdigit():
                raise ValueError("explicit dates required")
            datetime.strptime(value, "%Y%m%d")
        if PROBE_DATE != datetime.now().strftime("%Y%m%d") or HISTORY_END >= PROBE_DATE:
            raise ValueError("history must be closed before execution day")
        if not 1 <= len(SYMBOLS) <= 2 or len(set(SYMBOLS)) != len(SYMBOLS):
            raise ValueError("one or two unique symbols required")
        for symbol in SYMBOLS:
            code, market = symbol.split(".")
            if len(code) != 6 or not code.isdigit() or market not in ("SH", "SZ", "BJ"):
                raise ValueError("invalid symbol")
    except Exception as exc:
        _emit("configuration", "rejected", error_type=type(exc).__name__)
        return
    _emit("environment", "observed", client_build=CLIENT_BUILD,
          probe_date=PROBE_DATE, history_end=HISTORY_END,
          python_version=tuple(sys.version_info[:3]))
    for name in ("pandas", "numpy"):
        try:
            module = __import__(name)
            _emit("module", "observed", name=name, version=module.__version__)
        except Exception as exc:
            _emit("module", "unavailable", name=name, error_type=type(exc).__name__)
    names = ("get_market_data_ex", "get_market_data", "get_financial_data",
             "get_top10_share_holder", "get_raw_financial_data")
    _emit("interfaces", "presence_only",
          methods={name: callable(getattr(ContextInfo, name, None)) for name in names})
    _READY = True


def _describe(symbol, frame):
    import pandas as pd
    if not isinstance(frame, pd.DataFrame):
        _emit("daily_frame", "unexpected_type", symbol=symbol,
              returned_type=type(frame).__name__)
        return
    if frame.empty:
        _emit("daily_frame", "empty_unverified", symbol=symbol)
        return
    if len(frame) > 2 or len(frame.columns) > len(FIELDS):
        _emit("daily_frame", "unexpected_shape", symbol=symbol, shape=tuple(frame.shape))
        return
    multi = isinstance(frame.columns, pd.MultiIndex)
    missing = [name for name in FIELDS if name not in frame.columns]
    _emit("daily_frame", "sample_only", symbol=symbol, shape=tuple(frame.shape),
          column_levels=frame.columns.nlevels, multi_columns=multi,
          columns=[repr(value) for value in frame.columns],
          missing_columns=missing, duplicate_columns=bool(frame.columns.has_duplicates),
          index_type=type(frame.index).__name__, index_dtype=str(frame.index.dtype),
          index=[repr(value) for value in frame.index],
          duplicate_index=bool(frame.index.has_duplicates),
          monotonic_increasing=bool(frame.index.is_monotonic_increasing),
          dtypes=[str(value) for value in frame.dtypes],
          null_count=int(frame.isnull().sum().sum()))


def handlebar(ContextInfo):
    global _RAN, _CALLS
    if not _READY or _RAN or not ContextInfo.is_last_bar():
        return
    _RAN = True  # Consume before the call: exceptions must never trigger retry.
    try:
        _CALLS += 1
        raw = ContextInfo.get_market_data_ex(
            list(FIELDS), list(SYMBOLS), period="1d", start_time="",
            end_time=HISTORY_END + "150000", count=2, dividend_type="none",
            fill_data=False, subscribe=False,
        )
        _emit("history_container", "observed", returned_type=type(raw).__name__)
        if not isinstance(raw, dict):
            _emit("history_container", "unexpected_type")
        else:
            for symbol in SYMBOLS:
                if symbol not in raw:
                    _emit("daily_frame", "missing", symbol=symbol)
                else:
                    _describe(symbol, raw[symbol])
    except Exception as exc:
        _emit("history", "error", error_type=type(exc).__name__)
    _emit("scope", "incomplete", history_attempts=_CALLS, download_calls=0,
          kv_calls=0, account_calls=0, trading_calls=0,
          panel_api_calls=0, numeric_equivalence_verified=False)
