"""跑 Structural Strategy 的完整回测，产出净值。

**精确版只覆盖有分钟线的区间**：三个退出检查点都要日内价格。
早于分钟线起点的日子检查点会拿不到报价而跳过，退出逻辑不生效——
那不是回测结果，是噪声。所以默认区间就是分钟线区间。

用法：
  uv run python scripts/run_structural_backtest.py --start 2025-09-10 --end 2026-09-08
"""

from __future__ import annotations

import argparse
import sys
import time as _time
from datetime import date, datetime, time
from pathlib import Path

import polars as pl

from qsys.backtest.engine import BacktestEngine
from qsys.backtest.fees import AShareFeeModel
from qsys.backtest.metrics import compute_metrics
from qsys.backtest.slippage import FixedRateSlippage
from qsys.core.calendar import AShareCalendar
from qsys.core.types import Symbol
from qsys.data.feed import HistoricalFeed
from qsys.data.market_feed import MarketFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.markets.ashare import ashare_pipeline
from qsys.strategy.structural.selection import StructuralSelection
from qsys.strategy.structural.strategy import CHECKPOINTS, StructuralStrategy


def main() -> int:
    parser = argparse.ArgumentParser(description="Structural Strategy 回测")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--cash", type=float, default=100_000.0)
    parser.add_argument("--out", default="var/backtest_equity.csv")
    parser.add_argument(
        "--no-require-auction-volume", dest="require_auction_volume",
        action="store_false",
    )
    args = parser.parse_args()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    warehouse = Warehouse(Path(args.warehouse))
    query = WarehouseQuery(warehouse)

    sessions = sorted(query.market_regime()["dt"].to_list())
    calendar = AShareCalendar(sessions)
    feed = HistoricalFeed(query, calendar)
    market = MarketFeed(query, calendar)

    instruments = query.instruments()
    universe = [
        Symbol.parse(s) for s in instruments["symbol"].to_list()
        if not s.split(".")[0].startswith(("4", "8", "68"))
    ]
    boards = dict(
        zip(instruments["symbol"].to_list(), instruments["board"].to_list())
    )

    def intraday(session: date, at: time, symbols):
        quotes = market.bind(datetime.combine(session, at)).intraday(
            [str(s) for s in symbols], session, at
        )
        return {
            s: (quotes[str(s)].price, quotes[str(s)].volume)
            for s in symbols if str(s) in quotes
        }

    strategy = StructuralStrategy(
        StructuralSelection(
            feed, market, query,
            require_auction_volume=args.require_auction_volume,
        )
    )

    print(f"区间 {start} ~ {end}，标的池 {len(universe)} 只")
    print(f"检查点 {[t.strftime('%H:%M') for t in CHECKPOINTS]}")
    started = _time.time()

    engine = BacktestEngine(
        strategy,
        feed=feed, calendar=calendar, universe=universe,
        initial_cash=args.cash, pipeline=ashare_pipeline(),
        fees=AShareFeeModel(), slippage=FixedRateSlippage(0.0005),
        checkpoints=CHECKPOINTS, intraday=intraday, market=market,
        boards={Symbol.parse(k): v for k, v in boards.items()},
        params={"strategy": "structural_v1_2"},
    )
    result = engine.run(start, end)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    result.equity.write_csv(out)

    print(f"\nrun_id {result.run_id}   耗时 {(_time.time()-started)/60:.1f} 分钟")
    print(f"成交 {len(result.fills)} 笔   拒单 {len(result.rejects)} 笔")
    buys = [f for f in result.fills if f.side.value == "buy"]
    sells = [f for f in result.fills if f.side.value == "sell"]
    print(f"  买入 {len(buys)}   卖出 {len(sells)}")
    print(f"\n初始 {args.cash:,.2f}   最终 {result.final_value:,.2f}")
    if result.equity.height:
        m = compute_metrics(result.equity, result.fills)
        print(f"  总收益         {m.total_return * 100:>10.2f}%")
        print(f"  年化           {m.annualized_return * 100:>10.2f}%")
        print(f"  波动率         {m.volatility * 100:>10.2f}%")
        print(f"  夏普           {m.sharpe:>10.3f}")
        print(f"  最大回撤       {m.max_drawdown * 100:>10.2f}%（{m.max_drawdown_days} 天）")
        print(f"  交易笔数       {m.n_trades:>10}")
        print(f"  胜率           {m.win_rate * 100:>10.2f}%")
    print(f"\n净值已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
