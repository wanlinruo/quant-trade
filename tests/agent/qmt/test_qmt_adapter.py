from datetime import datetime
from types import SimpleNamespace

import pytest

from qsys.agent.fill_tracker import FillTracker
from qsys.agent.qmt import protocol
from qsys.agent.qmt.adapter import QmtIpcAdapter
from qsys.agent.qmt.memory import SharedMemory
from qsys.agent.qmt.port import QmtIpcPort
from qsys.backtest.fees import AShareFeeModel
from qmt_fake import ACCOUNT, FakeQmtApi, load_executor

RUN = 'a' * 32
KEY = bytes(range(32))
NOW = 1_762_653_600_000
CID = 'q261109001abcdef012345'


def rig():
    buf = bytearray(protocol.MAP_SIZE)
    mem = SharedMemory(buf)
    mem.beat(NOW)
    api = FakeQmtApi()
    ex = load_executor(ACCOUNT_ID=ACCOUNT).Executor(RUN, KEY, api.as_api(), buf, lambda: NOW)
    port = QmtIpcPort(mem, run_id=RUN, key=KEY, clock_ms=lambda: NOW,
                      sleep=lambda _: ex.tick(None), receipt_timeout_s=0.5)
    return QmtIpcAdapter(mem, port), ex, api


def deal(trade_id, remark=CID, volume=100, price=4.157, offset=48):
    return SimpleNamespace(m_strRemark=remark, m_strTradeID=trade_id, m_strOrderSysID='S1',
                           m_dPrice=price, m_nVolume=volume, m_nOffsetFlag=offset,
                           m_strInstrumentID='510300', m_strExchangeID='SH',
                           m_strTradeDate='20261109', m_strTradeTime='100129',
                           m_strAccountID=ACCOUNT)


def test_callback_and_query_same_trade_counted_once():
    adapter, ex, api = rig()
    ex.record_order('callback', api.order(CID, status=56, traded=100))
    ex.record_deal('callback', deal('T1'))
    ex.record_deal('query', deal('T1'))
    trades = adapter.query_trades()
    assert [(t.traded_id, t.client_order_id, t.side, t.qty, t.symbol) for t in trades] == [
        ('T1', CID, 'buy', 100, '510300.SH')]
    assert trades[0].at == datetime(2026, 11, 9, 10, 1, 29)
    assert [(o.client_order_id, o.status, o.side) for o in adapter.query_orders()] == [(CID, 56, 'buy')]


def test_partial_fill_then_cancel_maps_52_53():
    adapter, ex, api = rig()
    ex.record_order('callback', api.order(CID, status=55, traded=40))
    ex.record_deal('callback', deal('T1', volume=40))
    ex.record_order('callback', SimpleNamespace(**{**vars(api.orders[0]), 'm_nOrderStatus': 53}))
    out = FillTracker(AShareFeeModel()).observe(
        adapter.query_trades(), adapter.query_orders(), seen_trade_ids=frozenset(),
        closed_order_ids=frozenset(), now=datetime(2026, 11, 9, 10, 2))
    assert [f.qty for f in out.fills] == [40]
    assert [(s.status, s.filled_qty, s.remaining_qty) for s in out.statuses] == [('cancelled', 40, 60)]
    assert [r.unfilled_qty for r in out.rejects] == [60]


def test_foreign_remark_ignored():
    adapter, ex, api = rig()
    ex.record_order('callback', api.order('manual'))
    assert adapter.query_orders() == []


def test_place_order_is_async_only():
    adapter, _, _ = rig()
    with pytest.raises(RuntimeError, match='async_only'):
        adapter.place_order('510300.SH', 'buy', 100, 'limit', 4.0, CID)


def test_account_snapshot_from_executor_without_account_id():
    adapter, ex, api = rig()
    api.accounts.append(SimpleNamespace(m_strAccountID=ACCOUNT, m_dAvailable=95_000.0,
                                        m_dBalance=100_023.38, m_dStockValue=415.7,
                                        m_dFrozenCash=0.0))
    api.positions.append(SimpleNamespace(m_strInstrumentID='510300', m_strExchangeID='SH',
                                         m_nVolume=100, m_nCanUseVolume=0, m_dOpenPrice=4.157,
                                         m_dMarketValue=415.7, m_strAccountID=ACCOUNT))
    api.order(CID, status=50)
    details = adapter.query_account_details()
    assert details.cash == 95_000.0 and details.total_asset == 100_023.38
    assert details.positions['510300.SH'].total_qty == 100
    assert details.positions['510300.SH'].available_qty == 0
    assert details.active_order_count == 1
    assert adapter.find_order(CID) == 'S1'


def test_cancel_all_goes_through_executor():
    adapter, ex, api = rig()
    api.order(CID, status=50)
    adapter.cancel_all()
    assert [c[0] for c in api.calls] == ['cancel']
