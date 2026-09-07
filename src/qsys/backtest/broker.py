"""Broker 抽象。

回测与实盘不是两个系统，是这个接口的两种实现——差异只可能来自这一个
边界，因而可以逐笔归因。

on_market_data 是分界点：SimBroker 在此撮合；实盘 Broker 忽略它，
因为真实市场自己会撮合。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from qsys.core.events import MarketSnapshot, Reject
from qsys.core.trading import Fill, Order


class Broker(ABC):
    @abstractmethod
    def submit(self, order: Order) -> None:
        """提交订单。订单进入未成交队列，等待下一次撮合机会。"""

    @abstractmethod
    def on_market_data(self, snapshot: MarketSnapshot) -> None:
        """市场数据到达。SimBroker 在此撮合；实盘 Broker 忽略。"""

    @abstractmethod
    def poll_fills(self) -> tuple[Fill, ...]:
        """取走自上次调用以来的成交回报。"""

    @abstractmethod
    def poll_rejects(self) -> tuple[Reject, ...]:
        """取走自上次调用以来的拒单记录。"""

    @abstractmethod
    def pending(self) -> tuple[Order, ...]:
        """当前未成交订单。"""

    @abstractmethod
    def cancel_all(self) -> None:
        """撤销全部未成交订单。"""
