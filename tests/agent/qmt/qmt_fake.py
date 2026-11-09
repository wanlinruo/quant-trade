"""QMT 内置交易 API 的测试替身（#601）。不导入任何 QMT/xtquant 模块。"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[3] / 'src' / 'qsys' / 'agent' / 'qmt' / 'executor_source.py'
ACCOUNT = '100000001'


def load_executor(raw=False, **literals):
    """Load the executor source; unless ``raw``, tests default to DRY_RUN=False so
    native calls are observable (the checked-in default is DRY_RUN=True)."""
    if not raw:
        literals.setdefault('DRY_RUN', False)
    spec = importlib.util.spec_from_file_location('qmt_executor_under_test', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, value in literals.items():
        setattr(module, name, value)
    return module


class FakeQmtApi:
    """记录 passorder/cancel；下单后按需在 ORDER 中生成同备注委托。"""

    def __init__(self, *, auto_order=True):
        self.calls, self.auto_order = [], auto_order
        self.orders, self.deals, self.positions, self.accounts = [], [], [], []
        self._sys = 0

    def order(self, remark, status=50, volume=100, traded=0, code='510300', exchange='SH',
              offset=48, account=ACCOUNT, price=4.0, date='20261109'):
        self._sys += 1
        row = SimpleNamespace(m_strRemark=remark, m_strOrderSysID=f'S{self._sys}',
                              m_nOrderStatus=status, m_nVolumeTotalOriginal=volume,
                              m_nVolumeTraded=traded, m_strInstrumentID=code,
                              m_strExchangeID=exchange, m_nOffsetFlag=offset,
                              m_strAccountID=account, m_dLimitPrice=price,
                              m_strInsertDate=date)
        self.orders.append(row)
        return row

    def passorder(self, op, order_type, account, code, pr_type, price, volume, strategy,
                  quick, remark, C):
        self.calls.append(('passorder', op, order_type, account, code, pr_type, price, volume,
                           strategy, quick, remark))
        if self.auto_order:
            symbol, exchange = code.split('.')
            self.order(remark, volume=volume, code=symbol, exchange=exchange,
                       offset=48 if op == 23 else 49)

    def cancel(self, order_sys_id, account, kind, C):
        self.calls.append(('cancel', order_sys_id, account, kind))
        return True

    def get_trade_detail_data(self, account, kind, datatype):
        if datatype in getattr(self, 'unavailable', ()):
            return None
        return {'ORDER': self.orders, 'DEAL': self.deals, 'POSITION': self.positions,
                'ACCOUNT': self.accounts}[datatype]

    def as_api(self):
        return dict(passorder=self.passorder, cancel=self.cancel,
                    get_trade_detail_data=self.get_trade_detail_data)
