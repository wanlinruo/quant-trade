"""重建退市整理期池（上线回填或修复用；每日由 Tushare 同步的派生步骤刷新）。

候选池剔除只读这张表，池子没覆盖到的日子会显式报错，所以上线前必须回填到
回测会用到的最早日期，并包含下一个交易日。

用法：
  uv run python scripts/build_delisting_pool.py --warehouse warehouse --start 2016-01-01 \\
      --config config/deploy.local.toml
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from qsys.data.pipeline.delisting import build_delisting_pool
from qsys.data.storage import Warehouse
from qsys.paper.calendar import load_trading_calendar


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--config", default="config/deploy.local.toml",
                        help="取 Tushare 交易日历，用来一并建好下一个交易日")
    args = parser.parse_args()
    next_session = load_trading_calendar(args.config).next_session(args.end)
    rows = build_delisting_pool(Warehouse(args.warehouse), args.start, args.end,
                                extra_sessions=[next_session])
    print(f"{datetime.now():%H:%M:%S} 退市整理期池 {args.start} ~ {next_session}：{rows:,} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
