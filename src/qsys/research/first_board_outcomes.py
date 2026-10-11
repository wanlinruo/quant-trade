"""Candidate-level counterfactual fills and exits for Issue #230."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Sequence

import polars as pl

from qsys.backtest.entry_fill import EntryFill, EntryFillMode, MinuteBar, decide
from qsys.backtest.fees import (
    ACCOUNT_COMMISSION_RATE,
    ACCOUNT_MIN_COMMISSION,
    AShareFeeModel,
)
from qsys.backtest.slippage import FixedRateSlippage
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Symbol
from qsys.research.first_board_quality import (
    PRIMARY_COST_STRESS,
    sealed_at_decision,
)


ENTRY_AT = time(9, 31)
CHECK_1125 = time(11, 25)
CHECK_1425 = time(14, 25)


@dataclass(slots=True)
class _Lot:
    row_index: int
    symbol: str
    policy: str
    entry_date: date
    entry_price: float
    qty: int
    committed: float
    cash_dividend: float = 0.0
    sell_blocked: bool = False
    min_price: float | None = None


def _latest_prices(frame: pl.DataFrame, day: date, at: time) -> dict[str, float]:
    if frame.is_empty():
        return {}
    moment = datetime.combine(day, at)
    rows = (
        frame.filter(pl.col("visible_at") <= moment)
        .sort(["symbol", "visible_at", "ts"])
        .group_by("symbol", maintain_order=True)
        .last()
    )
    return {
        row["symbol"]: float(row["close"])
        for row in rows.iter_rows(named=True)
        if row["close"] is not None and float(row["close"]) > 0
    }


def _entry_minutes(frame: pl.DataFrame, day: date) -> dict[str, MinuteBar]:
    if frame.is_empty():
        return {}
    moment = datetime.combine(day, ENTRY_AT)
    rows = (
        frame.filter(pl.col("visible_at") == moment)
        .sort(["symbol", "ts"])
        .group_by("symbol", maintain_order=True)
        .last()
    )
    return {
        row["symbol"]: MinuteBar(float(row["open"]), float(row["low"]))
        for row in rows.iter_rows(named=True)
        if row["open"] is not None and row["low"] is not None
        and float(row["open"]) > 0 and float(row["low"]) > 0
    }


def build_counterfactual_outcomes(
    query, candidates: pl.DataFrame, sessions: Sequence[date], *,
    cash_per_slot: float = 10_000.0,
    entry_price_buffer: float = 0.03,
    cost_stress: float = PRIMARY_COST_STRESS,
) -> pl.DataFrame:
    """Simulate every candidate independently with no portfolio competition."""
    required = {
        "prediction_date", "target_session", "symbol", "decision_price", "limit_up",
    }
    if missing := required - set(candidates.columns):
        raise ValueError(f"counterfactual candidates miss columns: {sorted(missing)}")
    candidates = candidates.sort("target_session", "rank").with_row_index("row_index")
    symbols = sorted(candidates["symbol"].unique().to_list())
    first, last = min(sessions), max(sessions)
    daily = query.bar_daily(
        symbols, first, last,
        columns=("symbol", "dt", "open", "low", "close", "pre_close"),
    )
    daily_by_key = {
        (row["dt"], row["symbol"]): row for row in daily.iter_rows(named=True)
    }
    limits = query.price_limit_daily(symbols, first, last)
    limits_by_key = {
        (row["dt"], row["symbol"]): row for row in limits.iter_rows(named=True)
    }
    actions = query.corporate_actions(symbols)
    actions_by_key = {
        (row["ex_date"], row["symbol"]): row for row in actions.iter_rows(named=True)
    }
    by_day = {
        key[0]: group for key, group in candidates.partition_by(
            "target_session", as_dict=True, maintain_order=True,
        ).items()
    }
    results = [{
        "row_index": int(row["row_index"]), "entry_filled": False,
        "entry_reason": "not evaluated", "entry_price": None, "entry_qty": 0,
        "next_open_return_pct": None, "next_open_exit_date": None,
        "next_open_exit_price": None,
        "next_open_sell_blocked": False,
        "two_stage_limit_hold_return_pct": None,
        "two_stage_limit_hold_exit_date": None,
        "two_stage_limit_hold_exit_price": None,
        "two_stage_sell_blocked": False,
        "two_stage_holding_days": None, "max_adverse_excursion": None,
    } for row in candidates.iter_rows(named=True)]
    active: list[_Lot] = []
    fee_model = AShareFeeModel(
        commission_rate=ACCOUNT_COMMISSION_RATE,
        min_commission=ACCOUNT_MIN_COMMISSION,
    )
    entry_rule = EntryFill(
        EntryFillMode.CONTINUOUS, minute_at=ENTRY_AT,
        price_stress=cost_stress, fallback_on_missing=False,
    )
    slippage = FixedRateSlippage(0.0005)
    positions = {day: index for index, day in enumerate(sessions)}

    for day in sessions:
        entering = by_day.get(day)
        wanted = sorted({lot.symbol for lot in active} | (
            set(entering["symbol"].to_list()) if entering is not None else set()
        ))
        minute = query.bar_minute_1m(
            wanted, datetime.combine(day, time(9, 30)),
            datetime.combine(day, CHECK_1425),
            columns=("symbol", "ts", "open", "low", "close", "visible_at"),
        ) if wanted else pl.DataFrame()
        prices_1125 = _latest_prices(minute, day, CHECK_1125)
        prices_1425 = _latest_prices(minute, day, CHECK_1425)

        remaining: list[_Lot] = []
        for lot in active:
            result = results[lot.row_index]
            daily_row = daily_by_key.get((day, lot.symbol))
            limit = limits_by_key.get((day, lot.symbol))
            action = actions_by_key.get((day, lot.symbol))
            if action is not None:
                lot.cash_dividend += lot.qty * float(action.get("cash_per_share") or 0.0)
                lot.qty += int(lot.qty * float(action.get("stock_per_share") or 0.0))
            if daily_row is not None and daily_row["low"] is not None:
                low = float(daily_row["low"])
                lot.min_price = low if lot.min_price is None else min(lot.min_price, low)
            if daily_row is None or limit is None:
                remaining.append(lot)
                continue
            up, down = float(limit["limit_up"]), float(limit["limit_down"])
            exit_price: float | None = None
            if lot.policy == "next_open":
                opened = float(daily_row["open"])
                if opened <= down + 0.005:
                    lot.sell_blocked = True
                else:
                    exit_price = slippage.adjust(Side.SELL, opened)
            else:
                p1125, p1425 = prices_1125.get(lot.symbol), prices_1425.get(lot.symbol)
                previous = daily_row.get("pre_close")
                if p1125 is not None and previous is not None \
                        and p1125 > float(previous) and p1125 < up - 0.005:
                    if p1125 <= down + 0.005:
                        lot.sell_blocked = True
                    else:
                        exit_price = slippage.adjust(Side.SELL, p1125)
                elif p1425 is not None and p1425 < up - 0.005:
                    if p1425 <= down + 0.005:
                        lot.sell_blocked = True
                    else:
                        exit_price = slippage.adjust(Side.SELL, p1425)
            if exit_price is None:
                remaining.append(lot)
                continue
            sell_gross = exit_price * lot.qty
            proceeds = (
                sell_gross - fee_model.fee_for(Side.SELL, sell_gross)
                + lot.cash_dividend
            )
            value = 100.0 * (proceeds / lot.committed - 1.0)
            if lot.policy == "next_open":
                result["next_open_return_pct"] = value
                result["next_open_exit_date"] = day
                result["next_open_exit_price"] = exit_price
                result["next_open_sell_blocked"] = lot.sell_blocked
            else:
                result["two_stage_limit_hold_return_pct"] = value
                result["two_stage_limit_hold_exit_date"] = day
                result["two_stage_limit_hold_exit_price"] = exit_price
                result["two_stage_sell_blocked"] = lot.sell_blocked
                result["two_stage_holding_days"] = (
                    positions[day] - positions[lot.entry_date]
                )
                if lot.min_price is not None:
                    result["max_adverse_excursion"] = lot.min_price / lot.entry_price - 1.0
        active = remaining

        if entering is None:
            continue
        entry_minutes = _entry_minutes(minute, day)
        for row in entering.iter_rows(named=True):
            index = int(row["row_index"])
            result = results[index]
            symbol = str(row["symbol"])
            daily_row = daily_by_key.get((day, symbol))
            limit_up = float(row["limit_up"])
            if daily_row is None:
                result["entry_reason"] = "missing daily bar"
                continue
            if sealed_at_decision(float(row["decision_price"]), limit_up):
                result["entry_reason"] = "sealed at decision"
                continue
            protect = min(
                round(float(row["decision_price"]) * (1 + entry_price_buffer), 2),
                limit_up,
            )
            qty = int(cash_per_slot / protect / 100) * 100
            if qty <= 0:
                result["entry_reason"] = "position budget below one lot"
                continue
            order = Order(
                f"cf-{index}", Symbol.parse(symbol), Side.BUY, qty,
                OrderType.LIMIT, protect, datetime.combine(day, time(9, 30)),
            )
            decision = decide(
                entry_rule, order, float(daily_row["open"]),
                entry_minutes.get(symbol), slippage,
            )
            result["entry_reason"] = decision.outcome
            if decision.price is None:
                continue
            result.update({
                "entry_filled": True, "entry_price": decision.price, "entry_qty": qty,
            })
            gross = decision.price * qty
            committed = gross + fee_model.fee_for(Side.BUY, gross)
            for policy in ("next_open", "two_stage_limit_hold"):
                active.append(_Lot(
                    index, symbol, policy, day, decision.price, qty, committed,
                ))

    outcomes = pl.DataFrame(results)
    return candidates.join(outcomes, on="row_index", how="left", validate="1:1").drop(
        "row_index"
    )
