"""每日增量同步的命令行入口。逻辑在 qsys.data.pipeline.tushare_sync。

用法:
    uv run python scripts/tushare_daily_sync.py --warehouse /data/qsys/warehouse
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from qsys.data.pipeline.tushare_sync import completed_calendar_end, last_open_day, sync
from qsys.data.providers.tushare import TushareProvider
from qsys.data.providers.tushare_client import TushareClient
from qsys.data.storage import Warehouse
from qsys.live.config import load_tushare_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config", type=Path, default=Path("config/deploy.local.toml"))
    parser.add_argument("--day", type=date.fromisoformat, default=None,
                        help="指定交易日；不给则取最近一个已收盘交易日")
    args = parser.parse_args()

    client = TushareClient(load_tushare_config(args.config).token)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    # 16:00 前当日数据可能尚未结算；默认只允许选到前一自然日。
    calendar_end = completed_calendar_end(now)
    day = args.day or last_open_day(client, calendar_end)
    if day is None:
        print("最近 30 天没有交易日，退出")
        return 0

    counts = sync(TushareProvider(client), Warehouse(args.warehouse), day)
    print(f"=== {day} ===")
    for name, rows in counts.items():
        print(f"  {name:22s} {rows:>9,} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
