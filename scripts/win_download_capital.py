"""Windows 侧：下载全市场股本历史并导出 parquet。

**只在 Windows 上运行**（依赖 xtquant）。可中断可续传。

实测：`download_financial_data` 0.11 秒/只，全市场约 10 分钟，磁盘约 4 MB。

`m_timetag` 是股本**生效日**，`m_anntime` 是公告日。两者都落盘——市值按
生效日算（股本是公开可即时观察的事实，交易所每日据此发布总市值），
公告日留作更严格 PIT 口径的口子。

抽样 15 只全部有股本变动，且粒度是事件级（增发/解禁/回购注销都有独立
记录），所以必须按记录逐条落盘，不能只取季末快照。

用法：
  C:\\qsys\\.venv\\Scripts\\python.exe scripts\\win_download_capital.py ^
      --symbols C:\\qsys\\symbols.csv --out C:\\qsys\\capital
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime

COLUMNS = ["symbol", "dt", "ann_date", "total", "circulating"]


def _arrow_schema():
    import pyarrow as pa

    return pa.schema(
        [
            ("symbol", pa.string()),
            ("dt", pa.date32()),
            ("ann_date", pa.date32()),
            ("total", pa.float64()),
            ("circulating", pa.float64()),
        ]
    )


def _as_date(stamp):
    text = str(stamp).strip()
    if len(text) < 8 or not text[:8].isdigit():
        return None
    try:
        return datetime.strptime(text[:8], "%Y%m%d").date()
    except ValueError:
        return None


def read_symbols(path: str) -> list[str]:
    with open(path, newline="", encoding="utf-8") as handle:
        return [row["symbol"] for row in csv.DictReader(handle) if row.get("symbol")]


def read_done(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as handle:
        return {line.strip() for line in handle if line.strip()}


def flush(rows, out_dir, index):
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {name: [r[i] for r in rows] for i, name in enumerate(COLUMNS)},
        schema=_arrow_schema(),
    )
    path = os.path.join(out_dir, f"capital_{index:05d}.parquet")
    pq.write_table(table, path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="下载全市场股本历史")
    parser.add_argument("--symbols", required=True, help="含 symbol 列的 csv")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch", type=int, default=200, help="每批下载多少只")
    parser.add_argument("--flush-rows", type=int, default=100_000)
    args = parser.parse_args()

    from xtquant import xtdata

    os.makedirs(args.out, exist_ok=True)
    done_path = os.path.join(args.out, "done.csv")
    done = read_done(done_path)
    todo = [s for s in read_symbols(args.symbols) if s not in done]
    if args.limit:
        todo = todo[: args.limit]

    print(f"待下载 {len(todo)} 只（已完成 {len(done)}）")
    if not todo:
        return 0

    buffer: list[tuple] = []
    file_index = len([f for f in os.listdir(args.out) if f.endswith(".parquet")])
    started = time.time()
    ok = empty = 0

    for offset in range(0, len(todo), args.batch):
        chunk = todo[offset : offset + args.batch]
        try:
            xtdata.download_financial_data(chunk, table_list=["Capital"])
            data = xtdata.get_financial_data(
                chunk,
                table_list=["Capital"],
                start_time="20200101",
                end_time="20991231",
                report_type="report_time",
            )
        except Exception as exc:  # 整批失败也不中断，下批继续
            print(f"  [失败] 批 {offset}: {type(exc).__name__}: {exc}", flush=True)
            continue

        for symbol in chunk:
            frame = (data.get(symbol) or {}).get("Capital")
            if frame is None or len(frame) == 0:
                empty += 1
            else:
                for _, row in frame.iterrows():
                    effective = _as_date(row.get("m_timetag"))
                    if effective is None:
                        continue
                    buffer.append(
                        (
                            symbol,
                            effective,
                            _as_date(row.get("m_anntime")),
                            float(row.get("total_capital") or 0.0),
                            float(row.get("circulating_capital") or 0.0),
                        )
                    )
                ok += 1
            with open(done_path, "a", encoding="utf-8") as handle:
                handle.write(symbol + "\n")

        if len(buffer) >= args.flush_rows:
            name = os.path.basename(flush(buffer, args.out, file_index))
            print(f"  写出 {name}  {len(buffer):,} 行", flush=True)
            file_index += 1
            buffer = []

        elapsed = time.time() - started
        seen = offset + len(chunk)
        rate = seen / elapsed if elapsed else 0
        left = (len(todo) - seen) / rate / 60 if rate else 0
        print(
            f"  {seen}/{len(todo)}  有数据 {ok} 空 {empty}  "
            f"{rate:.0f} 只/秒  剩余约 {left:.0f} 分钟",
            flush=True,
        )

    if buffer:
        name = os.path.basename(flush(buffer, args.out, file_index))
        print(f"  写出 {name}  {len(buffer):,} 行")
    print(
        f"完成：有数据 {ok} 只 / 空 {empty} 只 / 耗时 {(time.time()-started)/60:.1f} 分钟"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
