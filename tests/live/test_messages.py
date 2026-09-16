from datetime import datetime

import pytest

from qsys.live.messages import (
    AccountSnapshot,
    ControlCommand,
    FillEvent,
    Heartbeat,
    OrderCommand,
    PositionSnapshot,
    OrderStatusEvent,
    RejectEvent,
)

NOW = datetime(2024, 1, 3, 9, 31, 12)


def order() -> OrderCommand:
    return OrderCommand(
        client_order_id="c-1", symbol="600036.SH", side="buy", qty=100,
        order_type="market", limit_price=None, created_at=NOW,
    )


def test_order_round_trips_through_payload():
    restored = OrderCommand.from_payload(order().to_payload())
    assert restored == order()


def test_payload_values_are_all_strings():
    """Redis Stream 的字段值必须是字符串。"""
    payload = order().to_payload()
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in payload.items())


def test_payload_carries_a_type_tag():
    assert order().to_payload()["type"] == "OrderCommand"


def test_order_rejects_unknown_side():
    with pytest.raises(Exception):
        OrderCommand(client_order_id="c", symbol="600036.SH", side="hold", qty=1,
                     order_type="market", limit_price=None, created_at=NOW)


def test_order_rejects_non_positive_qty():
    with pytest.raises(Exception):
        OrderCommand(client_order_id="c", symbol="600036.SH", side="buy", qty=0,
                     order_type="market", limit_price=None, created_at=NOW)


def test_limit_order_requires_price():
    with pytest.raises(ValueError, match="limit_price"):
        OrderCommand(client_order_id="c", symbol="600036.SH", side="buy", qty=100,
                     order_type="limit", limit_price=None, created_at=NOW)


def test_fill_round_trips():
    fill = FillEvent(client_order_id="c-1", symbol="600036.SH", side="buy", qty=100,
                     price=27.58, fee=5.01, filled_at=NOW, broker_order_id="B123")
    assert FillEvent.from_payload(fill.to_payload()) == fill


def test_order_status_round_trips():
    event = OrderStatusEvent(
        client_order_id="c-1", broker_order_id="B1", symbol="600036.SH",
        side="buy", ordered_qty=1000, filled_qty=300, remaining_qty=700,
        status="partially_filled", at=NOW,
    )
    assert OrderStatusEvent.from_payload(event.to_payload()) == event


def test_invalid_fill_and_inconsistent_status_are_rejected():
    with pytest.raises(ValueError):
        FillEvent(
            client_order_id="o", symbol="600036.SH", side="buy", qty=0,
            price=10.0, fee=0.0, filled_at=NOW,
        )
    with pytest.raises(ValueError):
        OrderStatusEvent(
            client_order_id="o", broker_order_id="b", symbol="600036.SH",
            side="buy", ordered_qty=100, filled_qty=20, remaining_qty=70,
            status="partially_filled", at=NOW,
        )


def test_reject_round_trips():
    rej = RejectEvent(client_order_id="c-1", symbol="600036.SH",
                      reason="limit up", at=NOW)
    assert RejectEvent.from_payload(rej.to_payload()) == rej


def test_account_snapshot_round_trips():
    snap = AccountSnapshot(
        at=NOW, cash=98765.43,
        positions={"600036.SH": PositionSnapshot(total_qty=500, available_qty=500,
                                                 avg_cost=27.63)},
    )
    restored = AccountSnapshot.from_payload(snap.to_payload())
    assert restored == snap
    assert restored.positions["600036.SH"].total_qty == 500


def test_empty_account_snapshot_round_trips():
    snap = AccountSnapshot(at=NOW, cash=100_000.0, positions={})
    assert AccountSnapshot.from_payload(snap.to_payload()) == snap


def test_control_command_round_trips():
    cmd = ControlCommand(action="kill", at=NOW, reason="manual")
    assert ControlCommand.from_payload(cmd.to_payload()) == cmd


def test_control_rejects_unknown_action():
    with pytest.raises(Exception):
        ControlCommand(action="explode", at=NOW)


def test_heartbeat_round_trips():
    hb = Heartbeat(source="agent", at=NOW)
    assert Heartbeat.from_payload(hb.to_payload()) == hb


def test_from_payload_rejects_wrong_type_tag():
    payload = order().to_payload()
    with pytest.raises(ValueError, match="expected FillEvent"):
        FillEvent.from_payload(payload)


def test_datetime_survives_to_the_second():
    restored = OrderCommand.from_payload(order().to_payload())
    assert restored.created_at == NOW
