"""The first real order must fail closed before it reaches Redis."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from qsys.live.commissioning import (
    COMMISSIONING_KEY,
    COMMISSIONING_MAX_NOTIONAL,
    COMMISSIONING_QTY,
    COMMISSIONING_SYMBOL,
    CommissioningLease,
)
from qsys.live.config import SafetyConfig, StreamConfig
from qsys.live.messages import (
    AccountSnapshot, Heartbeat, MarketQuote, MarketSchedule, PositionSnapshot,
)
from qsys.live.transport import MemoryTransport
from scripts import commissioning_order as cli

NOW = datetime(2026, 10, 12, 10)


@dataclass
class FakeConfig:
    safety: SafetyConfig = field(default_factory=lambda: SafetyConfig(
        mode="live", orders_enabled=True, protected_account_ids=("A1",),
    ))
    streams: StreamConfig = field(default_factory=StreamConfig)


@dataclass
class Args:
    symbol: str = COMMISSIONING_SYMBOL
    side: str = "buy"
    qty: int = COMMISSIONING_QTY
    limit: float = 12.8


def _transport(*, core_age=None, agent_age=1.0, killed=False, lease=True,
               positions=None, schedule_open=True):
    t = MemoryTransport()
    s = StreamConfig()
    if lease:
        armed = NOW - timedelta(seconds=5)
        t.set_key(COMMISSIONING_KEY, CommissioningLease(
            armed_at=armed, expires_at=armed + timedelta(minutes=10),
        ).model_dump_json())
    if agent_age is not None:
        t.set_key(f"{s.heartbeat}:agent", Heartbeat(
            source="agent", at=NOW - timedelta(seconds=agent_age),
            trading_mode="live", orders_enabled=True, account_protected=True,
            local_kill_engaged=False, commissioning_lease_active=True,
        ).model_dump_json())
    if core_age is not None:
        t.set_key(f"{s.heartbeat}:core", Heartbeat(
            source="core", at=NOW - timedelta(seconds=core_age),
        ).model_dump_json())
    t.set_key(s.market_schedule, MarketSchedule(
        checked_at=NOW - timedelta(seconds=1), is_trading_day=True,
        is_open=schedule_open,
    ).model_dump_json())
    t.set_key(s.account, AccountSnapshot(
        at=NOW - timedelta(seconds=1), cash=100_000,
        positions=positions or {},
    ).model_dump_json())
    if killed:
        t.set_key("control:kill", "manual")
    return t


def test_a_running_live_core_blocks_the_order():
    refusal = cli._preflight(
        FakeConfig(), _transport(core_age=1.0), Args(), now=NOW,
    )
    assert refusal is not None and "核心" in refusal and "对账" in refusal


def test_a_stopped_core_with_a_lease_is_fine():
    assert cli._preflight(
        FakeConfig(), _transport(core_age=120.0), Args(), now=NOW,
    ) is None


@pytest.mark.parametrize("safety,expect", [
    (SafetyConfig(mode="shadow", orders_enabled=False), "写保护"),
    (SafetyConfig(mode="live", orders_enabled=False), "写保护"),
    (SafetyConfig(mode="live", orders_enabled=True), "白名单"),
])
def test_the_three_write_protections_are_all_required(safety, expect):
    refusal = cli._preflight(
        FakeConfig(safety=safety), _transport(), Args(), now=NOW,
    )
    assert refusal is not None and expect in refusal


def test_symbol_size_and_notional_are_fixed():
    wrong_symbol = cli._preflight(
        FakeConfig(), _transport(), Args(symbol="510300.SH"), now=NOW,
    )
    wrong_qty = cli._preflight(
        FakeConfig(), _transport(), Args(qty=200), now=NOW,
    )
    over_cash = cli._preflight(
        FakeConfig(), _transport(), Args(limit=COMMISSIONING_MAX_NOTIONAL), now=NOW,
    )
    assert "固定" in wrong_symbol
    assert "固定" in wrong_qty
    assert "上限" in over_cash


def test_lease_and_safe_trading_window_are_required():
    assert "未武装" in cli._preflight(
        FakeConfig(), _transport(lease=False), Args(), now=NOW,
    )
    assert "09:35" in cli._preflight(
        FakeConfig(), _transport(), Args(), now=NOW.replace(hour=9, minute=20),
    )


def test_stale_or_missing_agent_blocks_the_order():
    stale = cli._preflight(
        FakeConfig(), _transport(agent_age=60.0), Args(), now=NOW,
    )
    missing = cli._preflight(
        FakeConfig(), _transport(agent_age=None), Args(), now=NOW,
    )
    assert "心跳" in stale
    assert "代理" in missing


@pytest.mark.parametrize("field,value,expect", [
    ("trading_mode", "shadow", "trading_mode"),
    ("orders_enabled", False, "orders_enabled"),
    ("account_protected", False, "白名单"),
    ("local_kill_engaged", True, "停机闸"),
    ("commissioning_lease_active", False, "验证窗口"),
])
def test_agent_must_announce_all_remote_guards(field, value, expect):
    transport = _transport()
    key = f"{StreamConfig().heartbeat}:agent"
    heartbeat = json.loads(transport.get_key(key))
    heartbeat[field] = value
    transport.set_key(key, json.dumps(heartbeat))
    refusal = cli._preflight(FakeConfig(), transport, Args(), now=NOW)
    assert refusal is not None and expect in refusal


def test_buy_requires_empty_account_and_sell_requires_available_target():
    position = PositionSnapshot(total_qty=100, available_qty=100, avg_cost=12.8)
    buy = cli._preflight(
        FakeConfig(), _transport(positions={COMMISSIONING_SYMBOL: position}),
        Args(), now=NOW,
    )
    sell = cli._preflight(
        FakeConfig(), _transport(), Args(side="sell"), now=NOW,
    )
    assert "空仓" in buy
    assert "精确等于" in sell


def test_sell_accepts_the_exact_available_position():
    position = PositionSnapshot(total_qty=100, available_qty=100, avg_cost=12.8)
    refusal = cli._preflight(
        FakeConfig(), _transport(positions={COMMISSIONING_SYMBOL: position}),
        Args(side="sell"), now=NOW,
    )
    assert refusal is None


def test_sell_accepts_an_exact_partial_fill_remainder_only():
    position = PositionSnapshot(total_qty=40, available_qty=40, avg_cost=12.8)
    assert cli._preflight(
        FakeConfig(), _transport(positions={COMMISSIONING_SYMBOL: position}),
        Args(side="sell", qty=40), now=NOW,
    ) is None
    refusal = cli._preflight(
        FakeConfig(), _transport(positions={COMMISSIONING_SYMBOL: position}),
        Args(side="sell", qty=30), now=NOW,
    )
    assert refusal is not None and "精确等于" in refusal


def test_kill_switch_and_closed_schedule_block_the_order():
    assert "停机" in cli._preflight(
        FakeConfig(), _transport(killed=True), Args(), now=NOW,
    )
    assert "交易时段" in cli._preflight(
        FakeConfig(), _transport(schedule_open=False), Args(), now=NOW,
    )


def test_quote_guard_uses_the_live_book_and_etf_tick_size():
    quote = MarketQuote(
        symbol=COMMISSIONING_SYMBOL, at=NOW, price=12.8, open=12.8,
        high=12.8, low=12.8, prev_close=12.8, bid1=12.799, ask1=12.801,
    )
    assert cli._quote_refusal(Args(limit=12.801), quote) is None
    assert "0.5%" in cli._quote_refusal(Args(limit=13.0), quote)
    assert "价格档位" in cli._quote_refusal(Args(limit=12.8005), quote)
    assert "0.5%" in cli._quote_refusal(Args(side="sell", limit=12.7), quote)
