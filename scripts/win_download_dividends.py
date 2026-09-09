"""Windows 侧：下载全市场分红送配历史并导出 parquet。

**只在 Windows 上运行**（依赖 xtquant）。可中断可续传。

`xtdata.get_divid_factors(code)` 返回逐次除权除息记录，索引是除权日
（YYYYMMDD）：

| 字段 | 含义 |
|---|---|
| `interest` | 每股现金红利（元） |
| `stockBonus` | 每股送股 |
| `stockGift` | 每股转增 |
| `allotNum` | 每股配股数 |
| `allotPrice` | 配股价 |
| `dr` | 单次复权因子（用于交叉校验） |

**它含配股**——akshare 的 `stock_fhps_detail_em` 只有分红送转，
做过配股的标的复权因子会偏差（见 spec §12 风险表）。这条缺口由此关闭。

用法：
  C:\\qsys\\.venv\\Scripts\\python.exe scripts\\win_download_dividends.py ^
      --symbols C:\\qsys\\symbols.csv --out C:\\qsys\\dividends
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime

COLUMNS = [
    "symbol", "ex_date", "cash_per_share", "stock_per_share",
    "rights_per_share", "rights_price", "factor",
]


def _arrow_schema():
    import pyarrow as pa

    return pa.schema(
        [
            ("symbol", pa.string()),
            ("ex_date", pa.date32()),
            ("cash_per_share", pa.float64()),
            ("stock_per_share", pa.float64()),
            ("rights_per_share", pa.float64()),
            ("rights_price", pa.float64()),
            ("factor", pa.float64()),
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


def _num(value) -> float:
    """空值归零。NaN 是真值，`value or 0.0` 会原样放行。"""
    if value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if number != number else number


def read_symbols(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return [r["symbol"] for r in csv.DictReader(handle) if r.get("symbol")]


def read_done(path):
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
    path = os.path.join(out_dir, f"dividends_{index:05d}.parquet")
    pq.write_table(table, path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="下载全市场分红送配")
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
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

    buffer, file_index = [], len(
        [f for f in os.listdir(args.out) if f.endswith(".parquet")]
    )
    started = time.time()
    ok = empty = failed = 0

    for position, symbol in enumerate(todo, 1):
        try:
            frame = xtdata.get_divid_factors(symbol)
        except Exception as exc:  # 单只失败不中断整轮
            failed += 1
            print(f"  [失败] {symbol}: {type(exc).__name__}: {exc}", flush=True)
            continue

        if frame is None or len(frame) == 0:
            empty += 1
        else:
            for stamp, row in frame.iterrows():
                ex_date = _as_date(stamp)
                if ex_date is None:
                    continue
                buffer.append(
                    (
                        symbol, ex_date,
                        _num(row.get("interest")),
                        _num(row.get("stockBonus")) + _num(row.get("stockGift")),
                        _num(row.get("allotNum")),
                        _num(row.get("allotPrice")),
                        _num(row.get("dr")),
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

        if position % 500 == 0:
            elapsed = time.time() - started
            rate = position / elapsed if elapsed else 0
            left = (len(todo) - position) / rate / 60 if rate else 0
            print(
                f"  {position}/{len(todo)}  有数据 {ok} 空 {empty} 失败 {failed}  "
                f"{rate:.0f} 只/秒  剩余约 {left:.0f} 分钟",
                flush=True,
            )

    if buffer:
        name = os.path.basename(flush(buffer, args.out, file_index))
        print(f"  写出 {name}  {len(buffer):,} 行")
    print(
        f"完成：有数据 {ok} 只 / 空 {empty} 只 / 失败 {failed} 只 / "
        f"耗时 {(time.time()-started)/60:.1f} 分钟"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
