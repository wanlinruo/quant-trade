"""跨机消息协议。

刻意不依赖 qsys.core —— Windows 侧执行代理只需要这个模块和 redis-py，
依赖越少越不容易在 Windows 上出问题（spec §11）。

Redis Stream 的字段值必须是字符串，故整条消息序列化为单个 JSON payload。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, model_validator


class Message(BaseModel):
    def to_payload(self) -> dict[str, str]:
        return {"type": type(self).__name__, "data": self.model_dump_json()}

    @classmethod
    def from_payload(cls, payload: dict[str, str]) -> Self:
        tag = payload.get("type")
        if tag != cls.__name__:
            raise ValueError(f"expected {cls.__name__}, got {tag!r}")
        return cls.model_validate_json(payload["data"])


class OrderCommand(Message):
    client_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    order_type: Literal["market", "limit"]
    limit_price: float | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.qty <= 0:
            raise ValueError(f"qty must be positive: {self.qty}")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit order requires limit_price")
        return self


class FillEvent(Message):
    client_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    price: float
    fee: float
    filled_at: datetime
    broker_order_id: str | None = None


class RejectEvent(Message):
    client_order_id: str
    symbol: str
    reason: str
    at: datetime


class PositionSnapshot(BaseModel):
    total_qty: int
    available_qty: int
    avg_cost: float


class AccountSnapshot(Message):
    at: datetime
    cash: float
    positions: dict[str, PositionSnapshot]


class ControlCommand(Message):
    action: Literal["kill", "resume", "cancel_all", "query"]
    at: datetime
    reason: str = ""


class Heartbeat(Message):
    source: str
    at: datetime


class QuoteRequest(Message):
    """Core-side request for a fresh, read-only QMT market snapshot."""

    request_id: str
    symbols: list[str]
    requested_at: datetime

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.request_id:
            raise ValueError("request_id cannot be empty")
        if not self.symbols:
            raise ValueError("symbols cannot be empty")
        return self


class MarketQuote(BaseModel):
    symbol: str
    at: datetime
    price: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: int = 0  # shares, cumulative for the session
    amount: float = 0.0
    upper_limit: float | None = None
    lower_limit: float | None = None
    suspended: bool = False

    @model_validator(mode="after")
    def _valid_price(self) -> Self:
        if self.price < 0 or self.volume < 0:
            raise ValueError("quote price and volume must be non-negative")
        return self


class MarketIndexQuote(BaseModel):
    symbol: str
    name: str
    price: float
    change_pct: float
    amount: float = 0.0


class MarketOverviewSnapshot(Message):
    """Atomic market-wide snapshot published by the read-only QMT gateway."""

    session: date
    published_at: datetime
    indices: list[MarketIndexQuote]
    valid_stock_count: int
    advance_count: int
    decline_count: int
    flat_count: int
    limit_up_count: int
    limit_down_count: int
    touched_limit_up_count: int
    broken_board_count: int
    broken_board_rate: float
    market_advance_ratio: float
    market_median_return_1d: float
    market_total_money: float


class MarketSchedule(Message):
    checked_at: datetime
    is_trading_day: bool
    is_open: bool
    next_open_at: datetime | None = None


class QuoteSnapshot(Message):
    request_id: str
    published_at: datetime
    quotes: dict[str, MarketQuote]
    error: str = ""
