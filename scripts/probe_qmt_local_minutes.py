"""#550/#478：固定已收盘样本的只读缓存探针；不下载、不订阅、不访问账户。"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import sys
from time import monotonic

FIELDS = ("open", "high", "low", "close", "volume", "amount")
CASES = (
    ("600000.SH", "20261026", "20261027"),
    ("000001.SZ", "20261026", "20261027"),
    ("920285.BJ", "20261026", "20261027"),
    ("301139.SZ", "20261026", "20261027"),
    ("301139.SZ", "20261028", "20261028"),
    ("513100.SH", "20260928", "20260929"),
    ("600000.SH", "20261025", "20261025"),
    ("688785.SH", "20261026", "20261027"),
)
PREFIX = "QSYS550_PROBE "


def inspect_frame(frame):
    """只记录市场字段摘要；缺失保持null，不以0替代。"""
    import pandas as pd

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("unexpected native local container")
    result = {"rows": len(frame), "columns": list(frame.columns),
              "index_type": type(frame.index).__name__,
              "duplicate_index": int(frame.index.duplicated().sum())}
    if frame.empty:
        return result
    values = frame.loc[:, list(FIELDS)]
    def number(value):
        if pd.isna(value):
            return None
        value = float(value)
        return value if math.isfinite(value) else {"nonfinite": repr(value)}

    result.update(
        index_first=str(frame.index[0]), index_last=str(frame.index[-1]),
        null_cells=int(values.isna().sum().sum()),
        zero_volume=int((frame["volume"] == 0).sum()),
        positive_amount_zero_volume=int(((frame["volume"] == 0) & (frame["amount"] > 0)).sum()),
        volume_sum=number(frame["volume"].sum(skipna=False)),
        amount_sum=number(frame["amount"].sum(skipna=False)),
    )
    result["first_rows"] = [
        {"index": str(index), **{k: number(row[k])
                                 for k in ("time", *FIELDS)}}
        for index, row in frame.head(3).iterrows()
    ]
    moments = [datetime.fromtimestamp(value / 1000, timezone(timedelta(hours=8)))
               .strftime("%Y%m%d%H%M%S") for value in frame["time"]]
    result["time_ms_matches_index_in_UTC8"] = moments == [str(i) for i in frame.index]
    # Hash native values/index without decimal rounding; version scoped pandas hash.
    result["pandas_value_hash_sha256"] = hashlib.sha256(
        pd.util.hash_pandas_object(frame, index=True).values.tobytes()
    ).hexdigest()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    started = monotonic()
    report = {"protocol": "qsys550-native-local-readonly-v1", "python": sys.version.split()[0],
              "download_calls": 0, "subscribe_calls": 0, "account_calls": 0,
              "trading_calls": 0, "cases": [], "status": "failed"}
    try:
        import pandas as pd
        from xtquant import xtdata

        xtdata.connect(ip="127.0.0.1", port=args.port, remember_if_success=False)
        for symbol, start, end in CASES:
            if monotonic() - started > 40:
                raise TimeoutError("bounded probe budget exhausted")
            params = {"field_list": ["time", *FIELDS], "stock_list": [symbol],
                      "period": "1m", "start_time": start, "end_time": end,
                      "count": -1, "dividend_type": "none"}
            local = {}
            case = {"symbol": symbol, "start": start, "end": end}
            for flag in (True, False):
                data = xtdata.get_local_data(**params, fill_data=flag)
                if not isinstance(data, dict) or set(data) != {symbol}:
                    raise TypeError("unexpected native local response")
                local[flag] = data[symbol]
                case["local_fill_" + str(flag).lower()] = inspect_frame(data[symbol])
            case["local_fill_variants_equal"] = local[True].equals(local[False])
            rpc = xtdata.get_market_data(**{**params, "field_list": list(FIELDS)}, fill_data=False)
            rpc_frame = pd.DataFrame({name: rpc[name].loc[symbol] for name in FIELDS})
            case["rpc_rows"] = len(rpc_frame)
            reference = local[False].loc[:, list(FIELDS)]
            case["rpc_local_container_equal"] = rpc_frame.equals(reference)
            case["rpc_local_index_equal"] = rpc_frame.index.equals(reference.index)
            case["rpc_dtypes"] = {k: str(v) for k, v in rpc_frame.dtypes.items()}
            case["local_dtypes"] = {k: str(v) for k, v in reference.dtypes.items()}
            if case["rpc_local_index_equal"]:
                equal = rpc_frame.eq(reference) | (rpc_frame.isna() & reference.isna())
                case["rpc_local_different_cells"] = {k: int((~equal[k]).sum()) for k in FIELDS}
                case["rpc_local_max_absolute_difference"] = {
                    k: None if not len(reference) else float((rpc_frame[k] - reference[k]).abs().max())
                    for k in FIELDS
                }
            case["rpc_all_prices_zero"] = bool(len(rpc_frame) and
                (rpc_frame.loc[:, ["open", "high", "low", "close"]] == 0).all().all())
            daily = []
            for day in sorted({start, end}):
                value = xtdata.get_local_data(**{**params, "start_time": day, "end_time": day},
                                             fill_data=False)
                daily.append(value[symbol])
            case["single_day_concat_equals_range"] = pd.concat(daily).equals(local[False])
            report["cases"].append(case)
        report["status"] = "completed"
    except Exception as exc:
        # SDK/private configuration exceptions must never expose their messages.
        report["error_type"] = type(exc).__name__
    report["elapsed_seconds"] = round(monotonic() - started, 3)
    print(PREFIX + json.dumps(report, ensure_ascii=True, allow_nan=False), flush=True)
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
