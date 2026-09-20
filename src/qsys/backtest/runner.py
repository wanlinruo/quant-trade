"""CLI 与工作台共用的标准 A 股回测装配。"""

from __future__ import annotations

import shutil
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Callable, Mapping

import polars as pl

from qsys.backtest.artifacts import write_run
from qsys.backtest.corporate import CorporateAction
from qsys.backtest.engine import BacktestEngine
from qsys.backtest.fees import AShareFeeModel
from qsys.backtest.metrics import compute_metrics
from qsys.backtest.slippage import FixedRateSlippage
from qsys.core.types import Symbol
from qsys.data.cutover import CutoverConfig, CutoverMarketData
from qsys.markets.ashare import ashare_pipeline
from qsys.strategy.deps import build_deps, intraday_quotes
from qsys.strategy.loader import load_strategy
from qsys.strategy.parameters import parameter_specs_from_file, validate_parameters


def price_limit_loader(query) -> Callable[[date, date], dict[date, dict[Symbol, tuple[float, float]]]]:
    """回测撮合用的当日直取涨跌停价（price_limits_daily，Tushare stk_limit）。

    缺行的股-日不出现在结果里，撮合按「买单拒绝、卖单放行」处理（D-19）。
    """
    market_data = CutoverMarketData(query, CutoverConfig.from_env())

    def load(start: date, end: date) -> dict[date, dict[Symbol, tuple[float, float]]]:
        frame = market_data.price_limits(None, start, end).filter(
            pl.col("limit_up").is_not_null() & pl.col("limit_down").is_not_null()
        )
        out: dict[date, dict[Symbol, tuple[float, float]]] = {}
        for row in frame.iter_rows(named=True):
            out.setdefault(row["dt"], {})[Symbol.parse(row["symbol"])] = (
                float(row["limit_up"]), float(row["limit_down"])
            )
        return out

    return load


def execute_backtest(
    strategy_file: Path, *, start: date, end: date, cash: float,
    warehouse: Path, out_dir: Path, params: Mapping[str, object] | None = None,
    jq_compat: bool = False, progress: Callable[[int, int, date], None] | None = None,
    strategy_source: str | None = None,
) -> Path:
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    if cash <= 0:
        raise ValueError("初始资金必须大于 0")
    typed = validate_parameters(parameter_specs_from_file(strategy_file), params or {})
    deps, query, calendar, sessions, universe, boards = build_deps(warehouse, typed)
    requested = calendar.sessions_in_range(start, end)
    if not requested:
        raise ValueError("所选区间没有可用交易日数据")
    strategy = load_strategy(strategy_file, deps)

    def validate_schedule(checkpoints, auction_at):
        if not checkpoints:
            return
        bounds = query.intraday_bounds()
        if bounds is None:
            raise ValueError("策略声明了盘中检查点，但仓库没有分钟行情")
        first, last = bounds
        first_day = first.date() if hasattr(first, "date") else first
        last_day = last.date() if hasattr(last, "date") else last
        if start < first_day or end > last_day:
            raise ValueError(
                f"分钟行情仅覆盖 {first_day} ~ {last_day}，不能精确回测所选区间"
            )

    fees, slippage, cost_label = cost_model(jq_compat)
    result = BacktestEngine(
        strategy, feed=deps.feed, calendar=calendar, universe=universe,
        initial_cash=cash, pipeline=ashare_pipeline(), fees=fees, slippage=slippage,
        price_limits=price_limit_loader(query),
        intraday=intraday_quotes(deps.market), market=deps.market,
        corporate_actions=corporate_actions(query, start, end),
        boards={Symbol.parse(k): v for k, v in boards.items()},
        params={"strategy": type(strategy).__name__, **typed}, progress=progress,
        schedule_validator=validate_schedule,
    ).run(start, end)

    benchmark = benchmark_nav(result.equity, query.index_bars(["000300.SH"], start, end), cash)
    benchmark_values = benchmark["total_value"].to_list() if benchmark.height == result.equity.height else ()
    metrics = asdict(compute_metrics(result.equity, result.fills,
                                     benchmark_values=benchmark_values))
    average_equity = float(result.equity["total_value"].mean() or 0.0)
    metrics["turnover_rate"] = metrics["turnover"] / (2 * average_equity) if average_equity else 0.0
    if not benchmark_values:
        for key in ("benchmark_return", "excess_return", "alpha", "beta",
                    "tracking_error", "information_ratio"):
            metrics.pop(key, None)
    selection = getattr(strategy, "_selection", None)
    staging = out_dir.with_name(f".{out_dir.name}.tmp")
    if staging.exists():
        shutil.rmtree(staging)
    write_run(
        result, staging, strategy=type(strategy).__name__, start=start, end=end,
        initial_cash=cash,
        config={"strategy_file": str(strategy_file), "strategy_version": _sha(strategy_file),
                "cost_model": cost_label, "jq_compat": jq_compat,
                "benchmark": "000300.SH", "universe_size": len(universe), **typed},
        metrics=metrics, decisions=getattr(selection, "decisions", ()),
        regime=CutoverMarketData(query, CutoverConfig.from_env()).market_regime(),
        sessions=sessions, benchmark=benchmark,
        strategy_source=strategy_source or strategy_file.read_text(encoding="utf-8"),
    )
    archive = staging / f"{staging.name}.zip"
    if archive.exists():
        archive.rename(staging / f"{out_dir.name}.zip")
    if out_dir.exists():
        raise FileExistsError(f"运行目录已存在: {out_dir}")
    staging.rename(out_dir)
    return out_dir


