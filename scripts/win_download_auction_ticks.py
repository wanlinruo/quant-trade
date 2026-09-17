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


def ledger_key(code: str, stamps: list[str]) -> str:
    """续传账本的键：标的 + 本次要的全部日期。

    只记标的会出错：同一输出目录里先做过「09-16 那一天」的标的，重跑补
    更早日期时被当成已完成跳过，竞价数据就此过期（D-7）。
    """
    return f"{code}:{'|'.join(stamps)}"


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


def read_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()}


def _next_file_index(out: Path) -> int:
    """接着目录里已有的文件编号写。从 0 开始会覆盖上次崩溃前已落盘、尚未回传的文件。"""
    taken = [int(p.stem.split("_")[1]) for p in out.glob("auction_*.parquet")
             if p.stem.split("_")[1].isdigit()]
    return max(taken, default=-1) + 1


def run_batch(fetch_one, grouped: dict[str, list[str]], todo: list[str], out: Path,
              *, flush_every: int = 50, sleep: float = 0.0) -> dict[str, int]:
    """逐只下载，每 `flush_every` 只落盘一次，**落盘之后**才把这批记进 done.csv。

    2026-09-17 补采时进程在第 717 只后整体退出：旧实现先记账、攒到 20 万笔才写盘，
    结果 692 只被记成已完成、数据却一笔没落盘，重跑还会跳过它们（D-7）。
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    out.mkdir(parents=True, exist_ok=True)
    done_path = out / "done.csv"
    stats = {"ok": 0, "failed": 0, "written": 0, "files": 0}
    buffer: list[dict] = []
    pending: list[str] = []
    index = _next_file_index(out)
    started = time.time()

    def flush() -> None:
        nonlocal buffer, pending, index
        if buffer:
            path = out / f"auction_{index:05d}.parquet"
            table = pa.table({name: [row[name] for row in buffer] for name in COLUMNS},
                             schema=_arrow_schema())
            pq.write_table(table, str(path))
            print(f"  写出 {path.name}  {len(buffer):,} 笔")
            stats["written"] += len(buffer)
            stats["files"] += 1
            index += 1
        if pending:
            with done_path.open("a", encoding="utf-8") as ledger:
                ledger.writelines(key + "\n" for key in pending)
        buffer, pending = [], []

    for n, code in enumerate(todo, 1):
        try:
            rows = fetch_one(code, grouped[code])
        except Exception as exc:  # 单只失败不中断整批，也不记账
            stats["failed"] += 1
            print(f"  [失败] {code}: {exc}")
            continue
        stats["ok"] += 1
        buffer.extend(rows)
        pending.append(ledger_key(code, grouped[code]))
        if n % flush_every == 0:
            flush()
            print(f"  {n}/{len(todo)}  累计 {stats['written']:,} 笔  "
                  f"{time.time() - started:.0f}s")
        if sleep:
            time.sleep(sleep)
    flush()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="下载集合竞价 tick")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0, help="只做前 N 只，用于试跑")
    parser.add_argument("--flush-every", type=int, default=50,
                        help="每做完这么多只落盘并记账一次")
    parser.add_argument("--sleep", type=float, default=0.0)
    args = parser.parse_args()

    from xtquant import xtdata

    out = Path(args.out)
    done = read_done(out / "done.csv")
    grouped = load_manifest(Path(args.manifest))
    todo = [s for s in sorted(grouped) if ledger_key(s, grouped[s]) not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"清单 {len(grouped)} 只 / 已完成 {len(done)} 只 / 待做 {len(todo)} 只")

    started = time.time()
    stats = run_batch(lambda code, stamps: fetch(xtdata, code, stamps), grouped, todo, out,
                      flush_every=args.flush_every, sleep=args.sleep)
    print(f"完成：成功 {stats['ok']} 只 / 失败 {stats['failed']} 只 / "
          f"{stats['written']:,} 笔 / {stats['files']} 个文件")
    print(f"耗时 {(time.time() - started) / 60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())
