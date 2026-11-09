import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

from qsys.live.messages import OrderCommand, RejectEvent
from qsys.live.transport import MemoryTransport
from qsys.live.validation_transport import ValidationTransport

ROOT = Path(__file__).resolve().parents[2]
NS = 'component-validation:qmt-f2'


def tool():
    spec = importlib.util.spec_from_file_location('send_intents', ROOT / 'scripts' / 'send_qmt_validation_intents.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scenarios_publish_fixed_commands_only_to_validation_namespace():
    inner = MemoryTransport()
    t = ValidationTransport(inner, NS)
    sent = tool().submit(t, scenarios=['accept', 'board_reject', 'market_sell'], strategy_id='structural',
                         now=datetime(2026, 11, 9, 15, 0))
    commands = [OrderCommand.from_payload(m.payload) for m in inner.all_messages(NS + ':cmd:orders')]
    assert [c.client_order_id for c in commands] == [s['client_order_id'] for s in sent]
    assert [(c.symbol, c.side, c.order_type) for c in commands] == [
        ('510300.SH', 'buy', 'limit'), ('688001.SH', 'buy', 'limit'), ('510300.SH', 'sell', 'market')]
    assert all(len(c.client_order_id) == 22 and c.schema_version == 2 for c in commands)
    assert inner.all_messages('cmd:orders') == []


def test_refuses_non_validation_transport():
    with pytest.raises(ValueError):
        tool().submit(MemoryTransport(), scenarios=['accept'], strategy_id='s', now=datetime(2026, 11, 9))


def test_collect_reports_outcomes_by_client_order_id():
    inner = MemoryTransport()
    t = ValidationTransport(inner, NS)
    mod = tool()
    sent = mod.submit(t, scenarios=['board_reject'], strategy_id='structural', now=datetime(2026, 11, 9, 15, 0))
    cid = sent[0]['client_order_id']
    t.append('evt:rejects', RejectEvent(client_order_id=cid, symbol='688001.SH', side='buy', unfilled_qty=100,
                                        reason='executor:market_blocked', at=datetime(2026, 11, 9, 15, 0)).to_payload())
    assert mod.collect(t, [cid])[cid]['rejects'] == ['executor:market_blocked']
