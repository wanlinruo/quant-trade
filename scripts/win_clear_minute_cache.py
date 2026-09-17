"""Windows 侧：删除指定标的的 QMT 1 分钟缓存文件（D-20）。

**只在 Windows 上运行。** Linux 侧入库并逐日校验通过后才调用。只删
`userdata_mini\\datadir\\{SH,SZ,BJ}\\60\\<代码>.DAT`，不碰 tick、日线、日志等其他缓存。
2026-09-17 实测：QMT 运行中删除不被锁，删除后重下数据逐位一致，行情网关不受影响。

用法：python win_clear_minute_cache.py --symbols C:\\qsys\\daily\\x\\symbols.csv
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import sys


def cache_path(datadir: str, symbol: str) -> str:
    code, market = symbol.split(".")
    return os.path.join(datadir, market, "60", f"{code}.DAT")


def main() -> int:
    parser = argparse.ArgumentParser(description="删除 QMT 1 分钟缓存")
    parser.add_argument("--symbols", required=True, help="含 symbol 列的 csv")
    args = parser.parse_args()

    roots = glob.glob(r"C:\self\*\userdata_mini\datadir")
    if len(roots) != 1:
        print(f"找不到唯一的 QMT datadir: {roots}")
        return 2
    with open(args.symbols, newline="", encoding="utf-8") as handle:
        symbols = sorted({row["symbol"].strip() for row in csv.DictReader(handle) if row.get("symbol")})
    removed = freed = missing = failed = 0
    for symbol in symbols:
        path = cache_path(roots[0], symbol)
        if not os.path.exists(path):
            missing += 1
            continue
        try:
            size = os.path.getsize(path)
            os.remove(path)
            removed += 1
            freed += size
        except OSError as exc:
            failed += 1
            print(f"  [失败] {symbol}: {exc}")
    print(f"删除 {removed} 个 / 不存在 {missing} 个 / 失败 {failed} 个 / 释放 {freed/1e6:.1f} MB")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
