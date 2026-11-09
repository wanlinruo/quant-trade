"""报告环 → 券商回报/账户快照；``ExecutionAgent`` 的大 QMT 券商端（#601）。

下单只走异步端口（``place_order`` 拒绝）。委托、成交来自执行器回调与查询行，
按券商成交号去重；只处理本系统备注形状的委托。账户号从不进入报告。
"""
from __future__ import annotations

from datetime import datetime

import structlog

from qsys.agent.adapter import BrokerAccount
from qsys.agent.fill_tracker import BrokerOrder, BrokerTrade
from qsys.agent.qmt.memory import SharedMemory
from qsys.agent.qmt.port import QmtIpcPort
from qsys.live.messages import PositionSnapshot

log = structlog.get_logger(__name__)
# QMT 股票委托/成交的开平标志：48 买入、49 卖出（F0 现场再核对）。
_SIDES = {48: 'buy', 49: 'sell'}


def _symbol(row: dict) -> str | None:
    code, exchange = row.get('m_strInstrumentID'), row.get('m_strExchangeID')
    return f'{code}.{exchange}' if code and exchange else None


class QmtIpcAdapter:
    def __init__(self, memory: SharedMemory, port: QmtIpcPort) -> None:
        self._memory, self._port = memory, port
        self._orders: dict[str, BrokerOrder] = {}
        self._trades: dict[str, BrokerTrade] = {}
        self._positions: dict[str, PositionSnapshot] = {}
        self._account: dict | None = None
        self.epochs: set[str] = set()
        self.last_snapshot: dict = {}

    # -- report ingestion -------------------------------------------------
    def ingest(self) -> int:
        rows = self._memory.read_reports()
        for row in rows:
            if row.get('epoch'):
                self.epochs.add(row['epoch'])
            kind = row.get('kind')
            try:
                if kind == 'order':
                    self._order(row)
                elif kind == 'deal':
                    self._deal(row)
                elif kind == 'position':
                    self._position(row)
                elif kind == 'account':
                    self._account = row
                elif kind == 'order_error':
                    log.warning('qmt.order_error', remark=row.get('m_strRemark'),
                                message=row.get('message'))
            except (KeyError, TypeError, ValueError) as exc:
                log.error('qmt.report_unparsed', kind=kind, error=type(exc).__name__)
        return len(rows)

    def _order(self, row: dict) -> None:
        side, symbol = _SIDES.get(row.get('m_nOffsetFlag')), _symbol(row)
        if side is None or symbol is None or not isinstance(row.get('m_nOrderStatus'), int):
            raise ValueError('order row shape')
        msg = row.get('m_strErrorMsg') or row.get('m_strCancelInfo') or ''
        self._orders[row['m_strRemark']] = BrokerOrder(
            broker_order_id=str(row.get('m_strOrderSysID') or ''),
            client_order_id=row['m_strRemark'], symbol=symbol, side=side,
            ordered_qty=int(row['m_nVolumeTotalOriginal']), traded_qty=int(row['m_nVolumeTraded']),
            status=row['m_nOrderStatus'], status_msg=msg)

    def _deal(self, row: dict) -> None:
        trade_id = row.get('m_strTradeID')
        side, symbol = _SIDES.get(row.get('m_nOffsetFlag')), _symbol(row)
        if not trade_id or side is None or symbol is None:
            raise ValueError('deal row shape')
        if trade_id in self._trades:
            return
        self._trades[trade_id] = BrokerTrade(
            traded_id=trade_id, client_order_id=row['m_strRemark'],
            broker_order_id=str(row.get('m_strOrderSysID') or ''), symbol=symbol, side=side,
            price=float(row['m_dPrice']), qty=int(row['m_nVolume']),
            at=datetime.strptime(row['m_strTradeDate'] + row['m_strTradeTime'], '%Y%m%d%H%M%S'))

    def _position(self, row: dict) -> None:
        symbol = _symbol(row)
        if symbol is None:
            raise ValueError('position row shape')
        self._positions[symbol] = PositionSnapshot(
            total_qty=int(row['m_nVolume']), available_qty=int(row['m_nCanUseVolume']),
            avg_cost=float(row.get('m_dOpenPrice') or 0.0),
            market_value=row.get('m_dMarketValue'))

    # -- BrokerAdapter ------------------------------------------------------
    def place_order(self, symbol, side, qty, order_type, limit_price, client_order_id=''):
        raise RuntimeError('async_only: orders go through QmtIpcPort')

    def cancel_all(self) -> None:
        self._port.request('cancel_all')

    def query_trades(self) -> list[BrokerTrade]:
        self.ingest()
        return list(self._trades.values())

    def query_orders(self) -> list[BrokerOrder]:
        self.ingest()
        return list(self._orders.values())

    def query_account_details(self) -> BrokerAccount:
        self.ingest()
        self._positions, self._account = {}, None
        receipt = self._port.request('snapshot')
        self.ingest()
        result = receipt.get('result') or {}
        self.last_snapshot = dict(result) if receipt.get('status') == 'accepted' else {}
        account = self._account
        if receipt.get('status') != 'accepted' or account is None or 'active_orders' not in result:
            raise RuntimeError('executor account snapshot unavailable')
        return BrokerAccount(
            cash=float(account['m_dAvailable']), positions=dict(self._positions),
            frozen_cash=float(account.get('m_dFrozenCash') or 0.0),
            market_value=float(account.get('m_dStockValue') or 0.0),
            total_asset=account.get('m_dBalance'),
            active_order_count=int(result['active_orders']))

    def query_account(self):
        details = self.query_account_details()
        return details.cash, details.positions

    def query_active_order_count(self) -> int:
        count = self.query_account_details().active_order_count
        if count is None:
            raise RuntimeError('active order count unavailable')
        return count

    def find_order(self, client_order_id: str) -> str | None:
        self.ingest()
        order = self._orders.get(client_order_id)
        return order.broker_order_id if order is not None else None
