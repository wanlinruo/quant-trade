"""跑 Structural Strategy 的完整回测，产出净值。

**精确版只覆盖有分钟线的区间**：三个退出检查点都要日内价格。
早于分钟线起点的日子检查点会拿不到报价而跳过，退出逻辑不生效——
那不是回测结果，是噪声。所以默认区间就是分钟线区间。

用法：
  uv run python scripts/run_structural_backtest.py --start 2025-09-10 --end 2026-09-08

竞价入场口径（默认与原来一致：开盘价 + 通用滑点，保护价上浮 0.2%）：
  --entry-fill continuous --entry-price-buffer 0.005   # 09:25 后下单、09:31 分钟线判定
  --entry-fill slippage --entry-slippage 0.005         # 只加价、不改成交率的参照
"""

from __future__ import annotations

import argparse
import json
import sys
import time as _time
from datetime import date, datetime, time
from pathlib import Path

import polars as pl

from qsys.backtest.artifacts import write_run
from qsys.backtest.corporate import CorporateAction
from qsys.backtest.engine import BacktestEngine
from qsys.backtest.entry_fill import EntryFill, EntryFillMode
from qsys.backtest.fees import (
    ACCOUNT_COMMISSION_RATE,
    ACCOUNT_MIN_COMMISSION,
    AShareFeeModel,
)
from qsys.backtest.metrics import compute_metrics
from qsys.backtest.runner import entry_minute_loader, price_limit_loader
from qsys.backtest.slippage import FixedRateSlippage
from qsys.core.calendar import AShareCalendar
from qsys.core.types import Symbol
from qsys.data.cutover import (
    CutoverConfig, CutoverMarketData, add_facade_argument, apply_facade_argument,
)
from qsys.data.feed import HistoricalFeed
from qsys.data.market_feed import MarketFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.markets.ashare import ashare_pipeline
from qsys.strategy.deps import build_universe
from qsys.strategy.structural.selection import StructuralSelection
from qsys.strategy.structural.strategy import (
    AUCTION_AT,
    CHECKPOINTS,
    StructuralStrategy,
)

RESEARCH_THRESHOLD_KEYS = {
    "low_trend_weak", "low_heat_low", "high_main_breadth_high",
    "high_chinext_advance_not_low",
}


def load_research_thresholds(path: Path | None) -> dict[str, float]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    raw = value.get("candidate_thresholds") if isinstance(value, dict) else None
    if not isinstance(raw, dict) or set(raw) != RESEARCH_THRESHOLD_KEYS:
        raise ValueError(
            "research threshold file must contain exactly candidate_thresholds: "
            f"{sorted(RESEARCH_THRESHOLD_KEYS)}"
        )
    return {key: float(raw[key]) for key in sorted(raw)}


