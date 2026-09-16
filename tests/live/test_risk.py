from datetime import date, datetime

import pytest

from qsys.core.calendar import AShareCalendar
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, OrderIntent, Side
from qsys.core.types import Market, Symbol
from qsys.live.kill_switch import KillSwitch
from qsys.live.risk import (
    DailyLossCircuitBreaker,
    KillSwitchRule,
    MaxDailyTurnoverRule,
    MaxPositionWeightRule,
    PriceDeviationRule,
    RiskContext,
    RiskGate,
    TradingHoursRule,
    default_ashare_gate,
)
from qsys.live.transport import MemoryTransport

CMB = Symbol("600036", Market.SH)
EXPENSIVE = Symbol("600000", Market.SH)
SESSIONS = [date(2024, 1, 3)]
CAL = AShareCalendar(SESSIONS)
T = datetime(2024, 1, 3, 9, 45)


def ctx(*, held: int = 0, cash: float = 100_000.0, price: float = 10.0,
        day_start: float = 100_000.0, turnover: float = 0.0,
        now: datetime = T) -> RiskContext:
    p = Portfolio(cash)
    if held:
        p.apply_fill(Fill("o", CMB, Side.BUY, held, price, 0.0, T))
        p.unfreeze()
    return RiskContext(portfolio=p, now=now, reference_price=price,
                       day_start_value=day_start, turnover_today=turnover)


BUY = OrderIntent(CMB, Side.BUY, 100)
SELL = OrderIntent(CMB, Side.SELL, 100)


def test_kill_switch_allows_when_disengaged(tmp_path):
    ks = KillSwitch(MemoryTransport(), local_path=tmp_path / "K")
    assert KillSwitchRule(ks).check(BUY, ctx()).allowed is True


def test_kill_switch_blocks_opening_but_allows_reducing_when_engaged(tmp_path):
    ks = KillSwitch(MemoryTransport(), local_path=tmp_path / "K")
    ks.engage("stop")
    rule = KillSwitchRule(ks)
    assert rule.check(BUY, ctx()).allowed is False
    assert rule.check(SELL, ctx()).allowed is True


def test_kill_switch_reason_is_surfaced(tmp_path):
    ks = KillSwitch(MemoryTransport(), local_path=tmp_path / "K")
    ks.engage("circuit breaker")
    assert "circuit breaker" in KillSwitchRule(ks).check(BUY, ctx()).reason


def test_position_weight_allows_within_limit():
    r = MaxPositionWeightRule(0.20)
    assert r.check(BUY, ctx(held=1000)).allowed is True


def test_position_weight_blocks_beyond_limit():
    r = MaxPositionWeightRule(0.10)
    verdict = r.check(BUY, ctx(held=1000))
    assert verdict.allowed is False
    assert "weight" in verdict.reason


def test_position_weight_never_blocks_a_sell():
    """减仓永远放行——风控不能把人锁在仓位里。"""
    r = MaxPositionWeightRule(0.01)
    assert r.check(SELL, ctx(held=5000)).allowed is True


def test_portfolio_value_uses_each_positions_own_mark():
    portfolio = Portfolio(110_000.0)
    portfolio.apply_fill(Fill("seed", EXPENSIVE, Side.BUY, 1000, 100.0, 0.0, T))
    context = RiskContext(
        portfolio=portfolio, now=T, reference_price=5.0, day_start_value=100_000.0,
        marks={EXPENSIVE: 100.0, CMB: 5.0},
    )
    # 新订单 500 元，只占约 0.5%；旧实现会把全部持仓错按 5 元估值。
    assert MaxPositionWeightRule(0.01).check(BUY, context).allowed is True


def test_turnover_allows_within_limit():
    r = MaxDailyTurnoverRule(0.50)
    assert r.check(BUY, ctx(turnover=10_000.0)).allowed is True


def test_turnover_blocks_beyond_limit():
    r = MaxDailyTurnoverRule(0.10)
    verdict = r.check(BUY, ctx(turnover=9_500.0))
    assert verdict.allowed is False
    assert "turnover" in verdict.reason


def test_circuit_breaker_allows_within_drawdown():
    r = DailyLossCircuitBreaker(0.05)
    assert r.check(BUY, ctx(cash=98_000.0, day_start=100_000.0)).allowed is True


