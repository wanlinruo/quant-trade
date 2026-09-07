"""聚宽三层交叉验证：命令行入口。

需要从聚宽研究环境导出两个 CSV：
  prices.csv  列：symbol, dt, close        —— 用于 L1
  equity.csv  列：dt, total_value          —— 用于 L2 / L3

用法：
  uv run python scripts/compare_joinquant.py \
      --jq-prices prices.csv --jq-equity equity.csv \
      --our-equity our_equity.csv [--our-prices our_prices.csv]

L2 的正确做法是把聚宽导出的价格喂给自建引擎再跑一遍——
数据变量被消除后，净值差异才纯粹来自撮合逻辑。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

from qsys.backtest.compare import compare_equity, compare_prices, format_report


def _read(path: Path) -> pl.DataFrame:
    df = pl.read_csv(path, try_parse_dates=True)
    if "dt" not in df.columns:
        raise SystemExit(f"{path} 缺少 dt 列")
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="聚宽三层交叉验证")
    parser.add_argument("--jq-prices", type=Path, required=True)
    parser.add_argument("--jq-equity", type=Path, required=True)
    parser.add_argument("--our-equity", type=Path, required=True)
    parser.add_argument("--our-prices", type=Path)
    parser.add_argument("--tolerance", type=float, default=0.005)
    args = parser.parse_args()

    jq_prices = _read(args.jq_prices)
    our_prices = _read(args.our_prices) if args.our_prices else jq_prices
    price_diff = compare_prices(our_prices, jq_prices, tolerance=args.tolerance)
    equity_diff = compare_equity(_read(args.our_equity), _read(args.jq_equity))

    print(format_report(price_diff, equity_diff))
    if price_diff.n_mismatched:
        print("\nL1 不一致样本：")
        print(price_diff.samples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
