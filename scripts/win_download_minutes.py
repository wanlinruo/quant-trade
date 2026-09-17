"""Windows 侧：按清单下载分钟线并导出 parquet。

**只在 Windows 上运行**（依赖 xtquant）。可中断可续传：每批落盘之后才把
「标的 + 日期」记入 done.csv，重跑跳过（D-18）。

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
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

COLUMNS = ["symbol", "dt", "open", "high", "low", "close", "volume", "amount"]


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


def read_done(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as handle:
        return {line.strip() for line in handle if line.strip()}


def ledger_key(symbol: str, stamps: list[str]) -> str:
    """续传账本的键：标的 + 本次要的全部日期（D-18：只按标的记会跳过同标的的其他日期）。"""
    return f"{symbol}:{'|'.join(sorted(stamps))}"


def to_rows(symbol: str, data: dict, stamps: list[str]) -> list[tuple]:
    """把 xtdata 的 {字段: DataFrame} 摊平成行。"""
    closes = data.get("close")
    if closes is None or closes.empty:
        return []

    columns = [c for c in closes.columns if c[:8] in stamps]
    if not columns:
        return []

    opens, highs = data["open"], data["high"]
    lows, volumes, amounts = data["low"], data["volume"], data["amount"]
    rows = []
    for stamp in columns:
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


def flush(rows: list[tuple], out_dir: str, index: int) -> str:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {name: [r[i] for r in rows] for i, name in enumerate(COLUMNS)},
        schema=_arrow_schema(),
    )
    path = os.path.join(out_dir, f"minutes_{index:05d}.parquet")
    pq.write_table(table, path)
    return path


def _next_index(out_dir: str) -> int:
    taken = [int(name[8:13]) for name in os.listdir(out_dir)
             if name.startswith("minutes_") and name.endswith(".parquet") and name[8:13].isdigit()]
    return max(taken, default=-1) + 1


def run_batch(fetch_one, wanted: dict[str, list[str]], todo: list[str], out_dir: str,
              *, flush_every: int = 100, sleep: float = 0.0) -> dict[str, int]:
    """逐只下载，每 `flush_every` 只落盘一次，**落盘之后**才把这批记进 done.csv（D-18）。

    空数据（停牌整段、该日无数据）也记账，重跑不再请求；失败不记账，留给重跑。
    """
    os.makedirs(out_dir, exist_ok=True)
    done_path = os.path.join(out_dir, "done.csv")
    failed_path = os.path.join(out_dir, "failed.csv")
    stats = {"ok": 0, "empty": 0, "failed": 0, "rows": 0, "files": 0}
    buffer: list[tuple] = []
    pending: list[str] = []
    index = _next_index(out_dir)
    started = time.time()

    def commit() -> None:
        nonlocal buffer, pending, index
        if buffer:
            path = flush(buffer, out_dir, index)
            print(f"  写出 {os.path.basename(path)}  {len(buffer):,} 行", flush=True)
            stats["rows"] += len(buffer)
            stats["files"] += 1
            index += 1
        if pending:
            with open(done_path, "a", encoding="utf-8") as handle:
                handle.writelines(key + "\n" for key in pending)
        buffer, pending = [], []

    for position, symbol in enumerate(todo, 1):
        stamps = wanted[symbol]
        try:
            rows = fetch_one(symbol, stamps)
        except Exception as exc:  # 单只失败不中断整轮，也不记账
            stats["failed"] += 1
            with open(failed_path, "a", encoding="utf-8") as handle:
                handle.write(f"{symbol},{type(exc).__name__},{exc}\n")
            print(f"  [失败] {symbol}: {type(exc).__name__}: {exc}", flush=True)
            continue
        stats["ok" if rows else "empty"] += 1
        buffer.extend(rows)
        pending.append(ledger_key(symbol, stamps))
        if position % flush_every == 0:
            commit()
            elapsed = time.time() - started
            rate = position / elapsed if elapsed else 0
            left = (len(todo) - position) / rate if rate else 0
            print(f"  {position}/{len(todo)}  有数据 {stats['ok']} 空 {stats['empty']} "
                  f"失败 {stats['failed']}  {rate:.1f} 只/秒  剩余约 {left/60:.0f} 分钟", flush=True)
        if sleep:
            time.sleep(sleep)
    commit()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="按清单下载分钟线")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--window-start", required=True, help="YYYYMMDD")
    parser.add_argument("--window-end", required=True, help="YYYYMMDD")
    parser.add_argument("--limit", type=int, default=0, help="只做前 N 只，用于试跑")
    parser.add_argument("--flush-every", type=int, default=100,
                        help="每做完这么多只落盘并记账一次")
    parser.add_argument("--batch", action="store_true",
                        help="按日先用 download_history_data2 批量下载整批标的（全市场采集）")
    parser.add_argument("--sleep", type=float, default=0.0,
                        help="每只之间歇多久，给同机的其它进程让路")
    parser.add_argument("--whole-window", action="store_true",
                        help="按标的整年下载。快，但会把整年灌进 QMT 本地缓存——"
                             "磁盘不宽裕时不要用")
    args = parser.parse_args()

    from xtquant import xtdata

    os.makedirs(args.out, exist_ok=True)
    wanted = read_manifest(args.manifest)
    done = read_done(os.path.join(args.out, "done.csv"))
    todo = [s for s in sorted(wanted) if ledger_key(s, wanted[s]) not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"清单 {len(wanted)} 只 / 已完成 {len(done)} 只 / 本次 {len(todo)} 只")
    if not todo:
        print("没有要做的")
        return 0

    if args.batch:
        # 按日批量：先让 QMT 一次性下好整批标的，再逐只取数（D-20 全市场）
        days = sorted({stamp for symbol in todo for stamp in wanted[symbol]})
        for stamp in days:
            chunk = [s for s in todo if stamp in wanted[s]]
            started = time.time()
            xtdata.download_history_data2(chunk, period="1m", start_time=stamp, end_time=stamp)
            print(f"  批量下载 {stamp} {len(chunk)} 只 {time.time()-started:.1f}s", flush=True)

    def fetch_one(symbol: str, stamps: list[str]) -> list[tuple]:
        if not args.batch:
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
        data = xtdata.get_market_data(
            field_list=[], stock_list=[symbol], period="1m",
            start_time=min(stamps), end_time=max(stamps),
            dividend_type="none",
        )
        return to_rows(symbol, data, stamps) if isinstance(data, dict) else []

    started = time.time()
    stats = run_batch(fetch_one, wanted, todo, args.out, flush_every=args.flush_every,
                      sleep=args.sleep)
    print(f"完成：有数据 {stats['ok']} 只 / 空 {stats['empty']} 只 / 失败 {stats['failed']} 只 / "
          f"{stats['rows']:,} 行 / {stats['files']} 个文件")
    print(f"耗时 {(time.time()-started)/60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())
