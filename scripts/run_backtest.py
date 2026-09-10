"""通用回测运行器：指向任意策略 `.py` 就能跑。

在此之前每个策略都要有自己的 runner，接线在多处各写一遍。策略是一个
可以拿走的文件，运行器只有一个——调度由策略在 `on_init` 里自己声明。

跑：
  uv run python scripts/run_backtest.py --strategy strategies/ma_cross.py \\
      --start 2025-09-10 --end 2026-09-08 --cash 100000

  uv run python scripts/run_backtest.py \\
      --strategy strategies/structural_v1_2.py \\
      --start 2025-09-10 --end 2026-09-08 --cash 100000 --jq-compat

落盘见 `qsys.backtest.artifacts`：一次运行 = 一个自包含目录 + zip，
文件名与列名对齐聚宽的研究导出。
"""

from __future__ import annotations

import argparse
import sys
import time as _time
from datetime import date, datetime
from pathlib import Path

import polars as pl

from qsys.backtest.artifacts import write_run
from qsys.backtest.corporate import CorporateAction
from qsys.backtest.engine import BacktestEngine
from qsys.backtest.fees import AShareFeeModel
from qsys.backtest.metrics import compute_metrics
from qsys.backtest.slippage import FixedRateSlippage
from qsys.core.types import Symbol
from qsys.markets.ashare import ashare_pipeline
from qsys.strategy.deps import build_deps, intraday_quotes
from qsys.strategy.loader import load_strategy


def cost_model(jq_compat: bool):
    """成本口径。对账时切成聚宽默认值，日常用我们自己的实盘档位。

    实测依据见 `docs/reference/joinquant-alignment.md`：聚宽 86 笔 lot 的
    `成交价 / 开盘价` 中位数 0.001241，正是 `PriceRelatedSlippage(0.00246)`
    的单边值。
    """
    if jq_compat:
        return (
            AShareFeeModel(commission_rate=0.0003, min_commission=5.0,
                           stamp_duty_rate=0.001, transfer_fee_rate=0.0),
            FixedRateSlippage(0.00123),
            "聚宽默认（滑点 0.123% / 印花税 0.1% / 无过户费）",
        )
    return (
        AShareFeeModel(),
        FixedRateSlippage(0.0005),
        "qsys 实盘档位（滑点 0.05% / 印花税 0.05% / 含过户费）",
    )


def corporate_actions(query, start: date, end: date):
    """持仓期间遇到除权，价格会掉，不处理就被当成亏损。"""
    actions: dict[date, list[CorporateAction]] = {}
    frame = query.corporate_actions()
    if frame.height == 0:
        return actions
    rows = frame.filter(
        (pl.col("ex_date") >= start) & (pl.col("ex_date") <= end)
        & ((pl.col("cash_per_share") > 0) | (pl.col("stock_per_share") > 0))
    )
    for row in rows.iter_rows(named=True):
        actions.setdefault(row["ex_date"], []).append(
            CorporateAction(
                symbol=Symbol.parse(row["symbol"]),
                ex_date=row["ex_date"],
                cash_per_share=row["cash_per_share"] or 0.0,
                stock_per_share=row["stock_per_share"] or 0.0,
            )
        )
    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description="通用回测运行器")
    parser.add_argument("--strategy", required=True, help="策略 .py 的路径")
    parser.add_argument("--strategy-name", default=None,
                        help="文件里有多个策略类时指定")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cash", type=float, default=100_000.0)
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--jq-compat", action="store_true",
                        help="成本口径切成聚宽默认值，用于逐笔对账")
    parser.add_argument("--param", action="append", default=[], metavar="K=V",
                        help="传给策略的参数，可重复")
    parser.add_argument("--runs-dir", default="var/runs")
    parser.add_argument("--run-name", default=None)
    args = parser.parse_args()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    params: dict[str, str] = {}
    for item in args.param:
        key, _, value = item.partition("=")
        params[key.strip()] = value.strip()

    deps, query, calendar, sessions, universe, boards = build_deps(
        Path(args.warehouse), params
    )
    strategy = load_strategy(
        Path(args.strategy), deps, name=args.strategy_name
    )
    fees, slippage, cost_label = cost_model(args.jq_compat)
    actions = corporate_actions(query, start, end)

    print(f"策略 {type(strategy).__name__}  ({args.strategy})")
    print(f"区间 {start} ~ {end}   标的池 {len(universe)} 只   成本口径 {cost_label}")
    started = _time.time()

    engine = BacktestEngine(
        strategy,
        feed=deps.feed, calendar=calendar, universe=universe,
        initial_cash=args.cash, pipeline=ashare_pipeline(),
        fees=fees, slippage=slippage,
        intraday=intraday_quotes(deps.market), market=deps.market,
        corporate_actions=actions,
        boards={Symbol.parse(k): v for k, v in boards.items()},
        params={"strategy": type(strategy).__name__, **params},
    )
    result = engine.run(start, end)

    metrics: dict[str, float] = {}
    if result.equity.height:
        m = compute_metrics(result.equity, result.fills)
        metrics = {
            "total_return": m.total_return,
            "annualized_return": m.annualized_return,
            "volatility": m.volatility,
            "sharpe": m.sharpe,
            "max_drawdown": m.max_drawdown,
            "max_drawdown_days": float(m.max_drawdown_days),
            "n_trades": float(m.n_trades),
            "win_rate": m.win_rate,
        }

    run_name = args.run_name or f"{datetime.now():%Y%m%d-%H%M%S}-{result.run_id[:8]}"
    selection = getattr(strategy, "_selection", None)
    run_dir = write_run(
        result, Path(args.runs_dir) / run_name,
        strategy=type(strategy).__name__,
        start=start, end=end, initial_cash=args.cash,
        config={
            "strategy_file": str(Path(args.strategy).resolve()),
            "cost_model": cost_label,
            "jq_compat": args.jq_compat,
            "universe_size": len(universe),
            "corporate_action_days": len(actions),
            **params,
        },
        metrics=metrics,
        decisions=getattr(selection, "decisions", ()),
        regime=query.market_regime(),
        sessions=sessions,
    )

    buys = [f for f in result.fills if f.side.value == "buy"]
    print(f"\nrun_id {result.run_id}   耗时 {(_time.time() - started) / 60:.1f} 分钟")
    print(f"成交 {len(result.fills)} 笔（买入 {len(buys)}）  拒单 {len(result.rejects)} 笔")
    print(f"初始 {args.cash:,.2f}   最终 {result.final_value:,.2f}")
    for label, key, scale in (("总收益", "total_return", 100),
                              ("年化", "annualized_return", 100),
                              ("夏普", "sharpe", 1),
                              ("最大回撤", "max_drawdown", 100),
                              ("胜率", "win_rate", 100)):
        if key in metrics:
            suffix = "%" if scale == 100 else ""
            print(f"  {label:<8}{metrics[key] * scale:>10.2f}{suffix}")
    print(f"\n运行目录 {run_dir}   （zip: {run_dir.name}.zip）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
