"""#583: bounded native cache evidence; never download, subscribe, or trade."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from time import monotonic

FIELDS = ("time", "open", "high", "low", "close", "volume", "amount")
SYMBOLS = ("603828.SH", "001228.SZ", "920009.BJ", "688197.SH", "600000.SH")
TICK_SYMBOLS = ("603828.SH", "600000.SH")
PREFIX = "QSYS583_PROBE "


def frame_record(frame, fields):
    import pandas as pd

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("unexpected_native_container")
    if len(frame) > 20000:
        raise ValueError("bounded_cache_response_exceeded")
    if frame.empty:
        return {"rows": [], "count": 0, "columns": list(frame.columns)}
    if any(name not in frame.columns for name in fields):
        raise ValueError("missing_native_fields")
    if frame.index.duplicated().any():
        raise ValueError("duplicate_native_index")
    rows = []
    for index, row in frame.loc[:, list(fields)].iterrows():
        record = {"index": str(index)}
        for name in fields:
            value = row[name]
            if pd.isna(value):
                record[name] = None
            else:
                value = value.item() if hasattr(value, "item") else value
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise TypeError("non_numeric_native_field")
                if not math.isfinite(value):
                    raise ValueError("nonfinite_native_field")
                record[name] = value
        rows.append(record)
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {"rows": rows, "count": len(rows),
            "dtypes": {name: str(frame[name].dtype) for name in fields},
            "values_sha256": hashlib.sha256(payload.encode()).hexdigest()}


def observe(xtdata, day, clock=monotonic, result=None):
    started = clock()
    if result is None:
        result = {}
    result.update(minute_cases=[], tick_cases=[], calls=[])
    stamp = day.strftime("%Y%m%d")
    def read(symbol, period, fill, fields, start, end):
        if clock() - started > 40:
            raise TimeoutError("probe_budget_exhausted")
        call = {"api": "get_local_data", "symbol": symbol, "period": period,
                "fill_data": fill, "start": start, "end": end, "count": -1,
                "dividend_type": "none"}
        result["calls"].append(call)
        response = xtdata.get_local_data(field_list=list(fields), stock_list=[symbol],
            period=period, start_time=start, end_time=end, count=-1,
            dividend_type="none", fill_data=fill)
        if not isinstance(response, dict) or set(response) != {symbol}:
            raise TypeError("unexpected_native_response")
        frame = response[symbol]
        native_container = type(frame).__name__
        if period == "tick":
            import numpy as np
            import pandas as pd
            if isinstance(frame, np.ndarray):
                if frame.size and (not frame.dtype.names or any(
                        field not in frame.dtype.names for field in fields)):
                    raise ValueError("unexpected_tick_record_fields")
                frame = pd.DataFrame.from_records(frame)
        return {**frame_record(frame, fields), "native_container": native_container}
    for symbol in SYMBOLS:
        case = {"symbol": symbol}
        for flag in (False, True):
            case[str(flag).lower()] = read(symbol, "1m", flag, FIELDS, stamp, stamp)
        case["fill_variants_equal"] = case["false"].get("rows") == case["true"].get("rows")
        result["minute_cases"].append(case)
    for symbol in TICK_SYMBOLS:
        result["tick_cases"].append({"symbol": symbol, "data": read(
            symbol, "tick", False, ("time", "lastPrice", "volume", "amount"),
            stamp + "091500", stamp + "094000")})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    report = {"protocol": "qsys583-native-zero-evidence-v1", "status": "failed",
              "at": datetime.now().astimezone().isoformat(), "pid": os.getpid(),
              "python": sys.version.split()[0], "day": args.day.isoformat(),
              "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "download_calls": 0, "subscribe_calls": 0, "account_calls": 0,
              "trade_calls": 0, "independent_market_truth_verified": False}
    try:
        now = datetime.now()
        if args.day > now.date() or (args.day == now.date() and now.hour < 16):
            raise ValueError("unclosed_day")
        if not 1 <= args.port <= 65535:
            raise ValueError("invalid_port")
        from xtquant import xtdata
        xtdata.connect(ip="127.0.0.1", port=args.port, remember_if_success=False)
        observe(xtdata, args.day, result=report)
        report["status"] = "completed"
    except Exception as exc:
        # SDK/config errors can contain credentials: never print exception messages.
        report["error_type"] = type(exc).__name__
    print(PREFIX + json.dumps(report, ensure_ascii=True, allow_nan=False), flush=True)
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
