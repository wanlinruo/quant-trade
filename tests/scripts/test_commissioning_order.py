"""上线验证单的护栏。这个脚本会把真钱打出去，每条前置条件都必须真的拦得住。

最要紧的一条是「核心在跑时不许下单」：核心按自己推演的持仓与券商快照对账，
一笔它不知道的手工持仓会判不一致并触发停机。
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from qsys.live.config import FeeConfig, SafetyConfig, StreamConfig
from qsys.live.transport import MemoryTransport
from scripts import commissioning_order as cli


@dataclass
class FakeConfig:
    safety: SafetyConfig = field(default_factory=lambda: SafetyConfig(
        mode="live", orders_enabled=True, protected_account_ids=("803010036",),
    ))
    streams: StreamConfig = field(default_factory=StreamConfig)
    fees: FeeConfig = field(default_factory=lambda: FeeConfig(
        commission_rate=0.000075, min_commission=5.0,
        stamp_duty_rate=0.0005, transfer_fee_rate=0.00001,
    ))


@dataclass
class Args:
    qty: int = 100
    limit: float = 4.0


def _transport(*, core_age=None, agent_age=1.0, killed=False):
    t = MemoryTransport()
    s = StreamConfig()
    if agent_age is not None:
        t.set_key(f"{s.heartbeat}:agent", json.dumps(
            {"at": (datetime.now() - timedelta(seconds=agent_age)).isoformat()}))
    if core_age is not None:
        t.set_key(f"{s.heartbeat}:core", json.dumps(
            {"at": (datetime.now() - timedelta(seconds=core_age)).isoformat()}))
    if killed:
        t.set_key("control:kill", "停机中")
    return t


def test_a_running_live_core_blocks_the_order():
    """核心在跑就不许插手——手工持仓会让它的对账失败并停机。"""
    refusal = cli._preflight(FakeConfig(), _transport(core_age=1.0), Args())

    assert refusal is not None and "核心" in refusal and "对账" in refusal


def test_a_stopped_core_is_fine():
    assert cli._preflight(FakeConfig(), _transport(core_age=120.0), Args()) is None
    assert cli._preflight(FakeConfig(), _transport(), Args()) is None


@pytest.mark.parametrize("safety,expect", [
    (SafetyConfig(mode="shadow", orders_enabled=False), "写保护"),
    (SafetyConfig(mode="live", orders_enabled=False), "写保护"),
    (SafetyConfig(mode="live", orders_enabled=True), "白名单"),
])
def test_the_three_write_protections_are_all_required(safety, expect):
    config = FakeConfig(safety=safety)

    refusal = cli._preflight(config, _transport(), Args())

    assert refusal is not None and expect in refusal


def test_size_is_capped_by_shares_and_by_money():
    over_qty = cli._preflight(FakeConfig(), _transport(), Args(qty=cli.MAX_QTY + 100))
    over_cash = cli._preflight(FakeConfig(), _transport(), Args(qty=100, limit=100.0))

    assert over_qty is not None and "上限" in over_qty
    assert over_cash is not None and "金额" in over_cash


def test_a_stale_or_missing_agent_blocks_the_order():
    stale = cli._preflight(FakeConfig(), _transport(agent_age=60.0), Args())
    missing = cli._preflight(FakeConfig(), _transport(agent_age=None), Args())

    assert stale is not None and "心跳" in stale
    assert missing is not None and "代理" in missing


def test_an_engaged_kill_switch_blocks_the_order():
    refusal = cli._preflight(FakeConfig(), _transport(killed=True), Args())

    assert refusal is not None and "停机开关" in refusal


def test_the_fee_estimate_matches_the_account_tariff():
    """万 0.75、不免五：5,000 元的单佣金按最低 5 元计，卖出还要加印花税。"""
    config = FakeConfig()

    buy = cli._expected_fee(config, "buy", 1000, 5.0)
    sell = cli._expected_fee(config, "sell", 1000, 5.0)

    assert buy == pytest.approx(5.0 + 5000 * 0.00001)          # 佣金取最低 5 元
    assert sell == pytest.approx(buy + 5000 * 0.0005)          # 卖出多一道印花税
