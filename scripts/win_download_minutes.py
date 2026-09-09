"""Windows 侧：按清单下载分钟线并导出 parquet。

**只在 Windows 上运行**（依赖 xtquant）。可中断可续传：已完成的标的记入
done.csv，重跑跳过。

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


def main() -> int:
    parser = argparse.ArgumentParser(description="按清单下载分钟线")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--window-start", required=True, help="YYYYMMDD")
    parser.add_argument("--window-end", required=True, help="YYYYMMDD")
    parser.add_argument("--limit", type=int, default=0, help="只做前 N 只，用于试跑")
    parser.add_argument("--flush-rows", type=int, default=200_000,
                        help="攒够多少行写一个 parquet")
    parser.add_argument("--sleep", type=float, default=0.0,
                        help="每只之间歇多久，给同机的其它进程让路")
    parser.add_argument("--whole-window", action="store_true",
                        help="按标的整年下载。快，但会把整年灌进 QMT 本地缓存——"
                             "磁盘不宽裕时不要用")
    args = parser.parse_args()

    from xtquant import xtdata

    os.makedirs(args.out, exist_ok=True)
    done_path = os.path.join(args.out, "done.csv")
    failed_path = os.path.join(args.out, "failed.csv")

    wanted = read_manifest(args.manifest)
    done = read_done(done_path)
    todo = [s for s in sorted(wanted) if s not in done]
    if args.limit:
        todo = todo[: args.limit]

    print(f"清单 {len(wanted)} 只 / 已完成 {len(done)} 只 / 本次 {len(todo)} 只")
    if not todo:
        print("没有要做的")
        return 0

    buffer: list[tuple] = []
    file_index = len(
        [f for f in os.listdir(args.out) if f.endswith(".parquet")]
    )
    started = time.time()
    ok = failed = empty = 0

    for position, symbol in enumerate(todo, 1):
        stamps = wanted[symbol]
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
            data = xtdata.get_market_data(
                field_list=[], stock_list=[symbol], period="1m",
                start_time=min(stamps), end_time=max(stamps),
                dividend_type="none",
            )
            rows = to_rows(symbol, data, stamps) if isinstance(data, dict) else []
        except Exception as exc:  # 单只失败不中断整轮
            failed += 1
            with open(failed_path, "a", encoding="utf-8") as handle:
                handle.write(f"{symbol},{type(exc).__name__},{exc}\n")
            print(f"  [失败] {symbol}: {type(exc).__name__}: {exc}", flush=True)
            continue

        if rows:
            buffer.extend(rows)
            ok += 1
        else:
            empty += 1  # 停牌整段或该日无数据，不是失败

        with open(done_path, "a", encoding="utf-8") as handle:
            handle.write(symbol + "\n")

        if len(buffer) >= args.flush_rows:
            path = flush(buffer, args.out, file_index)
            print(f"  写出 {os.path.basename(path)}  {len(buffer):,} 行", flush=True)
            file_index += 1
            buffer = []

        if position % 100 == 0:
            elapsed = time.time() - started
            rate = position / elapsed
            left = (len(todo) - position) / rate if rate else 0
            print(
                f"  {position}/{len(todo)}  有数据 {ok} 空 {empty} 失败 {failed}  "
                f"{rate:.1f} 只/秒  剩余约 {left/60:.0f} 分钟",
                flush=True,
            )

        if args.sleep:
            time.sleep(args.sleep)

    if buffer:
        path = flush(buffer, args.out, file_index)
        print(f"  写出 {os.path.basename(path)}  {len(buffer):,} 行")

    print(f"完成：有数据 {ok} 只 / 空 {empty} 只 / 失败 {failed} 只")
    print(f"耗时 {(time.time()-started)/60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())
