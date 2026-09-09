"""Windows 侧：按清单下载**集合竞价段**的 tick，导出 parquet。

**只在 Windows 上运行。** 由 Linux 的 `daily_archive.py` 通过 SSH 驱动。

只留 09:15:00~09:25:59 那一段——全天 tick 单只单日约 5,000 笔，竞价段
只有 45~202 笔，落盘差两个数量级。竞价段之外的 tick 我们用不上：
盘中退出用的是分钟线。

**存原始快照，不存派生结论**：`get_first_auction_record` 取第 1 条，
买点前移要用 09:20 之后的某一条。两种口径都得从同一份原始数据算。

可中断可续传：`done.csv` 记账，重跑只补没做完的。

用法（Windows PowerShell）：
  C:\\qsys\\.venv\\Scripts\\python.exe -u C:\\qsys\\win_download_auction_ticks.py `
      --manifest C:\\qsys\\auction_manifest.csv --out C:\\qsys\\auction_out
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))
AUCTION_START = (9, 15, 0)
AUCTION_END = (9, 25, 59)

COLUMNS = [
    "symbol", "dt", "ts", "bid1", "ask1",
    "bid_vol1", "ask_vol1", "last_price", "volume", "amount",
]


def _arrow_schema():
    """必须与 qsys.data.schema.AUCTION_TICKS 一致。

    **这台机器上只有 pyarrow，没有 polars**——与 win_download_minutes.py
    同一条约束，别引入新依赖。
    """
    import pyarrow as pa

    return pa.schema(
        [
            ("symbol", pa.string()),
            ("dt", pa.date32()),
            ("ts", pa.timestamp("us")),
            ("bid1", pa.float64()),
            ("ask1", pa.float64()),
            ("bid_vol1", pa.int64()),
            ("ask_vol1", pa.int64()),
            ("last_price", pa.float64()),
            ("volume", pa.int64()),
            ("amount", pa.float64()),
        ]
    )


def _level1(cell) -> float:
    try:
        return float(cell[0])
    except (TypeError, IndexError):
        return 0.0


def _in_auction(moment: datetime) -> bool:
    key = (moment.hour, moment.minute, moment.second)
    return AUCTION_START <= key <= AUCTION_END


def load_manifest(path: Path) -> dict[str, list[str]]:
    """清单 -> {标的: [YYYYMMDD, ...]}。按标的分组，一次下一段更快。"""
    grouped: dict[str, list[str]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = row["symbol"].strip()
            stamp = row["dt"].strip().replace("-", "")
            if symbol and stamp:
                grouped.setdefault(symbol, []).append(stamp)
    return {s: sorted(set(d)) for s, d in grouped.items()}


def fetch(xtdata, code: str, stamps: list[str]) -> list[dict]:
    """下这只标的这几天的 tick，只留竞价段。"""
    lo, hi = stamps[0], stamps[-1]
    xtdata.download_history_data(code, period="tick", start_time=lo, end_time=hi)
    data = xtdata.get_market_data(
        field_list=[], stock_list=[code], period="tick",
        start_time=lo, end_time=hi, dividend_type="none",
    )
    if not isinstance(data, dict) or code not in data:
        return []

    arr = data[code]
    wanted = set(stamps)
    rows: list[dict] = []
    for record in arr:
        moment = datetime.fromtimestamp(int(record["time"]) / 1000, CST)
        if not _in_auction(moment):
            continue
        day = moment.strftime("%Y%m%d")
        if day not in wanted:
            continue
        rows.append({
            "symbol": code,
            "dt": moment.date(),
            # 落盘写成 naive 本地时刻：仓库里所有时间戳都是北京时间无时区
            "ts": moment.replace(tzinfo=None),
            "bid1": _level1(record["bidPrice"]),
            "ask1": _level1(record["askPrice"]),
            "bid_vol1": int(_level1(record["bidVol"])),
            "ask_vol1": int(_level1(record["askVol"])),
            "last_price": float(record["lastPrice"]),
            "volume": int(record["volume"]),
            "amount": float(record["amount"]),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="下载集合竞价 tick")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0, help="只做前 N 只，用于试跑")
    parser.add_argument("--flush-rows", type=int, default=200_000)
    parser.add_argument("--sleep", type=float, default=0.0)
    args = parser.parse_args()

    import pyarrow.parquet as pq
    from xtquant import xtdata

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    done_path = out / "done.csv"
    done = set()
    if done_path.exists():
        done = {
            line.strip()
            for line in done_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    grouped = load_manifest(Path(args.manifest))
    todo = [s for s in sorted(grouped) if s not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"清单 {len(grouped)} 只 / 已完成 {len(done)} 只 / 待做 {len(todo)} 只")

    buffer: list[dict] = []
    written = files = failed = 0
    started = time.time()

    def flush() -> None:
        nonlocal buffer, written, files
        if not buffer:
            return
        import pyarrow as pa

        path = out / f"auction_{files:05d}.parquet"
        table = pa.table(
            {name: [row[name] for row in buffer] for name in COLUMNS},
            schema=_arrow_schema(),
        )
        pq.write_table(table, str(path))
        print(f"  写出 {path.name}  {len(buffer):,} 笔")
        written += len(buffer)
        files += 1
        buffer = []

    with done_path.open("a", encoding="utf-8") as ledger:
        for n, code in enumerate(todo, 1):
            try:
                rows = fetch(xtdata, code, grouped[code])
            except Exception as exc:  # 单只失败不中断整批
                failed += 1
                print(f"  [失败] {code}: {exc}")
                continue
            buffer.extend(rows)
            ledger.write(code + "\n")
            ledger.flush()
            if n % 50 == 0:
                print(f"  {n}/{len(todo)}  累计 {written + len(buffer):,} 笔  "
                      f"{time.time() - started:.0f}s")
            if len(buffer) >= args.flush_rows:
                flush()
            if args.sleep:
                time.sleep(args.sleep)
    flush()

    print(f"完成：成功 {len(todo) - failed} 只 / 失败 {failed} 只 / "
          f"{written:,} 笔 / {files} 个文件")
    print(f"耗时 {(time.time() - started) / 60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())
