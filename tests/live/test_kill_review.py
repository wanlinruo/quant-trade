"""停机闸解除前核对（#662 计划 05）：结构化判定与只读采集。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from qsys.core.portfolio import Portfolio, Position
from qsys.core.types import Symbol
from qsys.live.kill_review import ITEM_IDS, SHANGHAI, ReviewInputs, review
from qsys.live.messages import AccountSnapshot, Heartbeat, PositionSnapshot

NOW = datetime(2026, 11, 18, 17, 0, tzinfo=SHANGHAI)


def beat(source, age=1.0, **kw):
    return Heartbeat(source=source, at=NOW - timedelta(seconds=age), expected_interval_s=1.0, **kw)


def agent(age=1.0, **over):
    kw = dict(trading_mode="live", orders_enabled=True, account_protected=True, local_kill_engaged=True) | over
    return beat("agent", age, **kw)


def inputs(**over) -> ReviewInputs:
    pf = Portfolio(100_000.0)
    pf.cash = 90_000.0
    pf.positions[Symbol.parse("600000.SH")] = Position(Symbol.parse("600000.SH"), 100, 100, 1000.0)
    account = AccountSnapshot(at=NOW.replace(tzinfo=None) - timedelta(seconds=1), cash=90_000.0,
                              positions={"600000.SH": PositionSnapshot(total_qty=100, available_qty=100, avg_cost=10.0)},
                              active_order_count=0)
    base = dict(local_kill_reason="heartbeat timeout: agent", redis_kill_reason="heartbeat timeout: agent",
                redis_ok=True, agent=agent(), market=beat("market"), account=account, portfolio=pf,
                own_pending=0, state_error=None)
    return ReviewInputs(**(base | over))


def items(r):
    return {i.id: i for i in r.items}


def test_item_ids_stable_and_ordered():
    r = review(inputs(), confirmed_by="维护者", now=NOW)
    assert [i.id for i in r.items] == list(ITEM_IDS) == [
        "gates.state", "trigger.cleared", "link.agent_heartbeat", "link.market_heartbeat", "link.agent_posture",
        "account.snapshot", "reconcile.positions_cash", "orders.own_pending", "orders.broker_vs_own",
        "maintainer.confirmation"]
    assert r.schema_version == 1 and r.target == "live" and r.reviewed_at == NOW


def test_all_clear_with_confirmation_is_ready():
    r = review(inputs(), confirmed_by="维护者", now=NOW)
    assert r.overall == "ready" and r.engaged is True
    assert all(i.state in ("pass", "info") for i in r.items), [(i.id, i.state) for i in r.items]


def test_without_confirmation_needs_confirmation():
    r = review(inputs(), confirmed_by=None, now=NOW)
    assert r.overall == "needs_confirmation" and items(r)["maintainer.confirmation"].state == "confirm"


def test_gates_state_reports_three_gates():
    g = items(review(inputs(redis_kill_reason=None, agent=agent(local_kill_engaged=None)), confirmed_by="x", now=NOW))
    assert g["gates.state"].state == "info"
    assert g["gates.state"].observed == {"linux_local": True, "redis": False, "windows_gateway": None}
    off = review(inputs(local_kill_reason=None, redis_kill_reason=None, agent=agent(local_kill_engaged=False)),
                 confirmed_by="x", now=NOW)
    assert off.engaged is False


def test_heartbeat_timeout_reason_cleared_by_fresh_heartbeats():
    assert items(review(inputs(), confirmed_by="x", now=NOW))["trigger.cleared"].state == "pass"


def test_heartbeat_timeout_reason_not_cleared_when_stale():
    r = review(inputs(agent=agent(age=120)), confirmed_by="x", now=NOW)
    assert items(r)["trigger.cleared"].state == "fail" and items(r)["link.agent_heartbeat"].state == "fail"
    assert r.overall == "blocked"


def test_reconcile_reason_follows_reconcile_item():
    reason = "实盘账户对账失败：600000.SH 本地 100 远端 200"
    ok = review(inputs(local_kill_reason=reason, redis_kill_reason=reason), confirmed_by="x", now=NOW)
    assert items(ok)["trigger.cleared"].state == "pass"
    bad_account = inputs().account.model_copy(update={"positions": {"600000.SH": PositionSnapshot(total_qty=200, available_qty=200, avg_cost=10.0)}})
    bad = review(inputs(local_kill_reason=reason, redis_kill_reason=reason, account=bad_account), confirmed_by="x", now=NOW)
    assert items(bad)["reconcile.positions_cash"].state == "fail" and items(bad)["trigger.cleared"].state == "fail"


def test_fuse_reason_requires_confirmation():
    for reason in ("连续拒单达到熔断阈值: 3", "core fuse: 3 consecutive tick errors", "manual", ""):
        r = review(inputs(local_kill_reason=reason, redis_kill_reason=reason), confirmed_by="维护者", now=NOW)
        assert items(r)["trigger.cleared"].state == "confirm", reason
        assert r.overall == "needs_confirmation"


def test_broker_vs_own_orders():
    def state(broker, own):
        acc = inputs().account.model_copy(update={"active_order_count": broker})
        return items(review(inputs(account=acc, own_pending=own), confirmed_by="x", now=NOW))["orders.broker_vs_own"]
    assert state(0, 0).state == "pass"
    more = state(1, 0)
    assert more.state == "confirm" and "多 1" in more.detail and "#666" in more.detail
    assert state(0, 1).state == "fail"
    assert state(None, 0).state == "unknown" and state(0, None).state == "unknown"
    own = items(review(inputs(own_pending=2, account=inputs().account.model_copy(update={"active_order_count": 2})),
                       confirmed_by="x", now=NOW))["orders.own_pending"]
    assert own.state == "confirm" and own.observed == {"own_pending": 2}


def test_missing_state_db_is_unknown():
    r = review(inputs(portfolio=None, own_pending=None, state_error="LookupError"), confirmed_by="x", now=NOW)
    i = items(r)
    assert i["reconcile.positions_cash"].state == "unknown" and i["orders.own_pending"].state == "unknown"
    assert "LookupError" in i["reconcile.positions_cash"].detail and r.overall == "blocked"


def test_redis_unavailable_is_blocked_not_crash():
    r = review(inputs(redis_ok=False, redis_kill_reason=None, agent=None, market=None, account=None),
               confirmed_by="x", now=NOW)
    i = items(r)
    assert i["gates.state"].observed["redis"] is None and i["gates.state"].observed["windows_gateway"] is None
    for key in ("link.agent_heartbeat", "link.market_heartbeat", "link.agent_posture", "account.snapshot",
                "reconcile.positions_cash", "orders.broker_vs_own"):
        assert i[key].state == "unknown", key
    assert r.overall == "blocked"


def test_reviewed_at_must_be_aware():
    with pytest.raises(ValueError):
        review(inputs(), confirmed_by="x", now=NOW.replace(tzinfo=None))
