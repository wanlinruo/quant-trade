"""盘前取当日 Tushare stk_limit，写入 price_limits_daily 并发布到 Redis（D-19）。

Tushare 文档：stk_limit「每个交易日 9 点左右更新当日股票涨跌停价格」。
由 qsys-premarket-limits.timer 在交易日 08:58 触发，每分钟重试到 09:25；
非交易日直接退出。到点仍取不到完整数据则以非零退出，盘中进程对缺价标的
按「买单拒绝、卖单放行」处理，不自算、不拿 QMT 顶替。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from qsys.data.pipeline.premarket import run_premarket_limits
from qsys.data.providers.tushare import TushareProvider
from qsys.data.providers.tushare_client import TushareClient
from qsys.data.storage import Warehouse
from qsys.live.config import load_deploy_config, load_tushare_config
from qsys.live.redis_transport import RedisTransport

DEADLINE = time(9, 25)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config", type=Path, default=Path("config/deploy.local.toml"))
    parser.add_argument("--day", type=date.fromisoformat, default=None)
    args = parser.parse_args()

    now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    day = args.day or now.date()
    client = TushareClient(load_tushare_config(args.config).token)
    stamp = day.strftime("%Y%m%d")
    if not client.call("trade_cal", {"exchange": "SSE", "start_date": stamp,
                                     "end_date": stamp, "is_open": "1"}):
        print(f"{day} 不是交易日，退出")
        return 0

    deploy = load_deploy_config(args.config)
    code = run_premarket_limits(
        TushareProvider(client), Warehouse(args.warehouse),
        RedisTransport.from_config(deploy), deploy.streams, day,
        deadline=datetime.combine(day, DEADLINE),
    )
    print(f"{day} 盘前涨跌停价：{'完成' if code == 0 else '到点仍未取到完整数据'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