def _cost_model(jq_compat: bool):
    """成本口径：默认用我们自己的实盘档位；对账时切成聚宽的默认值。

    原策略**没有调用** `set_slippage` / `set_order_cost`，所以聚宽用的是
    它的默认值。别凭记忆——从它导出的 86 笔 lot 反推：`entry_price / 当日
    open` 的中位数是 **0.001241**，正是 `PriceRelatedSlippage(0.00246)` 的
    单边值 0.00123。86/86 笔全部配上，说明两边的 open 本身完全一致。

    聚宽默认 `OrderCost`：佣金双边万三、最低 5 元，印花税**千一**（旧税率，
    仅卖出），**不收过户费**。我们的默认档位是现行税率（印花税万五）且计过户费
    ——更贴近实盘，但对账时必须换成它的。
    """
    if jq_compat:
        return (
            AShareFeeModel(
                commission_rate=0.0003,
                min_commission=5.0,
                stamp_duty_rate=0.001,     # 聚宽默认仍是旧税率
                transfer_fee_rate=0.0,     # 聚宽默认不收过户费
            ),
            FixedRateSlippage(0.00123),    # PriceRelatedSlippage(0.00246) 的单边
            "聚宽默认（滑点 0.123% / 印花税 0.1% / 无过户费）",
        )
    return (
        AShareFeeModel(
            commission_rate=ACCOUNT_COMMISSION_RATE,
            min_commission=ACCOUNT_MIN_COMMISSION,
        ),
        FixedRateSlippage(0.0005),
        "qsys 实盘档位（佣金万0.75最低5元 / 滑点 0.05% / 印花税 0.05% / 含过户费）",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Structural Strategy 回测")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--cash", type=float, default=100_000.0)
    parser.add_argument("--out", default="var/backtest_equity.csv")
    parser.add_argument(
        "--runs-dir", default="var/runs",
        help="每次运行落一个自包含目录 + zip，文件名对齐聚宽导出",
    )
    parser.add_argument("--run-name", default=None, help="运行目录名，默认按时间戳")
    parser.add_argument(
        "--no-require-auction-volume", dest="require_auction_volume",
        action="store_false",
    )
    parser.add_argument(
        "--no-gate", dest="gate", action="store_false",
        help="关闭 S7 生态闸门（只用于研究对照；S6 判定照常记录）",
    )
    parser.add_argument(
        "--research-gate-thresholds", type=Path, default=None,
        help="仅研究：读取 recalibration.json 的四个候选阈值，不写回生产参数",
    )
    parser.add_argument(
        "--disable-gate-tag", action="append", default=[],
        choices=("LOW_MAIN", "HIGH_MAIN", "HIGH_CHINEXT"),
        help="仅研究消融：放行指定分支，仍保留原分类留痕；可重复",
    )
    parser.add_argument(
        "--jq-compat", action="store_true",
        help="成本口径切成聚宽默认值，用于逐笔对账（见下方 _cost_model）",
    )
    parser.add_argument(
        "--entry-fill", choices=[m.value for m in EntryFillMode], default=None,
        help="竞价买单撮合口径；不给则与原来完全一致（开盘价 + 通用滑点）",
    )
    parser.add_argument(
        "--entry-slippage", type=float, default=0.005,
        help="slippage 口径的上浮比例，也是 continuous 缺 09:31 分钟线时的退回口径",
    )
    parser.add_argument(
        "--entry-price-buffer", type=float, default=None,
        help="限价保护价相对开盘价的上浮比例；不给则用策略默认 0.2%%",
    )
    parser.add_argument(
        "--sizing-price-buffer", type=float, default=None,
        help="定量用的开盘价上浮；不给则与保护价上浮相同（原策略）",
    )
    parser.add_argument(
        "--auction-at", default=None, choices=["09:30", "09:25:10"],
        help="竞价决策时刻；不给则 09:30（原策略）",
    )
    add_facade_argument(parser)
    args = parser.parse_args()
    apply_facade_argument(args.market_data_facade)
    research_thresholds = load_research_thresholds(args.research_gate_thresholds)

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    warehouse = Warehouse(Path(args.warehouse))
    query = WarehouseQuery(warehouse)
    market_data = CutoverMarketData(query, CutoverConfig.from_env())

    sessions = sorted(market_data.market_regime()["dt"].to_list())
    calendar = AShareCalendar(sessions)
    feed = HistoricalFeed(query, calendar, market_data)
    market = MarketFeed(query, calendar, market_data)

    universe, boards = build_universe(market_data.instruments())

    # 除权除息：持仓期间遇到除权，价格会掉，不处理就被当成亏损。
    # 全市场 6 万条，一期 bootstrap 时只有 3 只，回测入口此前根本没传。
    actions: dict[date, list[CorporateAction]] = {}
    ca = query.corporate_actions()
    for row in ca.filter(
        (pl.col("ex_date") >= start) & (pl.col("ex_date") <= end)
        & ((pl.col("cash_per_share") > 0) | (pl.col("stock_per_share") > 0))
    ).iter_rows(named=True):
        actions.setdefault(row["ex_date"], []).append(
            CorporateAction(
                symbol=Symbol.parse(row["symbol"]),
                ex_date=row["ex_date"],
                cash_per_share=row["cash_per_share"],
                stock_per_share=row["stock_per_share"],
            )
        )
    print(f"除权除息事件 {sum(len(v) for v in actions.values()):,} 条 / {len(actions)} 天")

    def intraday(session: date, at: time, symbols):
        quotes = market.bind(datetime.combine(session, at)).intraday(
            [str(s) for s in symbols], session, at
        )
        return {
            s: (quotes[str(s)].price, quotes[str(s)].volume)
            for s in symbols if str(s) in quotes
        }

    # 留一个引用：跑完要把它累计的全部 Gate 判定（含被拒的）一起落盘
    selection = StructuralSelection(
        feed, market, query,
        require_auction_volume=args.require_auction_volume,
        gate_enabled=args.gate,
        gate_thresholds=research_thresholds,
        disabled_gate_tags=args.disable_gate_tag,
    )
    strategy = StructuralStrategy(selection, calendar=calendar)

    fees, slippage, cost_label = _cost_model(args.jq_compat)
    entry_fill = (
        EntryFill(EntryFillMode(args.entry_fill), slippage=args.entry_slippage)
        if args.entry_fill is not None else None
    )
    params: dict[str, object] = {"strategy": "structural_v1_2"}
    if args.entry_price_buffer is not None:
        params["entry_price_buffer"] = args.entry_price_buffer
    if args.sizing_price_buffer is not None:
        params["sizing_price_buffer"] = args.sizing_price_buffer
    if args.auction_at is not None:
        params["auction_at"] = args.auction_at
    print(f"区间 {start} ~ {end}，标的池 {len(universe)} 只   成本口径 {cost_label}")
    if entry_fill is not None or args.entry_price_buffer is not None:
        print(f"竞价入场 {entry_fill.label() if entry_fill else '开盘价成交'}   "
              f"保护价上浮 {params.get('entry_price_buffer', 0.002):.2%}")
    auction_label = args.auction_at or AUCTION_AT.strftime("%H:%M")
    print(f"竞价时刻 {auction_label}  检查点 {[t.strftime('%H:%M') for t in CHECKPOINTS]}")
    started = _time.time()

    engine = BacktestEngine(
        strategy,
        feed=feed, calendar=calendar, universe=universe,
        initial_cash=args.cash, pipeline=ashare_pipeline(), price_limits=price_limit_loader(query),
        fees=fees, slippage=slippage,
        checkpoints=CHECKPOINTS, auction_at=AUCTION_AT,
        intraday=intraday, market=market,
        corporate_actions=actions,
        boards={Symbol.parse(k): v for k, v in boards.items()},
        params=params,
        entry_fill=entry_fill,
        entry_minutes=entry_minute_loader(market_data) if entry_fill is not None else None,
    )
    result = engine.run(start, end)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    result.equity.write_csv(out)

    # 成交明细：对账要逐笔比入场价、出场价、退出时刻
    fills_path = out.with_name(out.stem + "_fills.csv")
    pl.DataFrame({
        "filled_at": [f.filled_at for f in result.fills],
        "symbol": [str(f.symbol) for f in result.fills],
        "side": [f.side.value for f in result.fills],
        "qty": [f.qty for f in result.fills],
        "price": [f.price for f in result.fills],
        "fee": [f.fee for f in result.fills],
    }).write_csv(fills_path)
    print(f"成交明细已写入 {fills_path}")

    # 一次运行 = 一个自包含目录。文件名与列名对齐聚宽的研究导出，
    # 两边可以直接 diff——对账是双向的，我们这边也得拿得出同样的东西。
    run_name = args.run_name or (
        f"{datetime.now():%Y%m%d-%H%M%S}-{result.run_id[:8]}"
    )
    metrics_map: dict[str, float] = {}
    if result.equity.height:
        m = compute_metrics(result.equity, result.fills)
        metrics_map = {
            "total_return": m.total_return,
            "annualized_return": m.annualized_return,
            "volatility": m.volatility,
            "sharpe": m.sharpe,
            "max_drawdown": m.max_drawdown,
            "max_drawdown_days": float(m.max_drawdown_days),
            "n_trades": float(m.n_trades),
            "win_rate": m.win_rate,
        }
    run_dir = write_run(
        result, Path(args.runs_dir) / run_name,
        strategy="StructuralStrategy",
        start=start, end=end, initial_cash=args.cash,
        config={
            "cost_model": cost_label,
            "source": "cli",
            "jq_compat": args.jq_compat,
            "require_auction_volume": args.require_auction_volume,
            "gate_enabled": args.gate,
            "research_gate_thresholds": json.dumps(
                research_thresholds, sort_keys=True
            ) if research_thresholds else "",
            "disabled_gate_tags": ",".join(sorted(args.disable_gate_tag)),
            "auction_at": auction_label,
            "checkpoints": ",".join(t.strftime("%H:%M") for t in CHECKPOINTS),
            "universe_size": len(universe),
            "corporate_action_days": len(actions),
            **({"entry_fill": entry_fill.label(), "entry_fill_mode": entry_fill.mode.value,
                "entry_slippage": entry_fill.slippage,
                "entry_fill_stats": ",".join(f"{k}={v}" for k, v in sorted(result.diagnostics.items()))}
               if entry_fill is not None else {}),
            **({"entry_price_buffer": args.entry_price_buffer}
               if args.entry_price_buffer is not None else {}),
            **({"sizing_price_buffer": args.sizing_price_buffer}
               if args.sizing_price_buffer is not None else {}),
        },
        metrics=metrics_map,
        decisions=getattr(selection, "history", None) or getattr(selection, "decisions", ()),
        regime=market_data.market_regime(),
        sessions=sessions,
    )
    print(f"运行目录 {run_dir}   （zip: {run_dir.name}.zip）")

    print(f"\nrun_id {result.run_id}   耗时 {(_time.time()-started)/60:.1f} 分钟")
    print(f"成交 {len(result.fills)} 笔   拒单 {len(result.rejects)} 笔")
    buys = [f for f in result.fills if f.side.value == "buy"]
    sells = [f for f in result.fills if f.side.value == "sell"]
    print(f"  买入 {len(buys)}   卖出 {len(sells)}")
    if result.diagnostics:
        print(f"  竞价入场 {dict(result.diagnostics)}")
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