def benchmark_nav(equity: pl.DataFrame, index: pl.DataFrame, cash: float) -> pl.DataFrame:
    if equity.height == 0 or index.height == 0:
        return pl.DataFrame(schema={"date": pl.Date, "close": pl.Float64,
                                    "total_value": pl.Float64, "daily_return": pl.Float64})
    joined = equity.select(pl.col("dt").alias("date")).join(
        index.select(pl.col("dt").alias("date"), "close"), on="date", how="inner"
    ).sort("date")
    if joined.height != equity.height or not joined.height:
        return pl.DataFrame(schema={"date": pl.Date, "close": pl.Float64,
                                    "total_value": pl.Float64, "daily_return": pl.Float64})
    first = float(joined["close"][0])
    return joined.with_columns(
        (pl.col("close") / first * cash).alias("total_value"),
        (pl.col("close") / pl.col("close").shift(1) - 1.0).fill_null(0.0).alias("daily_return"),
    )


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def cost_model(jq_compat: bool):
    if jq_compat:
        return (AShareFeeModel(commission_rate=0.0003, min_commission=5.0,
                               stamp_duty_rate=0.001, transfer_fee_rate=0.0),
                FixedRateSlippage(0.00123), "聚宽默认（滑点 0.123% / 印花税 0.1% / 无过户费）")
    return (AShareFeeModel(), FixedRateSlippage(0.0005),
            "qsys 实盘档位（滑点 0.05% / 印花税 0.05% / 含过户费）")


def corporate_actions(query, start: date, end: date):
    actions: dict[date, list[CorporateAction]] = {}
    frame = query.corporate_actions()
    if frame.height == 0:
        return actions
    rows = frame.filter(
        (pl.col("ex_date") >= start) & (pl.col("ex_date") <= end)
        & ((pl.col("cash_per_share") > 0) | (pl.col("stock_per_share") > 0))
    )
    for row in rows.iter_rows(named=True):
        actions.setdefault(row["ex_date"], []).append(CorporateAction(
            symbol=Symbol.parse(row["symbol"]), ex_date=row["ex_date"],
            cash_per_share=row["cash_per_share"] or 0.0,
            stock_per_share=row["stock_per_share"] or 0.0,
        ))
    return actions
