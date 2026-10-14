"""回测落盘：一次运行 = 一个自包含的目录 + 一个 zip。

**文件名与列名刻意对齐聚宽的导出**（`daily_nav.csv` / `daily_buy_details.csv`
/ `closed_lots.csv` / `gate_decisions.csv` / `run_status.csv`），两边可以
直接 diff，不需要中间转换脚本。

标的代码**两种格式都写**：`stock` 是聚宽的 `002139.XSHE`，`symbol` 是我们的
`002139.SZ`。少一列就得在对账时现场转换，而那正是最容易出错的地方。

我们比聚宽多写的：`fills.csv`（逐笔成交，含费用）、`rejects.csv`
（被拒的单与原因——聚宽没有对应物）、`metrics.csv`、`config.json`。

**为什么要 zip**：跨机传一个文件比传一个目录省事，也便于直接下载给人看。
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import polars as pl
import structlog

log = structlog.get_logger(__name__)

_EXCHANGE = {"SZ": "XSHE", "SH": "XSHG"}


def to_joinquant_code(symbol: str) -> str:
    """`002139.SZ` -> `002139.XSHE`。认不出来的原样返回。"""
    code, _, market = symbol.partition(".")
    suffix = _EXCHANGE.get(market.upper())
    return f"{code}.{suffix}" if suffix else symbol


@dataclass(frozen=True, slots=True)
class ClosedLot:
    """一次完整的买入->卖出配对。字段对齐聚宽 `closed_lots.csv`。"""

    lot_id: str
    symbol: str
    strategy_tag: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    qty: int
    return_pct: float
    realized_pnl_gross: float
    holding_trade_days: int
    entry_notional: float


def closed_lots(
    fills: Sequence, tags: Mapping[str, str], sessions: Sequence[date] = ()
) -> list[ClosedLot]:
    """按 FIFO 把成交流水配成 lot。

    我们的 `Portfolio` 按标的聚合，没有 lot 级账本（已知缺口），所以这里
    只能从流水反推。策略持仓 1~2 天，同一标的很少并存多笔，FIFO 足够。
    """
    position = {d: i for i, d in enumerate(sorted(sessions))}
    open_buys: dict[str, list[dict]] = {}
    out: list[ClosedLot] = []
    seq = 0

    for fill in sorted(fills, key=lambda f: f.filled_at):
        symbol = str(fill.symbol)
        if fill.side.value == "buy":
            open_buys.setdefault(symbol, []).append(
                {"at": fill.filled_at, "price": fill.price, "qty": fill.qty}
            )
            continue

        remaining = fill.qty
        while remaining > 0 and open_buys.get(symbol):
            entry = open_buys[symbol][0]
            take = min(remaining, entry["qty"])
            seq += 1
            held = _trade_days(position, entry["at"].date(), fill.filled_at.date())
            out.append(
                ClosedLot(
                    lot_id=f"{symbol}-{seq:06d}",
                    symbol=symbol,
                    strategy_tag=tags.get(symbol, "UNKNOWN"),
                    entry_time=entry["at"],
                    exit_time=fill.filled_at,
                    entry_price=entry["price"],
                    exit_price=fill.price,
                    qty=take,
                    return_pct=(fill.price / entry["price"] - 1.0) * 100.0,
                    realized_pnl_gross=(fill.price - entry["price"]) * take,
                    holding_trade_days=held,
                    entry_notional=entry["price"] * take,
                )
            )
            entry["qty"] -= take
            remaining -= take
            if entry["qty"] == 0:
                open_buys[symbol].pop(0)

    return out


def _trade_days(position: Mapping[date, int], entry: date, exit_: date) -> int:
    a, b = position.get(entry), position.get(exit_)
    if a is None or b is None:
        return max((exit_ - entry).days, 0)
    return max(b - a, 0)


# 我们的 market_regime 列名 -> 聚宽 regime_daily 的列名。
# 只重命名语义相同的；我们没有的列不造假。
_REGIME_RENAME = {
    "valid_stock_count": "market_valid_stock_count",
    "market_total_money": "market_total_money_raw",
}


def write_run(
    result,
    out_dir: Path,
    *,
    strategy: str,
    start: date,
    end: date,
    initial_cash: float,
    config: Mapping[str, object],
    metrics: Mapping[str, float] | None = None,
    decisions: Sequence = (),
    candidate_traces: Sequence[Mapping[str, object]] = (),
    regime: pl.DataFrame | None = None,
    sessions: Sequence[date] = (),
    benchmark: pl.DataFrame | None = None,
    strategy_source: str | None = None,
    log_text: str = "",
) -> Path:
    """把一次运行完整落盘。返回运行目录。"""
    run_dir = Path(out_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    tags = dict(result.state.get("tags", {}) or {})
    lots = closed_lots(result.fills, tags, sessions)
    files: dict[str, str] = {}

    files["daily_nav.csv"] = _nav(result).write_csv()
    files["fills.csv"] = _fills(result).write_csv()
    files["daily_buy_details.csv"] = _buy_details(result, tags).write_csv()
    files["closed_lots.csv"] = _lots_frame(lots).write_csv()
    files["rejects.csv"] = _rejects(result).write_csv()
    files["orders.csv"] = _orders(result).write_csv()
    files["daily_positions.csv"] = _positions(result).write_csv()
    files["gate_decisions.csv"] = _gate_decisions(decisions).write_csv()
    files["candidate_funnel.csv"] = _candidate_funnel(candidate_traces).write_csv()
    files["branch_summary.csv"] = _branch_summary(lots).write_csv()
    if regime is not None and regime.height:
        files["regime_daily.csv"] = _regime(regime, start, end).write_csv()
    files["metrics.csv"] = _metrics(metrics or {}).write_csv()
    files["monthly_returns.csv"] = _monthly_returns(result).write_csv()
    if benchmark is not None and benchmark.height:
        files["benchmark_nav.csv"] = benchmark.write_csv()
    files["run_status.csv"] = _status(
        result, strategy, start, end, initial_cash, lots, decisions
    ).write_csv()
    files["config.json"] = json.dumps(
        {
            "run_id": result.run_id,
            "strategy": strategy,
            "start": str(start),
            "end": str(end),
            "initial_cash": initial_cash,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            **{k: _jsonable(v) for k, v in config.items()},
        },
        ensure_ascii=False,
        indent=2,
    )
    files["README.txt"] = _readme(strategy, start, end, initial_cash, result.run_id)
    if strategy_source is not None:
        files["strategy.py"] = strategy_source
    files["run.log"] = log_text

    for name, content in files.items():
        (run_dir / name).write_text(content, encoding="utf-8")

    archive = run_dir / f"{run_dir.name}.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            # utf-8-sig：Excel 直接打开不乱码，与聚宽导出一致
            zf.writestr(name, content.encode("utf-8-sig"))
    archive.write_bytes(buffer.getvalue())

    log.info(
        "backtest.artifacts_written",
        run_dir=str(run_dir), files=len(files), lots=len(lots),
    )
    return run_dir


def _jsonable(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _nav(result) -> pl.DataFrame:
    """列名对齐聚宽 daily_nav.csv。"""
    if result.equity.height == 0:
        return pl.DataFrame(
            schema={"date": pl.Date, "total_value": pl.Float64,
                    "available_cash": pl.Float64, "position_count": pl.Int64,
                    "daily_return": pl.Float64}
        )
    held = _positions_by_day(result)
    return (
        result.equity.rename({"dt": "date", "cash": "available_cash"})
        .with_columns(
            (pl.col("total_value") / pl.col("total_value").shift(1) - 1.0)
            .fill_null(0.0)
            .alias("daily_return"),
            pl.col("date")
            .map_elements(lambda d: held.get(d, 0), return_dtype=pl.Int64)
            .alias("position_count"),
        )
        .select(["date", "total_value", "available_cash", "position_count",
                 "daily_return"])
    )


def _positions_by_day(result) -> dict[date, int]:
    """每个交易日收盘时的持仓只数，从流水重放得出。"""
    qty: dict[str, int] = {}
    out: dict[date, int] = {}
    days = sorted({d for d in result.equity["dt"].to_list()})
    by_day: dict[date, list] = {}
    for fill in result.fills:
        by_day.setdefault(fill.filled_at.date(), []).append(fill)
    for day in days:
        for fill in by_day.get(day, []):
            symbol = str(fill.symbol)
            delta = fill.qty if fill.side.value == "buy" else -fill.qty
            qty[symbol] = qty.get(symbol, 0) + delta
            if qty[symbol] <= 0:
                qty.pop(symbol, None)
        out[day] = len(qty)
    return out


def _fills(result) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "filled_at": [f.filled_at for f in result.fills],
            "stock": [to_joinquant_code(str(f.symbol)) for f in result.fills],
            "symbol": [str(f.symbol) for f in result.fills],
            "side": [f.side.value for f in result.fills],
            "qty": [f.qty for f in result.fills],
            "price": [f.price for f in result.fills],
            "fee": [f.fee for f in result.fills],
            "notional": [f.qty * f.price for f in result.fills],
        },
        schema={"filled_at": pl.Datetime, "stock": pl.Utf8, "symbol": pl.Utf8,
                "side": pl.Utf8, "qty": pl.Int64, "price": pl.Float64,
                "fee": pl.Float64, "notional": pl.Float64},
    )


def _buy_details(result, tags: Mapping[str, str]) -> pl.DataFrame:
    """对齐聚宽 daily_buy_details.csv：同一日同一标的只留一行。"""
    seen: set[tuple[date, str]] = set()
    rows = []
    for fill in sorted(result.fills, key=lambda f: f.filled_at):
        if fill.side.value != "buy":
            continue
        symbol = str(fill.symbol)
        key = (fill.filled_at.date(), symbol)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "date": key[0],
            "stock": to_joinquant_code(symbol),
            "symbol": symbol,
            "strategy_tag": tags.get(symbol, "UNKNOWN"),
        })
    return pl.DataFrame(
        rows,
        schema={"date": pl.Date, "stock": pl.Utf8, "symbol": pl.Utf8,
                "strategy_tag": pl.Utf8},
    )


def _lots_frame(lots: Sequence[ClosedLot]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "lot_id": x.lot_id,
                "stock": to_joinquant_code(x.symbol),
                "symbol": x.symbol,
                "strategy_tag": x.strategy_tag,
                "entry_time": x.entry_time,
                "exit_time": x.exit_time,
                "entry_price": x.entry_price,
                "exit_price": x.exit_price,
                "qty": x.qty,
                "return_pct": x.return_pct,
                "realized_pnl_gross": x.realized_pnl_gross,
                "holding_trade_days": x.holding_trade_days,
                "entry_notional": x.entry_notional,
            }
            for x in lots
        ],
        schema={
            "lot_id": pl.Utf8, "stock": pl.Utf8, "symbol": pl.Utf8,
            "strategy_tag": pl.Utf8, "entry_time": pl.Datetime,
            "exit_time": pl.Datetime, "entry_price": pl.Float64,
            "exit_price": pl.Float64, "qty": pl.Int64, "return_pct": pl.Float64,
            "realized_pnl_gross": pl.Float64, "holding_trade_days": pl.Int64,
            "entry_notional": pl.Float64,
        },
    )


def _rejects(result) -> pl.DataFrame:
    """被拒的单。聚宽没有对应物——但「为什么没买成」是对账最关键的一列。"""
    return pl.DataFrame(
        [
            {
                "at": r.at,
                "order_id": r.order_id,
                "stock": to_joinquant_code(str(r.symbol)),
                "symbol": str(r.symbol),
                "side": r.side.value,
                "qty": r.qty,
                "reason": r.reason,
            }
            for r in result.rejects
        ],
        schema={"at": pl.Datetime, "order_id": pl.Utf8, "stock": pl.Utf8,
                "symbol": pl.Utf8, "side": pl.Utf8, "qty": pl.Int64,
                "reason": pl.Utf8},
    )


def _orders(result) -> pl.DataFrame:
    orders = getattr(result, "orders", ())
    return pl.DataFrame(
        [{"created_at": o.created_at, "order_id": o.order_id,
          "stock": to_joinquant_code(str(o.symbol)), "symbol": str(o.symbol),
          "side": o.side.value, "qty": o.qty, "order_type": o.order_type.value,
          "limit_price": o.limit_price} for o in orders],
        schema={"created_at": pl.Datetime, "order_id": pl.Utf8, "stock": pl.Utf8,
                "symbol": pl.Utf8, "side": pl.Utf8, "qty": pl.Int64,
                "order_type": pl.Utf8, "limit_price": pl.Float64},
    )


def _positions(result) -> pl.DataFrame:
    frame = getattr(result, "positions", None)
    if frame is None:
        return pl.DataFrame(schema={"date": pl.Date, "symbol": pl.Utf8})
    return frame.rename({"dt": "date"})


def _monthly_returns(result) -> pl.DataFrame:
    if result.equity.height == 0:
        return pl.DataFrame(schema={"month": pl.Utf8, "return": pl.Float64})
    frame = result.equity.sort("dt").with_columns(
        pl.col("dt").dt.strftime("%Y-%m").alias("month")
    )
    return frame.with_columns(
        (pl.col("total_value") / pl.col("total_value").shift(1) - 1.0)
        .fill_null(0.0).alias("period_return")
    ).group_by("month", maintain_order=True).agg(
        ((pl.col("period_return") + 1.0).product() - 1.0).alias("return")
    )


def _gate_decisions(decisions: Sequence) -> pl.DataFrame:
    """对齐聚宽 gate_decisions.csv，含被拒的判定。"""
    return pl.DataFrame(
        [
            {
                "date": d.dt,
                "stock": to_joinquant_code(d.symbol),
                "symbol": d.symbol,
                "strategy_tag": d.tag,
                "s7_state": d.state,
                "reason": d.reason,
                "real_allow": d.allow,
            }
            for d in decisions
        ],
        schema={"date": pl.Date, "stock": pl.Utf8, "symbol": pl.Utf8,
                "strategy_tag": pl.Utf8, "s7_state": pl.Utf8,
                "reason": pl.Utf8, "real_allow": pl.Boolean},
    )


def _candidate_funnel(
    traces: Sequence[Mapping[str, object]],
) -> pl.DataFrame:
    """候选级只读研究留痕；不改变原有 Gate 产物 schema。"""
    if not traces:
        return pl.DataFrame(schema={
            "date": pl.Date,
            "symbol": pl.Utf8,
            "terminal_stage": pl.Utf8,
            "terminal_reason": pl.Utf8,
        })
    return pl.DataFrame(list(traces), infer_schema_length=None, strict=False).sort(
        "date", "symbol"
    )


def _branch_summary(lots: Sequence[ClosedLot]) -> pl.DataFrame:
    frame = _lots_frame(lots)
    cols = {"strategy_tag": pl.Utf8, "closed_lots": pl.Int64, "wins": pl.Int64,
            "win_rate": pl.Float64, "avg_return_pct": pl.Float64,
            "median_return_pct": pl.Float64, "gross_realized_pnl": pl.Float64,
            "avg_holding_trade_days": pl.Float64}
    if frame.height == 0:
        return pl.DataFrame(schema=cols)
    return (
        frame.group_by("strategy_tag")
        .agg(
            pl.len().cast(pl.Int64).alias("closed_lots"),
            (pl.col("return_pct") > 0).sum().cast(pl.Int64).alias("wins"),
            (pl.col("return_pct") > 0).mean().alias("win_rate"),
            pl.col("return_pct").mean().alias("avg_return_pct"),
            pl.col("return_pct").median().alias("median_return_pct"),
            pl.col("realized_pnl_gross").sum().alias("gross_realized_pnl"),
            pl.col("holding_trade_days").cast(pl.Float64).mean()
            .alias("avg_holding_trade_days"),
        )
        .sort("strategy_tag")
    )


def _regime(regime: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    keep = regime.filter(pl.col("dt").is_between(start, end)).sort("dt")
    rename = {k: v for k, v in _REGIME_RENAME.items() if k in keep.columns}
    return keep.rename({"dt": "source_market_date", **rename})


def _metrics(metrics: Mapping[str, float]) -> pl.DataFrame:
    return pl.DataFrame(
        [{"metric": k, "value": v} for k, v in metrics.items()],
        schema={"metric": pl.Utf8, "value": pl.Float64},
    )


def _status(
    result, strategy: str, start: date, end: date, initial_cash: float,
    lots: Sequence[ClosedLot], decisions: Sequence,
) -> pl.DataFrame:
    buys = [f for f in result.fills if f.side.value == "buy"]
    sells = [f for f in result.fills if f.side.value == "sell"]
    allowed = sum(1 for d in decisions if d.allow)
    final = result.final_value
    return pl.DataFrame([{
        "run_id": result.run_id,
        "strategy": strategy,
        "start": str(start),
        "end": str(end),
        "sessions": result.equity.height,
        "initial_value": initial_cash,
        "total_value": final,
        "cumulative_return": final / initial_cash - 1.0 if initial_cash else None,
        "fills": len(result.fills),
        "buys": len(buys),
        "sells": len(sells),
        "rejects": len(result.rejects),
        "closed_lots": len(lots),
        "gate_decisions": len(decisions),
        "gate_allowed": allowed,
        "gate_rejected": len(decisions) - allowed,
    }])


def _readme(
    strategy: str, start: date, end: date, initial_cash: float, run_id: str
) -> str:
    return (
        f"qsys 回测运行 {run_id}\n"
        f"策略 {strategy}\n"
        f"区间 {start} ~ {end}\n"
        f"初始资金 {initial_cash:,.2f}\n\n"
        "文件名与列名刻意对齐聚宽的研究导出，两边可直接 diff：\n"
        "  daily_nav.csv          date/total_value/available_cash/position_count/daily_return\n"
        "  daily_buy_details.csv  date/stock/strategy_tag（同日同标的只一行）\n"
        "  closed_lots.csv        逐笔配对的买入->卖出\n"
        "  gate_decisions.csv     date/stock/strategy_tag/s7_state/reason/real_allow\n"
        "  candidate_funnel.csv   候选级入档条件与门禁研究留痕\n"
        "  regime_daily.csv       Gate 用到的每日市场因子\n"
        "  branch_summary.csv     分支维度的统计\n"
        "  run_status.csv         这次运行的一行摘要\n\n"
        "我们比聚宽多的：\n"
        "  fills.csv              逐笔成交，含费用与成交金额\n"
        "  rejects.csv            被拒的单与原因（聚宽没有对应物）\n"
        "  metrics.csv            收益/夏普/回撤等\n"
        "  config.json            这次运行的完整配置\n\n"
        "标的代码两种格式都写：stock 是聚宽的 XSHE/XSHG，symbol 是我们的 SZ/SH。\n"
    )
