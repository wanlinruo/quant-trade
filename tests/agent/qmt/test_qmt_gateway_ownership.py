"""#601 F5：网关只转发本账本委托的回报；同号异内容冲突隔离后不阻塞卖出/撤单。"""
from datetime import datetime
import json

import pytest

from qsys.agent.adapter import FakeAdapter
from qsys.agent.dispatch_store import DispatchConflictError
from qsys.agent.execution_agent import ExecutionAgent
from qsys.agent.fill_tracker import BrokerOrder, BrokerTrade
from qsys.live.config import StreamConfig
from qsys.live.messages import FillEvent
from qsys.live.safety import SafetyPolicy, TradingMode
from qsys.live.transport import MemoryTransport
from test_qmt_reconcile import cmd

S = StreamConfig()
OWN, PROBE = 'q261111001aaaaaaaaaaaa', 'q261111001bbbbbbbbbbbb'


class Dispatcher:
    def __init__(self, owned=(), conflict=False):
        self.owned, self.conflict = set(owned), conflict

    def matches(self, transport, streams):
        return True

    def owns(self, cid):
        return cid in self.owned

    def replay_existing(self, command):
        if self.conflict:
            raise DispatchConflictError('immutable dispatch intent differs')
        return True


def trade(cid, tid):
    return BrokerTrade(traded_id=tid, client_order_id=cid, broker_order_id='b' + tid,
                       symbol='510300.SH', side='sell', price=4.0, qty=100,
                       at=datetime(2026, 11, 11, 10, 0))


def order(cid):
    return BrokerOrder(broker_order_id='b' + cid[-4:], client_order_id=cid, symbol='510300.SH',
                       side='sell', ordered_qty=100, traded_qty=100, status=56, status_msg='')


def test_reports_of_orders_outside_the_dispatch_ledger_are_not_forwarded():
    t, adapter = MemoryTransport(), FakeAdapter()
    adapter.trades = [trade(OWN, 't1'), trade(PROBE, 't2')]
    adapter.orders = [order(OWN), order(PROBE)]
    agent = ExecutionAgent(t, adapter, S, async_dispatcher=Dispatcher(owned={OWN}))
    agent.poll_reports()
    fills = [FillEvent.from_payload(m.payload) for m in t.all_messages(S.fills)]
    assert [f.client_order_id for f in fills] == [OWN]
    statuses = [m.payload for m in t.all_messages(S.order_status)]
    assert statuses and all(OWN in json.dumps(p) and PROBE not in json.dumps(p) for p in statuses)
    agent.poll_reports()                          # foreign orders stay silent on later polls
    assert len(t.all_messages(S.fills)) == 1


def safety(tmp_path):
    return SafetyPolicy(TradingMode.LIVE, account_id='a', protected_account_ids=('a',),
                        orders_enabled=True, local_kill_path=tmp_path / 'AGENT_KILL')


def test_conflict_is_quarantined_acked_and_kill_engaged(tmp_path):
    t = MemoryTransport()
    quarantine = tmp_path / 'dispatch-conflicts.jsonl'
    agent = ExecutionAgent(t, FakeAdapter(), S, async_dispatcher=Dispatcher(conflict=True),
                           safety=safety(tmp_path), conflict_quarantine=quarantine)
    msg_id = t.append(S.orders, cmd().to_payload())
    agent.run_once()                              # no crash loop under the supervisor
    assert 'conflict' in (tmp_path / 'AGENT_KILL').read_text()
    (record,) = [json.loads(line) for line in quarantine.read_text().splitlines()]
    assert record['message_id'] == msg_id and record['client_order_id'] == cmd().client_order_id
    assert record['payload'] == cmd().to_payload()
    assert t.pending(S.orders, 'agent') == []
    agent.run_once()                              # nothing left to re-trigger
    assert len(quarantine.read_text().splitlines()) == 1


def test_conflict_without_quarantine_keeps_fail_fast_behaviour(tmp_path):
    t = MemoryTransport()
    agent = ExecutionAgent(t, FakeAdapter(), S, async_dispatcher=Dispatcher(conflict=True),
                           safety=safety(tmp_path))
    t.append(S.orders, cmd().to_payload())
    with pytest.raises(DispatchConflictError):
        agent.run_once()
