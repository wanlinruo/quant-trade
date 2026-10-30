"""Windows 侧：按清单下载分钟线并导出 parquet。

**只在 Windows 上运行**（依赖 xtquant）。可中断可续传：Parquet 成功落盘后才把
已完成的「标的 + 日期」记入 done.csv，重跑只跳过已经持久化的股日；空结果不记账。

策略依据 2026-09-09 的实测：
  · 按标的整年下载 0.69s/只，比按股-日（0.60s/组）**看起来**更快
  · **但整年下载会撑爆磁盘**：QMT 把下到的数据全留在 userdata_mini，
    实测 1056 只就占了 4.8 GB（4.5 MB/只），3515 只要约 16 GB，
    而这台机器只剩 15.9 GB，且它跑着实盘通道，磁盘写满会一起出事
  · 我们只需要 13,622 个股-日，而整年是 3515 × 242 = 850,000 个股-日——
    **要的只占 1.6%**。故默认**按股-日下载**（--whole-window 可切回整年）
  · QMT 分钟线只有约一年（实测最早 2025-09-09），且是滚动窗口——
    每过一天就永久丢一天，必须每日增量归档

导出的 parquet 直接符合 qsys 的 MINUTE_BARS，Linux 侧 import_minute_bars
不做任何格式转换。

2026-10-30（#550/#478）候选入口：下载后使用已实测的 get_local_data，显式
fill_data=False/count=-1/dividend_type=none；本地原生零量和缺失值保留，不回退RPC。

**dt 为该分钟的收盘时刻**，与 QMT 原生一致，不做换算。实测判据：
11:30 存在而 13:00 不存在——若 QMT 按开盘时刻标注必然相反。
当日 241 根 = 09:30 开盘竞价 1 根 + 上午 120 根 + 下午 120 根。

用法（Windows）：
  C:\\qsys\\.venv\\Scripts\\python.exe scripts\\win_download_minutes.py ^
      --manifest C:\\qsys\\minute_manifest.csv --out C:\\qsys\\outgoing ^
      --window-start 20250909 --window-end 20260908
  加 --limit 50 先试小批。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

COLUMNS = ["symbol", "dt", "open", "high", "low", "close", "volume", "amount"]
PROGRESS_PREFIX = "QSYS_PROGRESS "
RESULT_SCHEMA_VERSION = 1


def atomic_write_json(path: str, payload: dict) -> None:
    """结果与进度必须原子替换，Linux 永远不能读到半个 JSON。"""
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def publish_progress(path: str, payload: dict) -> None:
    atomic_write_json(path, payload)
    print(PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)


def _arrow_schema():
    import pyarrow as pa

    return pa.schema(
        [
            ("symbol", pa.string()),
            ("dt", pa.timestamp("us")),
            ("open", pa.float64()),
            ("high", pa.float64()),
            ("low", pa.float64()),
            ("close", pa.float64()),
            ("volume", pa.int64()),
            ("amount", pa.float64()),
        ]
    )


def read_manifest(path: str) -> dict[str, list[str]]:
    """symbol -> [YYYYMMDD, ...]"""
    wanted: dict[str, list[str]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            wanted[row["symbol"]].append(row["dt"].replace("-", ""))
    return dict(wanted)


def read_done(path: str) -> set[tuple[str, str]]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as handle:
        done: set[tuple[str, str]] = set()
        for line in handle:
            parts = [part.strip() for part in line.split(",")]
            if len(parts) == 2 and parts[0] and parts[1]:
                done.add((parts[0], parts[1].replace("-", "")))
        return done


def pending_manifest(
    wanted: dict[str, list[str]], done: set[tuple[str, str]],
) -> dict[str, list[str]]:
    """只跳过已经按「证券 + 日期」落盘完成的股日。"""
    return {
        symbol: [stamp for stamp in stamps if (symbol, stamp) not in done]
        for symbol, stamps in wanted.items()
    }


def append_done(path: str, keys: list[tuple[str, str]]) -> None:
    """Parquet 成功落盘后才追加账本；反过来会在崩溃时制造假完成。"""
    with open(path, "a", encoding="utf-8") as handle:
        for symbol, stamp in keys:
            handle.write(f"{symbol},{stamp}\n")


def to_rows(symbol: str, data: dict, stamps: list[str]) -> list[tuple]:
    """历史RPC响应复放：把 {字段: DataFrame} 摊平成行。

    生产入口已改用 to_local_rows；不得把这段历史过滤器套在本地原生False返回值上。

    **丢弃每个交易日首笔成交之前的行。** `get_market_data` 按区间取数时，会把当日
    首笔成交之前的分钟用昨收价填满、`volume=0`；按单日取则不会。那些行是填充，
    不是行情——它们看起来是一笔可成交的价格，而市场上并没有发生过。
    详见 docs/reference/qmt-range-query-padding.md（2026-10-04）。

    首笔成交**之后**的 0 量分钟保留：那是真实的无成交分钟。
    """
    closes = data.get("close")
    if closes is None or closes.empty:
        return []

    columns = [c for c in closes.columns if c[:8] in stamps]
    if not columns:
        return []

    opens, highs = data["open"], data["high"]
    lows, volumes, amounts = data["low"], data["volume"], data["amount"]

    # 时间戳是 YYYYMMDDHHMMSS，字典序即时序。
    columns = sorted(columns)
    first_traded: dict[str, str] = {}
    for stamp in columns:
        day = stamp[:8]
        if day not in first_traded and int(volumes.iloc[0][stamp]) > 0:
            first_traded[day] = stamp

    rows = []
    for stamp in columns:
        start = first_traded.get(stamp[:8])
        if start is None or stamp < start:
            continue
        moment = datetime.strptime(stamp, "%Y%m%d%H%M%S")
        rows.append(
            (
                symbol,
                moment,
                float(opens.iloc[0][stamp]),
                float(highs.iloc[0][stamp]),
                float(lows.iloc[0][stamp]),
                float(closes.iloc[0][stamp]),
                int(volumes.iloc[0][stamp]),
                float(amounts.iloc[0][stamp]),
            )
        )
    return rows


def to_local_rows(symbol: str, data: dict, stamps: list[str]) -> list[tuple]:
    """适配已实测的原生本地容器；不填值、不截断成交量、不按零量删行。"""
    import pandas as pd

    if not isinstance(data, dict) or set(data) != {symbol}:
        raise ValueError("unexpected local minute response identity")
    frame = data[symbol]
    fields = ["time", *COLUMNS[2:]]
    if (not isinstance(frame, pd.DataFrame) or frame.columns.duplicated().any()
            or not set(fields) <= set(frame.columns) or frame.index.duplicated().any()):
        raise ValueError("invalid local minute frame structure")

    def number(value):
        value = value.item() if hasattr(value, "item") else value
        if value is None or pd.isna(value):
            return None
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("invalid local minute numeric value")
        return value

    def integer(value):
        value = number(value)
        if value is None:
            return None
        if int(value) != value or not -(2**63) <= value < 2**63:
            raise ValueError("local minute integer would lose source precision")
        return int(value)

    def floating(value):
        value = number(value)
        if value is None:
            return None
        if float(value) != value:
            raise ValueError("local minute float would lose source precision")
        return float(value)

    wanted = set(stamps)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    local_zone = timezone(timedelta(hours=8))
    rows = []
    # itertuples preserves integer scalar types; iterrows can coerce an entire row to float.
    for row in frame.loc[:, fields].sort_index().itertuples(index=True, name=None):
        stamp = str(row[0])
        moment = datetime.strptime(stamp, "%Y%m%d%H%M%S")
        if moment.strftime("%Y%m%d%H%M%S") != stamp:
            raise ValueError("invalid local minute index")
        if stamp[:8] not in wanted:
            continue
        source_time = integer(row[1])
        if source_time is None:
            raise ValueError("missing local minute source time")
        native_moment = (epoch + timedelta(milliseconds=source_time)).astimezone(local_zone)
        if native_moment.replace(tzinfo=None) != moment:
            raise ValueError("local minute time/index mismatch")
        prices = [floating(value) for value in row[2:6]]
        rows.append((symbol, moment, *prices, integer(row[6]), floating(row[7])))
    return rows


def flush(rows: list[tuple], out_dir: str, index: int) -> str:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {name: [r[i] for r in rows] for i, name in enumerate(COLUMNS)},
        schema=_arrow_schema(),
    )
    path = os.path.join(out_dir, f"minutes_{index:05d}.parquet")
    temporary = path + ".tmp"
    pq.write_table(table, temporary)
    os.replace(temporary, path)
    return path


def flush_checkpoint(
    rows: list[tuple], keys: list[tuple[str, str]],
    out_dir: str, index: int, done_path: str,
) -> str:
    """先持久化数据、再持久化完成键；失败时账本绝不能前进。"""
    path = flush(rows, out_dir, index)
    append_done(done_path, keys)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="按清单下载分钟线")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--window-start", required=True, help="YYYYMMDD")
    parser.add_argument("--window-end", required=True, help="YYYYMMDD")
    parser.add_argument("--run-id", default="manual",
                        help="Linux ServiceRun id；用于拒绝旧 result.json")
    parser.add_argument("--limit", type=int, default=0, help="只做前 N 只，用于试跑")
    parser.add_argument("--flush-rows", type=int, default=200_000,
                        help="攒够多少行写一个 parquet")
    parser.add_argument("--sleep", type=float, default=0.0,
                        help="每只之间歇多久，给同机的其它进程让路")
    parser.add_argument("--whole-window", action="store_true",
                        help="每只标的一次下载本次起止窗口。适合稠密的全市场短窗口；"
                             "长窗口会显著占用 QMT 本地缓存")
    parser.add_argument("--progress-every", type=int, default=25,
                        help="每处理多少只输出一次结构化进度")
    args = parser.parse_args()

    from xtquant import xtdata

    os.makedirs(args.out, exist_ok=True)
    done_path = os.path.join(args.out, "done.csv")
    failed_path = os.path.join(args.out, "failed.csv")
    progress_path = os.path.join(args.out, "progress.json")
    result_path = os.path.join(args.out, "result.json")
    # 旧 result 不能在本次异常退出时冒充新结果；进度文件随后会原子覆盖。
    if os.path.exists(result_path):
        os.remove(result_path)

    wanted = read_manifest(args.manifest)
    done = read_done(done_path)
    pending = pending_manifest(wanted, done)
    todo = [symbol for symbol in sorted(pending) if pending[symbol]]
    if args.limit:
        todo = todo[: args.limit]

    wanted_days = sum(len(stamps) for stamps in wanted.values())
    pending_days = sum(len(pending[symbol]) for symbol in todo)
    completed_before = sum(
        (symbol, stamp) in done
        for symbol, stamps in wanted.items() for stamp in stamps
    )
    print(f"清单 {len(wanted)} 只、{wanted_days} 股日 / 已完成 {completed_before} 股日 / "
          f"本次 {len(todo)} 只、{pending_days} 股日")
    started = time.time()
    attempted_days = downloaded_days = persisted_days = 0
    empty_keys: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    parquet_files = 0
    processed_symbols = 0

    def progress(status: str = "running") -> dict:
        elapsed = max(time.time() - started, 0.001)
        rate = processed_symbols / elapsed
        remaining = ((len(todo) - processed_symbols) / rate) if rate else None
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "source_api": "xtdata.get_local_data",
            "fill_data": False,
            "run_id": args.run_id,
            "status": status,
            "total_symbols": len(todo),
            "completed_symbols": processed_symbols,
            "total_stock_days": wanted_days,
            "completed_stock_days": completed_before + attempted_days,
            "downloaded_stock_days": downloaded_days,
            "persisted_stock_days": completed_before + persisted_days,
            "empty_stock_days": len(empty_keys),
            "failed_symbols": len(failures),
            "parquet_files": parquet_files,
            "last_progress_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "estimated_remaining_seconds": round(remaining) if remaining is not None else None,
        }

    publish_progress(progress_path, progress())
    if not todo:
        result = {
            **progress("succeeded"),
            "downloaded": 0,
            "empty": [],
            "failed": [],
        }
        atomic_write_json(result_path, result)
        publish_progress(progress_path, result)
        print("没有要做的")
        return 0

    buffer: list[tuple] = []
    buffer_done: list[tuple[str, str]] = []
    file_index = len(
        [f for f in os.listdir(args.out) if f.endswith(".parquet")]
    )
    ok = failed = empty = 0

    for position, symbol in enumerate(todo, 1):
        stamps = pending[symbol]
        try:
            if args.whole_window:
                xtdata.download_history_data(
                    symbol, period="1m",
                    start_time=args.window_start, end_time=args.window_end,
                )
            else:
                # 只下要的那几天。QMT 会把下到的一切留在 userdata_mini，
                # 整年下载会把 850,000 个股-日灌进磁盘，而我们只要 13,622 个。
                for stamp in stamps:
                    xtdata.download_history_data(
                        symbol, period="1m", start_time=stamp, end_time=stamp
                    )
            data = xtdata.get_local_data(
                field_list=["time", *COLUMNS[2:]], stock_list=[symbol], period="1m",
                start_time=min(stamps), end_time=max(stamps),
                count=-1, dividend_type="none", fill_data=False,
            )
            rows = to_local_rows(symbol, data, stamps)
        except Exception as exc:  # 单只失败不中断整轮
            failed += 1
            attempted_days += len(stamps)
            failures.append({
                "symbol": symbol,
                "error": type(exc).__name__,
            })
            with open(failed_path, "a", encoding="utf-8") as handle:
                handle.write(f"{symbol},{type(exc).__name__}\n")
            print(f"  [失败] {symbol}: {type(exc).__name__}", flush=True)
            processed_symbols = position
            publish_progress(progress_path, progress())
            continue

        completed = sorted({row[1].strftime("%Y%m%d") for row in rows})
        missing_stamps = sorted(set(stamps) - set(completed))
        attempted_days += len(stamps)
        downloaded_days += len(completed)
        if rows:
            buffer.extend(rows)
            buffer_done.extend((symbol, stamp) for stamp in completed)
            ok += 1
        if missing_stamps:
            empty += len(missing_stamps)
            empty_keys.extend(
                {"symbol": symbol, "dt": stamp} for stamp in missing_stamps
            )
            print(f"  [空] {symbol}: {','.join(missing_stamps)}", flush=True)

        if len(buffer) >= args.flush_rows:
            path = flush_checkpoint(
                buffer, buffer_done, args.out, file_index, done_path
            )
            print(f"  写出 {os.path.basename(path)}  {len(buffer):,} 行", flush=True)
            file_index += 1
            parquet_files += 1
            persisted_days += len(buffer_done)
            buffer = []
            buffer_done = []

        processed_symbols = position
        if position % max(args.progress_every, 1) == 0 or missing_stamps:
            elapsed = time.time() - started
            rate = position / elapsed
            left = (len(todo) - position) / rate if rate else 0
            print(
                f"  {position}/{len(todo)}  有数据 {ok} 只 空 {empty} 股日 "
                f"失败 {failed} 只  "
                f"{rate:.1f} 只/秒  剩余约 {left/60:.0f} 分钟",
                flush=True,
            )
            publish_progress(progress_path, progress())

        if args.sleep:
            time.sleep(args.sleep)

    if buffer:
        persisted_now = len(buffer_done)
        path = flush_checkpoint(buffer, buffer_done, args.out, file_index, done_path)
        print(f"  写出 {os.path.basename(path)}  {len(buffer):,} 行")
        parquet_files += 1
        persisted_days += persisted_now

    final_status = "failed" if failures else (
        "completed_with_empty" if empty_keys else "succeeded"
    )
    result = {
        **progress(final_status),
        "downloaded": downloaded_days,
        "empty": empty_keys,
        "failed": failures,
    }
    atomic_write_json(result_path, result)
    publish_progress(progress_path, result)
    print(f"完成：有数据 {ok} 只 / 空 {empty} 股日 / 失败 {failed} 只")
    print(f"耗时 {(time.time()-started)/60:.1f} 分钟")
    # 空结果交给 Linux 用权威停牌真值分类；只有单股异常才是下载器失败。
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
