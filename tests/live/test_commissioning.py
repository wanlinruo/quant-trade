from datetime import datetime, timedelta

import pytest

from qsys.live.commissioning import (
    COMMISSIONING_KEY,
    CommissioningLease,
    commissioning_qty_allowed,
    is_commissioning_order_id,
    read_commissioning_lease,
)
from qsys.live.transport import MemoryTransport

NOW = datetime(2026, 10, 12, 10)


def test_a_valid_lease_is_short_lived_and_parseable():
    transport = MemoryTransport()
    lease = CommissioningLease.arm(now=NOW)
    transport.set_key(COMMISSIONING_KEY, lease.model_dump_json())
    assert read_commissioning_lease(transport, now=NOW) == lease
    assert read_commissioning_lease(
        transport, now=NOW + timedelta(minutes=11),
    ) is None


def test_a_long_or_malformed_lease_fails_closed():
    with pytest.raises(ValueError):
        CommissioningLease(
            armed_at=NOW, expires_at=NOW + timedelta(minutes=11),
        )
    transport = MemoryTransport()
    transport.set_key(COMMISSIONING_KEY, "not json")
    assert read_commissioning_lease(transport, now=NOW) is None


def test_commissioning_order_shape_allows_only_one_lot_buy_or_reducing_sell():
    assert is_commissioning_order_id("commission-20261012-100000-abcdef")
    assert not is_commissioning_order_id("commission-manual")
    assert commissioning_qty_allowed("buy", 100)
    assert not commissioning_qty_allowed("buy", 50)
    assert commissioning_qty_allowed("sell", 50)
    assert not commissioning_qty_allowed("sell", 101)
