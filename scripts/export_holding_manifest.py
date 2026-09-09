"""从一次回测的成交流水，导出**持仓期**还缺的分钟线清单。

候选清单（`export_minute_manifest.py`）只覆盖买入决策要的那几天。退出要的
是持仓期间每一天的日内报价——分钟线缺哪天，`on_checkpoint` 就在哪天拿不到
报价而跳过，持仓于是永久卡死，把满仓策略的现金抽干。

这是个**迭代**过程：补齐后重跑回测，持仓会变，可能又缺新的股-日。跑到
本脚本输出 0 行为止。收敛很快——退出正常时持仓只有 1~2 天。

用法：
  uv run python scripts/export_holding_manifest.py \
      --fills var/backtest_exact_fills.csv --out var/holding_manifest.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

from qsys.data.pipeline.candidates import holding_stock_days, write_manifest
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def main() -> int:
    parser = argparse.ArgumentParser(description="导出持仓期分钟线清单")
    parser.add_argument("--fills", required=True, help="回测产出的成交明细 csv")
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--out", default="var/holding_manifest.csv")
    parser.add_argument(
        "--window", type=int, default=30,
        help="买入后跟几个交易日；持仓超过这个天数说明退出本身有问题",
    )
    args = parser.parse_args()

    warehouse = Warehouse(Path(args.warehouse))
    query = WarehouseQuery(warehouse)

    fills = (
        pl.read_csv(args.fills, try_parse_dates=True)
        .with_columns(pl.col("filled_at").dt.date().alias("dt"))
    )
    sessions = sorted(query.market_regime()["dt"].to_list())
    wanted = holding_stock_days(fills, sessions, window=args.window)
    print(f"持仓期股-日 {wanted.height:,} 组（买入 {fills.filter(pl.col('side') == 'buy').height} 笔）")

    have = _existing(warehouse)
    missing = wanted.join(have, on=["symbol", "dt"], how="anti")
    print(f"已有 {wanted.height - missing.height:,} 组，还缺 {missing.height:,} 组")
    if missing.height:
        print(f"按 1.08 s/组 估约 {missing.height * 1.08 / 60:.0f} 分钟")

    rows = write_manifest(missing, Path(args.out))
    print(f"已写入 {args.out}（{rows:,} 行）")
    return 0


def _existing(warehouse: Warehouse) -> pl.DataFrame:
    """仓库里已有分钟线的（标的, 日期）。空仓库要返回**带类型的空表**，
    否则 anti-join 会因 schema 不匹配报错。"""
    import duckdb

    glob = str(Path(warehouse.root) / "minute_bars" / "**" / "*.parquet")
    schema = {"symbol": pl.Utf8, "dt": pl.Date}
    try:
        frame = duckdb.connect().execute(
            "select distinct symbol, cast(dt as date) as dt "
            f"from read_parquet('{glob}', union_by_name=true)"
        ).pl()
    except Exception as exc:  # 没有任何分区时 read_parquet 直接抛
        print(f"仓库暂无分钟线（{type(exc).__name__}），全部视为缺失")
        return pl.DataFrame(schema=schema)
    return frame.cast(schema)


if __name__ == "__main__":
    sys.exit(main())
