"""AI 连板强势延续策略。

夜间排名只提供候选；真正的买入决策在次日集合竞价结果可见后完成。
信号文件即使含有历史结果列，本模块也只会读取预测日、目标日、标的、
模型、排名与预测概率，避免策略通过研究产物偷看结果。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

import polars as pl

from qsys.core.trading import OrderType, Side
from qsys.core.types import Symbol
from qsys.strategy.base import Strategy


SAFE_SIGNAL_COLUMNS = (
    "prediction_date", "target_session", "symbol", "model", "rank", "p_close",
)
EXIT_TIMES = {"11:25": time(11, 25), "14:25": time(14, 25)}


@dataclass(frozen=True, slots=True)
class ContinuationSignal:
    prediction_date: date
    target_session: date
    symbol: str
    rank: int
    probability: float


@dataclass(frozen=True, slots=True)
class ContinuationDecision:
    dt: date
    symbol: str
    tag: str
    state: str
    reason: str
    allow: bool


class ContinuationSignalBook:
    """Lazy, read-only projection of a frozen ranking artifact."""

    def __init__(
        self, path: Path, *, model: str = "lightgbm", top_k: int = 3,
        split: str = "ALL",
    ) -> None:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        self.path = Path(path)
        self.model = model
        self.top_k = top_k
        self.split = split.upper()
        self._by_target: dict[date, tuple[ContinuationSignal, ...]] | None = None

    def for_target(self, day: date) -> tuple[ContinuationSignal, ...]:
        if self._by_target is None:
            self._by_target = self._load()
        return self._by_target.get(day, ())

    def _load(self) -> dict[date, tuple[ContinuationSignal, ...]]:
        if not self.path.is_file():
            raise FileNotFoundError(f"AI ranking artifact does not exist: {self.path}")
        lazy = (
            pl.scan_parquet(self.path)
            if self.path.suffix.lower() == ".parquet"
            else pl.scan_csv(self.path, try_parse_dates=True)
        )
        columns = set(lazy.collect_schema().names())
        missing = set(SAFE_SIGNAL_COLUMNS) - columns
        if missing:
            raise ValueError(f"ranking artifact misses columns: {sorted(missing)}")
        filters = (pl.col("model") == self.model) & (pl.col("rank") <= self.top_k)
        if self.split != "ALL":
            if "split" not in columns:
                raise ValueError("signal_split requires a split column")
            filters &= pl.col("split") == self.split
        # Project immediately.  Outcome columns never leave this expression.
        safe = (
            lazy.filter(filters).select(*SAFE_SIGNAL_COLUMNS)
            .sort(["target_session", "rank"]).collect()
        )
        if safe.is_empty():
            raise ValueError(
                f"no {self.model} top-{self.top_k} signals for split {self.split}"
            )
        if any(count > 0 for count in safe.null_count().row(0)):
            raise ValueError("ranking signal contains nulls")
        duplicates = safe.group_by(["target_session", "symbol"]).len().filter(
            pl.col("len") != 1
        )
        if duplicates.height:
            raise ValueError("ranking signal contains duplicate target/symbol rows")

        out: dict[date, tuple[ContinuationSignal, ...]] = {}
        for key, group in safe.group_by("target_session", maintain_order=True):
            target = key[0]
            ranks = group["rank"].to_list()
            if ranks != list(range(1, len(ranks) + 1)):
                raise ValueError(f"ranking is not contiguous for {target}: {ranks}")
            signals = tuple(
                ContinuationSignal(
                    prediction_date=row["prediction_date"],
                    target_session=row["target_session"], symbol=row["symbol"],
                    rank=int(row["rank"]), probability=float(row["p_close"]),
                )
                for row in group.iter_rows(named=True)
            )
            if any(signal.prediction_date >= signal.target_session for signal in signals):
                raise ValueError(f"prediction date must precede target session: {target}")
            out[target] = signals
        return out


class ContinuationSelection:
    def __init__(
        self, book: ContinuationSignalBook, *, gap_min: float, gap_max: float,
    ) -> None:
        if gap_min >= gap_max:
            raise ValueError("gap_min must be below gap_max")
        self.book = book
        self.gap_min = gap_min
        self.gap_max = gap_max
        self.history: list[ContinuationDecision] = []

    def select(self, ctx, day: date) -> list[tuple[ContinuationSignal, float, float]]:
        signals = self.book.for_target(day)
        if not signals:
            return []
        symbols = [signal.symbol for signal in signals]
        opens = ctx.market.session_open(symbols, day)
        limits = ctx.market.limit_up_price(symbols, day)
        suspended = ctx.market.suspended_symbols(symbols, day)
        eligibility = ctx.market.security_eligibility(symbols, day)
        by_prediction: dict[date, list[str]] = {}
        for signal in signals:
            by_prediction.setdefault(signal.prediction_date, []).append(signal.symbol)
        closes: dict[tuple[date, str], float] = {}
        for prediction_day, batch in by_prediction.items():
            frame = ctx.data.get(batch, prediction_day, prediction_day)
            if frame.height:
                closes.update({
                    (prediction_day, row["symbol"]): float(row["close"])
                    for row in frame.select("symbol", "close").iter_rows(named=True)
                    if row["close"] is not None and float(row["close"]) > 0
                })

        allowed: list[tuple[ContinuationSignal, float, float]] = []
        for signal in signals:
            reason = ""
            open_price = opens.get(signal.symbol)
            previous_close = closes.get((signal.prediction_date, signal.symbol))
            limit_up = limits.get(signal.symbol)
            status = eligibility.get(signal.symbol)
            if status is None:
                reason = "missing ST/delisting status"
            elif status[0]:
                reason = "ST"
            elif status[1]:
                reason = "delisting"
            elif signal.symbol in suspended:
                reason = "suspended"
            elif open_price is None or previous_close is None or limit_up is None:
                reason = "missing auction or previous-close truth"
            elif open_price >= limit_up - 1e-6:
                reason = "opened at limit up"
            else:
                gap = open_price / previous_close - 1.0
                if not self.gap_min <= gap < self.gap_max:
                    reason = f"auction gap {gap:.4%} outside range"
                else:
                    allowed.append((signal, open_price, limit_up))
            self.history.append(ContinuationDecision(
                dt=day, symbol=signal.symbol, tag="AI_CONTINUATION",
                state=f"rank={signal.rank};p={signal.probability:.6f}",
                reason=reason or "top rank and auction gate passed",
                allow=not reason,
            ))
        return allowed


class AiLimitUpContinuationStrategy(Strategy):
    """Top-ranked continuation candidates with an executable auction gate."""

    def __init__(
        self, selection: ContinuationSelection, *, calendar=None,
        position_weight: float = 0.10, max_positions: int = 3,
        entry_price_buffer: float = 0.005, exit_policy: str = "next_open",
        auction_at: str = "09:25:10",
    ) -> None:
        if not 0 < position_weight <= 1:
            raise ValueError("position_weight must be in (0, 1]")
        if max_positions < 1 or position_weight * max_positions > 1:
            raise ValueError("position slots exceed total capital")
        if entry_price_buffer < 0:
            raise ValueError("entry_price_buffer must be non-negative")
        if exit_policy not in {"next_open", *EXIT_TIMES}:
            raise ValueError(f"unknown exit policy: {exit_policy}")
        self._selection = selection
        self._calendar = calendar
        self.position_weight = position_weight
        self.max_positions = max_positions
        self.entry_price_buffer = entry_price_buffer
        self.exit_policy = exit_policy
        self.auction_at = auction_at

    def on_init(self, ctx) -> None:
        ctx.schedule.auction(self.auction_at)
        if self.exit_policy in EXIT_TIMES:
            ctx.schedule.checkpoint(EXIT_TIMES[self.exit_policy])

    def on_auction(self, ctx) -> None:
        if self.exit_policy == "next_open":
            self._exit_available(ctx)

        held = set(ctx.portfolio.symbols)
        occupied = {
            symbol for symbol in held
            if self.exit_policy != "next_open"
            or ctx.portfolio.position(symbol).available_qty <= 0
        }
        free_slots = max(self.max_positions - len(occupied), 0)
        if free_slots == 0:
            ctx.report(note="position slots are full")
            return
        selected = self._selection.select(ctx, ctx.now.date())
        rejected = Counter(
            decision.reason for decision in self._selection.history
            if decision.dt == ctx.now.date() and not decision.allow
        )
        passed = 0
        tags = ctx.state.setdefault("tags", {})
        for signal, open_price, limit_up in selected:
            symbol = Symbol.parse(signal.symbol)
            if symbol in held or passed >= free_slots:
                continue
            protect = min(round(open_price * (1 + self.entry_price_buffer), 2), limit_up)
            budget = ctx.portfolio.total_value * self.position_weight
            qty = int(budget / protect / 100) * 100
            if qty <= 0:
                rejected["position budget below one lot"] += 1
                continue
            tags[signal.symbol] = "AI_CONTINUATION"
            ctx.order.submit(symbol, Side.BUY, qty, OrderType.LIMIT, protect)
            passed += 1
        ctx.report(
            candidates=len(self._selection.book.for_target(ctx.now.date())),
            routed=len(selected), passed=passed, reasons=dict(rejected),
        )

    def on_checkpoint(self, ctx, at: time) -> None:
        if EXIT_TIMES.get(self.exit_policy) == at:
            self._exit_available(ctx)

    @staticmethod
    def _exit_available(ctx) -> None:
        for symbol in sorted(ctx.portfolio.symbols, key=str):
            qty = ctx.portfolio.position(symbol).available_qty
            if qty > 0:
                ctx.order.submit(symbol, Side.SELL, qty)
