"""风控规则与闸门。

与约束系统职责不同：
  闸门（本模块）—— 能不能发这一单？是 / 否。管组合级与交易日级限制。
  约束（core）   —— 能发多少？管市场规则与数量夹取。

减仓类规则一律放行卖出——风控不能把人锁在仓位里，尤其是熔断之后。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, time

from qsys.core.calendar import TradingCalendar
from qsys.core.portfolio import Portfolio
from qsys.core.trading import OrderIntent, Side
from qsys.live.kill_switch import KillSwitch
from qsys.core.types import Symbol


@dataclass(frozen=True, slots=True)
class RiskContext:
    portfolio: Portfolio
    now: datetime
    reference_price: float
    day_start_value: float
    turnover_today: float = 0.0
    marks: Mapping[Symbol, float] = field(default_factory=dict)
    frozen_cash: float = 0.0
    pending_buy_value: float = 0.0
    market_reference_price: float | None = None
    orders_last_minute: int = 0
    orders_today: int = 0
    cumulative_volume: int = 0
    previous_day_amount: float = 0.0

    def mark(self, symbol: Symbol) -> float:
        return float(self.marks.get(symbol, self.reference_price))

    def current_value(self) -> float:
        return self.portfolio.cash + sum(
            p.total_qty * self.mark(symbol)
            for symbol, p in self.portfolio.positions.items()
        )


@dataclass(frozen=True, slots=True)
class RiskVerdict:
    allowed: bool
    reason: str = ""


class RiskRule(ABC):
    name: str

    @abstractmethod
    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict: ...


class KillSwitchRule(RiskRule):
    name = "kill_switch"

    def __init__(self, kill_switch: KillSwitch) -> None:
        self._ks = kill_switch

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict:
        if intent.side is Side.SELL:
            return RiskVerdict(True)
        if self._ks.is_engaged():
            return RiskVerdict(False, f"kill switch engaged: {self._ks.reason()}")
        return RiskVerdict(True)


class MaxPositionWeightRule(RiskRule):
    name = "max_position_weight"

    def __init__(self, max_weight: float) -> None:
        if not 0 < max_weight <= 1:
            raise ValueError(f"max_weight must be in (0, 1]: {max_weight}")
        self._max = max_weight

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict:
        if intent.side is Side.SELL:
            return RiskVerdict(True)  # 减仓永远放行
        held = ctx.portfolio.position(intent.symbol).total_qty
        prospective = (held + intent.qty) * ctx.reference_price
        total = ctx.current_value()
        if total <= 0:
            return RiskVerdict(False, "portfolio value is non-positive")
        weight = prospective / total
        if weight > self._max:
            return RiskVerdict(
                False, f"position weight {weight:.2%} exceeds limit {self._max:.2%}"
            )
        return RiskVerdict(True)


class MaxDailyTurnoverRule(RiskRule):
    name = "max_daily_turnover"

    def __init__(self, max_ratio: float) -> None:
        if max_ratio <= 0:
            raise ValueError(f"max_ratio must be > 0: {max_ratio}")
        self._max = max_ratio

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict:
        prospective = ctx.turnover_today + intent.qty * ctx.reference_price
        if ctx.day_start_value <= 0:
            return RiskVerdict(True)
        ratio = prospective / ctx.day_start_value
        if ratio > self._max:
            return RiskVerdict(
                False, f"daily turnover {ratio:.2%} exceeds limit {self._max:.2%}"
            )
        return RiskVerdict(True)


class MaxOrderValueRule(RiskRule):
    name = "max_order_value"

    def __init__(self, maximum: float) -> None:
        self._maximum = maximum

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict:
        value = intent.qty * ctx.reference_price
        if value > self._maximum:
            return RiskVerdict(False, f"order value {value:.2f} exceeds {self._maximum:.2f}")
        return RiskVerdict(True)


class MaxGrossExposureRule(RiskRule):
    name = "max_gross_exposure"

    def __init__(self, maximum: float) -> None:
        self._maximum = maximum

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict:
        if intent.side is Side.SELL:
            return RiskVerdict(True)
        total = ctx.current_value()
        if total <= 0:
            return RiskVerdict(False, "portfolio value is non-positive")
        gross = sum(
            p.total_qty * ctx.mark(symbol)
            for symbol, p in ctx.portfolio.positions.items()
        ) + intent.qty * ctx.reference_price + ctx.pending_buy_value
        ratio = gross / total
        if ratio > self._maximum:
            return RiskVerdict(False, f"gross exposure {ratio:.2%} exceeds {self._maximum:.2%}")
        return RiskVerdict(True)


class OrderRateRule(RiskRule):
    name = "order_rate"

    def __init__(self, per_minute: int, per_day: int) -> None:
        self._minute, self._day = per_minute, per_day

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict:
        if ctx.orders_last_minute >= self._minute:
            return RiskVerdict(False, f"orders/minute reached {self._minute}")
        if ctx.orders_today >= self._day:
            return RiskVerdict(False, f"orders/day reached {self._day}")
        return RiskVerdict(True)


class LiquidityRule(RiskRule):
    name = "liquidity"

    def __init__(self, volume_ratio: float = 0.05, amount_ratio: float = 0.01) -> None:
        self._volume_ratio, self._amount_ratio = volume_ratio, amount_ratio

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict:
        if intent.side is Side.SELL:
            return RiskVerdict(True)
        if ctx.cumulative_volume > 0 and intent.qty > ctx.cumulative_volume * self._volume_ratio:
            return RiskVerdict(False, "order exceeds cumulative-volume participation cap")
        value = intent.qty * ctx.reference_price
        if ctx.previous_day_amount > 0 and value > ctx.previous_day_amount * self._amount_ratio:
            return RiskVerdict(False, "order exceeds previous-day amount cap")
        return RiskVerdict(True)


class DailyLossCircuitBreaker(RiskRule):
    name = "daily_loss_breaker"

    def __init__(self, max_loss_ratio: float) -> None:
        if not 0 < max_loss_ratio < 1:
            raise ValueError(f"max_loss_ratio must be in (0, 1): {max_loss_ratio}")
        self._max = max_loss_ratio

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict:
        if intent.side is Side.SELL:
            return RiskVerdict(True)  # 熔断后仍可减仓，否则会被锁死在亏损仓位里
        if ctx.day_start_value <= 0:
            return RiskVerdict(True)
        current = ctx.current_value()
        loss = 1.0 - current / ctx.day_start_value
        if loss > self._max:
            return RiskVerdict(False, f"daily loss {loss:.2%} exceeds limit {self._max:.2%}")
        return RiskVerdict(True)


class PriceDeviationRule(RiskRule):
    """防参数写错下出离谱价格。"""

    name = "price_deviation"

    def __init__(self, max_deviation: float) -> None:
        if max_deviation <= 0:
            raise ValueError(f"max_deviation must be > 0: {max_deviation}")
        self._max = max_deviation

    def check(
        self, intent: OrderIntent, ctx: RiskContext, *, reference: float | None = None,
        **kwargs,
    ) -> RiskVerdict:
        reference = reference or ctx.market_reference_price
        if reference is None or reference <= 0:
            return RiskVerdict(True)
        deviation = abs(ctx.reference_price / reference - 1.0)
        if deviation > self._max:
            return RiskVerdict(
                False,
                f"price deviation {deviation:.2%} from {reference:.2f} "
                f"exceeds limit {self._max:.2%}",
            )
        return RiskVerdict(True)


class TradingHoursRule(RiskRule):
    name = "trading_hours"

    def __init__(
        self,
        calendar: TradingCalendar,
        open_time: time = time(9, 15),
        close_time: time = time(15, 5),
    ) -> None:
        self._calendar = calendar
        self._open = open_time
        self._close = close_time

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict:
        day = ctx.now.date()
        if not self._calendar.is_trading_day(day):
            return RiskVerdict(False, f"{day} is not a trading day")
        clock = ctx.now.time()
        if not (self._open <= clock <= self._close):
            # 午休期间挂单是常见做法（排队等下午开盘），故不拦
            return RiskVerdict(False, f"{clock} outside trading hours")
        return RiskVerdict(True)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    checks: tuple[tuple[str, RiskVerdict], ...] = ()

    @property
    def blocking_rule(self) -> str:
        for name, verdict in self.checks:
            if not verdict.allowed:
                return name
        return ""

    @property
    def reason(self) -> str:
        for name, verdict in self.checks:
            if not verdict.allowed:
                return f"{name}: {verdict.reason}"
        return ""

    def as_log_fields(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "blocking_rule": self.blocking_rule,
            "reason": self.reason,
            "checks": ",".join(n for n, _ in self.checks),
        }


class RiskGate:
    """按序检查，任一条拒绝即立即停止。"""

    def __init__(self, rules: Sequence[RiskRule]) -> None:
        self._rules = tuple(rules)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(r.name for r in self._rules)

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskDecision:
        checks: list[tuple[str, RiskVerdict]] = []
        for rule in self._rules:
            verdict = rule.check(intent, ctx, **kwargs)
            checks.append((rule.name, verdict))
            if not verdict.allowed:
                return RiskDecision(False, tuple(checks))
        return RiskDecision(True, tuple(checks))


def default_ashare_gate(
    kill_switch: KillSwitch,
    calendar: TradingCalendar,
    *,
    max_position_weight: float = 0.20,
    max_daily_turnover: float = 1.0,
    max_daily_loss: float = 0.05,
    max_price_deviation: float = 0.10,
    production_limits: bool = False,
    max_order_value: float = 10_000.0,
    max_gross_exposure: float = 0.50,
) -> RiskGate:
    """默认 A 股风控闸门。

    kill switch 必须最先检查——按下之后一切都该立即停。
    """
    rules: list[RiskRule] = [
            KillSwitchRule(kill_switch),
            TradingHoursRule(calendar),
            PriceDeviationRule(max_price_deviation),
            DailyLossCircuitBreaker(max_daily_loss),
            MaxPositionWeightRule(max_position_weight),
            MaxDailyTurnoverRule(max_daily_turnover),
    ]
    if production_limits:
        rules.extend([
            MaxOrderValueRule(max_order_value),
            MaxGrossExposureRule(max_gross_exposure),
            OrderRateRule(10, 100),
            LiquidityRule(0.05, 0.01),
        ])
    return RiskGate(rules)
