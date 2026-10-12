"""Bounded maintenance lease for the first real-order commissioning session.

The execution agent normally fails closed when the live core heartbeat disappears.
The first round-trip order is deliberately sent while that core is stopped, so the
exception must be explicit, short-lived and narrower than normal live trading.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re
import secrets

from pydantic import BaseModel, model_validator


COMMISSIONING_KEY = "control:commissioning"
COMMISSIONING_SYMBOL = "511380.SH"
COMMISSIONING_QTY = 100
COMMISSIONING_MAX_NOTIONAL = 3_000.0
MAX_LEASE_SECONDS = 10 * 60
# 2026-10-12 生产实测：miniQMT 只保留 order_remark 的前 23 个字符。
# client_order_id 必须完整落在这个边界内，否则回报过滤、幂等查单和成交关联
# 会同时失效。普通策略 ID 是 22 字符；验证单也把上限作为显式契约。
QMT_ORDER_REMARK_MAX_LENGTH = 23
_COMMISSIONING_ORDER_ID = re.compile(
    r"c\d{8}-\d{6}-[0-9a-f]{6}"
)


def is_commissioning_order_id(value: str) -> bool:
    return bool(_COMMISSIONING_ORDER_ID.fullmatch(value))


def new_commissioning_order_id(now: datetime | None = None) -> str:
    """Build a QMT-safe, unique ID that survives ``order_remark`` round trips."""
    now = now or datetime.now()
    value = f"c{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    if len(value) != QMT_ORDER_REMARK_MAX_LENGTH:  # pragma: no cover - invariant
        raise AssertionError("commissioning order ID exceeds QMT order_remark")
    return value


def commissioning_qty_allowed(side: str, qty: int) -> bool:
    """A buy opens exactly one lot; a sell may flatten a partial-fill remainder."""
    return qty == COMMISSIONING_QTY if side == "buy" else 0 < qty <= COMMISSIONING_QTY


class CommissioningLease(BaseModel):
    armed_at: datetime
    expires_at: datetime
    symbol: str = COMMISSIONING_SYMBOL
    max_qty: int = COMMISSIONING_QTY
    max_notional: float = COMMISSIONING_MAX_NOTIONAL

    @model_validator(mode="after")
    def _check(self) -> "CommissioningLease":
        duration = (self.expires_at - self.armed_at).total_seconds()
        if duration <= 0 or duration > MAX_LEASE_SECONDS:
            raise ValueError("commissioning lease must last 1..600 seconds")
        if self.symbol != COMMISSIONING_SYMBOL:
            raise ValueError(f"commissioning symbol must be {COMMISSIONING_SYMBOL}")
        if (
            self.max_qty != COMMISSIONING_QTY
            or self.max_notional != COMMISSIONING_MAX_NOTIONAL
        ):
            raise ValueError("commissioning size limits are fixed")
        return self

    def active(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(tz=self.expires_at.tzinfo)
        return self.armed_at <= now <= self.expires_at

    @classmethod
    def arm(cls, *, now: datetime | None = None, seconds: int = MAX_LEASE_SECONDS):
        now = now or datetime.now()
        return cls(armed_at=now, expires_at=now + timedelta(seconds=seconds))


def read_commissioning_lease(transport, *, now: datetime | None = None):
    """Malformed and expired leases are inactive; callers must fail closed."""
    raw = transport.get_key(COMMISSIONING_KEY)
    if raw is None:
        return None
    try:
        lease = CommissioningLease.model_validate_json(raw)
    except Exception:
        return None
    return lease if lease.active(now) else None
