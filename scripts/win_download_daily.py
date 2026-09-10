"""Windows 侧：从 QMT 补采日线，用于填 mootdx 的静默缺口。

**只在 Windows 上运行。** 由 Linux 侧驱动。

为什么要补：mootdx 返回的日线序列会静默少掉某些天——采集日志报 0 失败，
但序列里就是没有那一天。实测 2023-01 ~ 2026-09 全市场有 **5,008 个内部
空洞**（前后都有 bar、中间那天没有），占 0.125%。2026-04-30 因此比聚宽
少 41 只，直接翻转了当天的 `market_advance_ratio` Gate 判定。

QMT 的日线带 `suspendFlag`，能同时做两件事：**补回真实交易日**，以及
**把真停牌与采集缺口区分开**——真停牌两边都该没有，采集缺口才是 bug。

**单位**：QMT 与 mootdx 的日线 volume 都是**手**。脚本启动时会用一只
已知标的做自检，对不上就直接退出，不写任何数据。

用法（Windows PowerShell）：
  C:\\qsys\\.venv\\Scripts\\python.exe -u C:\\qsys\\win_download_daily.py `
      --symbols C:\\qsys\\daily_symbols.csv --out C:\\qsys\\daily_out `
      --start 20230101 --end 20260909
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime

COLUMNS = ["symbol", "dt", "open", "high", "low", "close", "volume", "amount",
           "suspend_flag"]

# 自检基准：600036.SH 2026-09-08，mootdx 日线 volume 493,101 手
_CHECK_CODE = "600036.SH"
_CHECK_DAY = "20260908"
_CHECK_VOLUME_LOTS = 493_101
_CHECK_TOLERANCE = 0.001   # 千分之一


def _arrow_schema():
    import pyarrow as pa

    return pa.schema([
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.int64()),
        ("amount", pa.float64()),
        ("suspend_flag", pa.int64()),
    ])


def unit_selfcheck(xtdata) -> bool:
    """确认 QMT 日线的 volume 单位与仓库一致（手）。对不上就不要写数据。"""
    xtdata.download_history_data(_CHECK_CODE, period="1d",
                                 start_time=_CHECK_DAY, end_time=_CHECK_DAY)
    data = xtdata.get_market_data(
        field_list=["volume", "amount", "close"], stock_list=[_CHECK_CODE],
        period="1d", start_time=_CHECK_DAY, end_time=_CHECK_DAY,
        dividend_type="none",
    )
    if not isinstance(data, dict) or "volume" not in data:
        print("[自检] 取不到数据，无法确认单位")
        return False
    volume = float(data["volume"].iloc[0].iloc[0])
    close = float(data["close"].iloc[0].iloc[0])
    amount = float(data["amount"].iloc[0].iloc[0])
    off = abs(volume - _CHECK_VOLUME_LOTS) / _CHECK_VOLUME_LOTS
    vwap_lots = amount / (volume * 100) if volume else 0
    print(f"[自检] {_CHECK_CODE} {_CHECK_DAY}  QMT volume={volume:,.0f}  "
          f"仓库(手)={_CHECK_VOLUME_LOTS:,}  偏差 {off*100:.4f}%")
    print(f"       amount/(volume*100)={vwap_lots:.4f}  close={close}  "
          f"（同量级则单位是手）")
    if off > _CHECK_TOLERANCE:
        print("[自检] **单位不一致，拒绝写入**")
        return False
    print("[自检] 通过")
    return True


def fetch(xtdata, code: str, start: str, end: str) -> list[dict]:
    xtdata.download_history_data(code, period="1d", start_time=start, end_time=end)
    data = xtdata.get_market_data(
        field_list=["open", "high", "low", "close", "volume", "amount",
                    "suspendFlag"],
        stock_list=[code], period="1d", start_time=start, end_time=end,
        dividend_type="none",
    )
    if not isinstance(data, dict) or "close" not in data:
        return []

    closes = data["close"]
    if code not in closes.index:
        return []
    stamps = list(closes.columns)

    def series(name):
        frame = data.get(name)
        return frame.loc[code] if frame is not None and code in frame.index else None

    o, h, low_, c = (series(x) for x in ("open", "high", "low", "close"))
    v, a, sf = (series(x) for x in ("volume", "amount", "suspendFlag"))

    rows = []
    for stamp in stamps:
        close = float(c[stamp]) if c is not None else 0.0
        # QMT 对停牌/无数据日返回 0 价的占位行——不要写进仓库
        if close <= 0:
            continue
        rows.append({
            "symbol": code,
            "dt": datetime.strptime(str(stamp)[:8], "%Y%m%d").date(),
            "open": float(o[stamp]), "high": float(h[stamp]),
            "low": float(low_[stamp]), "close": close,
            "volume": int(v[stamp]), "amount": float(a[stamp]),
            "suspend_flag": int(sf[stamp]) if sf is not None else 0,
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="从 QMT 补采日线")
    parser.add_argument("--symbols", required=True, help="每行一个标的的 csv")
    parser.add_argument("--out", required=True)
    parser.add_argument("--start", required=True, help="YYYYMMDD")
    parser.add_argument("--end", required=True, help="YYYYMMDD")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--flush-rows", type=int, default=300_000)
    parser.add_argument("--sleep", type=float, default=0.0)
    args = parser.parse_args()

    import pyarrow as pa
    import pyarrow.parquet as pq
    from xtquant import xtdata

    if not unit_selfcheck(xtdata):
        return 2

    out = args.out
    os.makedirs(out, exist_ok=True)
    done_path = os.path.join(out, "done.csv")
    done = set()
    if os.path.exists(done_path):
        with open(done_path, encoding="utf-8") as handle:
            done = {line.strip() for line in handle if line.strip()}

    with open(args.symbols, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        first = next(reader, None)
        codes = []
        if first and first[0].strip().lower() not in ("symbol", "stock", "code"):
            codes.append(first[0].strip())
        codes += [r[0].strip() for r in reader if r and r[0].strip()]

    todo = [c for c in codes if c not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"清单 {len(codes)} 只 / 已完成 {len(done)} 只 / 待做 {len(todo)} 只")

    buffer: list[dict] = []
    written = files = failed = 0
    started = time.time()

    def flush():
        nonlocal buffer, written, files
        if not buffer:
            return
        path = os.path.join(out, f"daily_{files:05d}.parquet")
        table = pa.table({k: [r[k] for r in buffer] for k in COLUMNS},
                         schema=_arrow_schema())
        pq.write_table(table, path)
        print(f"  写出 {os.path.basename(path)}  {len(buffer):,} 行")
        written += len(buffer)
        files += 1
        buffer = []

    with open(done_path, "a", encoding="utf-8") as ledger:
        for n, code in enumerate(todo, 1):
            try:
                rows = fetch(xtdata, code, args.start, args.end)
            except Exception as exc:  # 单只失败不中断整批
                failed += 1
                print(f"  [失败] {code}: {exc}")
                continue
            buffer.extend(rows)
            ledger.write(code + "\n")
            ledger.flush()
            if n % 100 == 0:
                print(f"  {n}/{len(todo)}  累计 {written + len(buffer):,} 行  "
                      f"{time.time() - started:.0f}s")
            if len(buffer) >= args.flush_rows:
                flush()
            if args.sleep:
                time.sleep(args.sleep)
    flush()

    print(f"完成：成功 {len(todo) - failed} 只 / 失败 {failed} 只 / "
          f"{written:,} 行 / {files} 个文件   耗时 "
          f"{(time.time() - started) / 60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())