def test_circuit_breaker_blocks_after_a_big_loss():
    r = DailyLossCircuitBreaker(0.02)
    verdict = r.check(BUY, ctx(cash=95_000.0, day_start=100_000.0))
    assert verdict.allowed is False
    assert "loss" in verdict.reason


def test_circuit_breaker_still_allows_selling():
    """熔断后仍可减仓——否则会被锁死在亏损仓位里。"""
    r = DailyLossCircuitBreaker(0.02)
    assert r.check(SELL, ctx(cash=95_000.0, day_start=100_000.0, held=100)).allowed is True


def test_price_deviation_allows_normal_price():
    r = PriceDeviationRule(0.10)
    assert r.check(BUY, ctx(price=10.0), reference=10.2).allowed is True


def test_price_deviation_blocks_absurd_price():
    """防参数写错下出离谱价格。"""
    r = PriceDeviationRule(0.10)
    verdict = r.check(BUY, ctx(price=100.0), reference=10.0)
    assert verdict.allowed is False
    assert "deviation" in verdict.reason


def test_price_deviation_without_a_reference_allows():
    r = PriceDeviationRule(0.10)
    assert r.check(BUY, ctx(price=10.0), reference=None).allowed is True


def test_trading_hours_allows_during_session():
    r = TradingHoursRule(CAL)
    assert r.check(BUY, ctx(now=datetime(2024, 1, 3, 10, 0))).allowed is True


def test_trading_hours_blocks_before_open():
    r = TradingHoursRule(CAL)
    verdict = r.check(BUY, ctx(now=datetime(2024, 1, 3, 8, 0)))
    assert verdict.allowed is False
    assert "hours" in verdict.reason


def test_trading_hours_blocks_after_close():
    r = TradingHoursRule(CAL)
    assert r.check(BUY, ctx(now=datetime(2024, 1, 3, 16, 0))).allowed is False


def test_trading_hours_blocks_on_a_non_trading_day():
    r = TradingHoursRule(CAL)
    verdict = r.check(BUY, ctx(now=datetime(2024, 1, 6, 10, 0)))
    assert verdict.allowed is False
    assert "trading day" in verdict.reason


def test_trading_hours_allows_lunch_break_by_default():
    """午休期间挂单是常见做法（排队等下午开盘），默认不拦。"""
    r = TradingHoursRule(CAL)
    assert r.check(BUY, ctx(now=datetime(2024, 1, 3, 12, 0))).allowed is True


def gate(tmp_path, **kw):
    ks = KillSwitch(MemoryTransport(), local_path=tmp_path / "K")
    return default_ashare_gate(ks, CAL, **kw), ks


def test_gate_allows_a_clean_order(tmp_path):
    g, _ = gate(tmp_path)
    assert g.check(BUY, ctx()).allowed is True


def test_gate_stops_at_the_first_blocking_rule(tmp_path):
    g, ks = gate(tmp_path)
    ks.engage("halt")
    decision = g.check(BUY, ctx())
    assert decision.allowed is False
    assert decision.blocking_rule == "kill_switch"
    assert [name for name, _ in decision.checks] == ["kill_switch"]


def test_gate_records_every_rule_that_ran(tmp_path):
    g, _ = gate(tmp_path)
    decision = g.check(BUY, ctx())
    assert [name for name, _ in decision.checks] == list(g.names)


def test_gate_reason_names_the_blocking_rule(tmp_path):
    g, _ = gate(tmp_path, max_position_weight=0.01)
    decision = g.check(BUY, ctx(held=1000))
    assert decision.allowed is False
    assert decision.reason.startswith("max_position_weight")


def test_gate_log_fields_are_flat(tmp_path):
    g, _ = gate(tmp_path)
    fields = g.check(BUY, ctx()).as_log_fields()
    assert fields["allowed"] is True
    assert isinstance(fields["checks"], str)


def test_empty_gate_allows_everything():
    assert RiskGate([]).check(BUY, ctx()).allowed is True


def test_default_gate_rule_order_puts_kill_switch_first(tmp_path):
    """kill switch 必须最先检查——按下之后一切都该立即停。"""
    g, _ = gate(tmp_path)
    assert g.names[0] == "kill_switch"


def test_default_gate_includes_all_six_rules(tmp_path):
    g, _ = gate(tmp_path)
    assert set(g.names) == {
        "kill_switch", "trading_hours", "price_deviation",
        "daily_loss_breaker", "max_position_weight", "max_daily_turnover",
    }
