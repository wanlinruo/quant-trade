"""回填 Tushare 直取真值并生成机器可读执行日志。

默认只做 2023 年以来的策略生产区间；请求按账户 500 次/分钟主动限流。
同一日期采用完整替换语义，上游撤销的停牌/榜单记录不会残留。
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from qsys.data.pipeline import tushare_reference as reference
from qsys.data.pipeline.regime import build_market_regime
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.pipeline.valuation import build_valuation
from qsys.data.providers.tushare import TushareProvider
from qsys.data.providers.tushare_client import TushareClient
from qsys.data.storage import Warehouse
from qsys.live.config import load_tushare_config


class RateLimitedClient:
    def __init__(self, client: TushareClient, requests_per_minute: int) -> None:
        self.client = client
        self.interval = 60.0 / requests_per_minute
        self.last_call = 0.0

    def call(self, *args, **kwargs):
        wait = self.interval - (time.monotonic() - self.last_call)
        if wait > 0:
            time.sleep(wait)
        result = self.client.call(*args, **kwargs)
        self.last_call = time.monotonic()
        return result


def trading_days(client, start: date, end: date) -> list[date]:
    rows = client.call("trade_cal", {
        "exchange": "SSE", "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"), "is_open": "1",
    })
    return sorted(date.fromisoformat(
        f"{r['cal_date'][:4]}-{r['cal_date'][4:6]}-{r['cal_date'][6:]}"
    ) for r in rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config", type=Path, default=Path("config/deploy.local.toml"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--batch-days", type=int, default=20)
    parser.add_argument("--requests-per-minute", type=int, default=500)
    parser.add_argument("--skip-derived", action="store_true")
    parser.add_argument("--log", type=Path, default=None)
    args = parser.parse_args()
    if args.start < date(2000, 1, 1):
        parser.error("stock_st 官方历史从 2000-01-01 开始，不能建立更早完整真值")

    started = datetime.now(ZoneInfo("Asia/Shanghai"))
    client = RateLimitedClient(
        TushareClient(load_tushare_config(args.config).token),
        args.requests_per_minute,
    )
    provider = TushareProvider(client)
    warehouse = Warehouse(args.warehouse)
    # daily_flags 为年初前收与首板连续性多读 150 个自然日；真值表必须覆盖
    # 同一窗口，否则 2023 年会在重建时退回旧名称链路。
    facts_start = args.start - timedelta(days=150)
    days = trading_days(client, facts_start, args.end)
    totals = {name: 0 for name in (
        "price_limits_daily", "suspend_daily", "stock_st_daily", "limit_list_daily"
    )}
    for offset in range(0, len(days), args.batch_days):
        batch = days[offset:offset + args.batch_days]
        totals["price_limits_daily"] += reference.ingest_price_limits(
            provider, warehouse, batch
        )
        totals["suspend_daily"] += reference.ingest_suspensions(
            provider, warehouse, batch
        )
        totals["stock_st_daily"] += reference.ingest_stock_st(
            provider, warehouse, batch
        )
        totals["limit_list_daily"] += reference.ingest_limit_list(
            provider, warehouse, batch
        )
        print(f"{batch[0]} ~ {batch[-1]} 完成", flush=True)

    if not args.skip_derived and days:
        totals["daily_flags"] = build_daily_flags(
            warehouse, args.start, args.end, boards=None
        )
        totals["market_regime"] = build_market_regime(
            warehouse, args.start, args.end, boards=None
        )
        totals["valuation_snapshot"] = build_valuation(
            warehouse, args.start, args.end
        )

    ended = datetime.now(ZoneInfo("Asia/Shanghai"))
    record = {
        "operation": "tushare_truth_backfill", "status": "success",
        "started_at": started.isoformat(), "ended_at": ended.isoformat(),
        "warehouse": str(args.warehouse.resolve()), "start": str(args.start),
        "end": str(args.end), "facts_start": str(facts_start),
        "trading_days": len(days), "rows": totals,
        "requests_per_minute": args.requests_per_minute,
    }
    target = args.log or Path("artifacts") / f"tushare_truth_backfill_{ended:%Y%m%d_%H%M%S}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(f"执行日志：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
