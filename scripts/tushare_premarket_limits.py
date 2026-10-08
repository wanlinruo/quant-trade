"""盘前取当日 Tushare 涨跌停价与停复牌事实。

Tushare 文档：stk_limit「每个交易日 9 点左右更新当日股票涨跌停价格」。
由 qsys-premarket-limits.timer 在交易日 08:58、09:15、09:24 触发；
每次运行都观测 suspend_d，stk_limit 不完整时在单次运行内每分钟重试到 09:25；
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
from qsys.data.pipeline.premarket_suspensions import sync_premarket_suspensions
from qsys.data.pipeline.canonical_publish import publish_premarket
from qsys.data.providers.tushare import TushareProvider
from qsys.data.providers.tushare_client import TushareClient
from qsys.data.storage import Warehouse
from qsys.live.config import load_deploy_config, load_tushare_config
from qsys.live.redis_transport import RedisTransport
from qsys.data.query import WarehouseQuery
from qsys.ops.services import ServiceRun

DEADLINE = time(9, 25)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config", type=Path, default=Path("config/deploy.local.toml"))
    parser.add_argument("--day", type=date.fromisoformat, default=None)
    parser.add_argument("--service-runs", type=Path, default=Path("var/service-runs"),
                        help="运行记录目录（服务管理页读取）")
    args = parser.parse_args()

    with ServiceRun.open(args.service_runs, "premarket-limits") as run:
        now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        day = args.day or now.date()
        client = TushareClient(load_tushare_config(args.config).token)
        stamp = day.strftime("%Y%m%d")
        with run.step("判断交易日", interface="Tushare trade_cal") as step:
            is_open = bool(client.call("trade_cal", {"exchange": "SSE", "start_date": stamp,
                                                     "end_date": stamp, "is_open": "1"}))
            step.detail = f"{day} {'是' if is_open else '不是'}交易日"
        if not is_open:
            run.skip(f"{day} 不是交易日")
            print(f"{day} 不是交易日，退出")
            return 0
        run.session = day

        deploy = load_deploy_config(args.config)
        warehouse = Warehouse(args.warehouse)
        # timer 会在 08:58、09:15、09:24 重复触发。每次都先留下 suspend_d
        # 观测，再处理稠密的 stk_limit；这样 stk_limit 晚到重试不会吞掉停牌探针时点。
        suspension_code = 0
        with run.step("当日停复牌", interface="Tushare suspend_d",
                      table="suspend_daily") as step:
            try:
                snapshot = sync_premarket_suspensions(
                    TushareProvider(client), warehouse, day, observed_at=now,
                )
                step.rows = snapshot.row_count
                step.detail = snapshot.detail
                if not snapshot.accepted:
                    step.warn("接口响应少于已有快照，已保留已有数据；" + snapshot.detail)
            except Exception as exc:
                suspension_code = 1
                step.fail(f"查询失败，未把失败伪装成空集合：{type(exc).__name__}: {exc}")
        with run.step("当日涨跌停价", interface="Tushare stk_limit → Redis",
                      table="price_limits_daily") as step:
            code = run_premarket_limits(
                TushareProvider(client), warehouse,
                RedisTransport.from_config(deploy), deploy.streams, day,
                deadline=datetime.combine(day, DEADLINE),
            )
            step.rows = WarehouseQuery(warehouse).price_limits(None, day, day).height
            if code != 0:
                step.fail(f"{DEADLINE:%H:%M} 仍未取到完整数据；盘中缺价标的买单拒绝、卖单放行")
        if code == 0:
            with run.step(
                "canonical 涨跌停价", interface="price_limits_daily → price_limit_daily",
                table="price_limit_daily",
            ) as step:
                published = publish_premarket(warehouse, day)
                step.rows = published.counts["price_limit_daily"]
                step.detail = "写后逐键逐字段对账通过"
        print(
            f"{day} 盘前事实：涨跌停价"
            f"{'完成' if code == 0 else '到点仍未取到完整数据'}，"
            f"停复牌{'完成' if suspension_code == 0 else '失败'}"
        )
        return code or suspension_code


if __name__ == "__main__":
    sys.exit(main())
