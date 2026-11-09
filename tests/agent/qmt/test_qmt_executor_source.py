import json

import pytest

from qsys.agent.qmt import protocol
from qmt_fake import ACCOUNT, FakeQmtApi, load_executor

KEY = bytes(range(32))
RUN = 'a' * 32
NOW = 1_762_653_600_000          # 2026-11-09 10:00 +08:00
CID = 'q261109001abcdef012345'


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def make(api=None, *, market=None, **literals):
    mod = load_executor(ACCOUNT_ID=ACCOUNT, PRICE_TYPE_MARKET=market, **literals)
    api = api or FakeQmtApi()
    clock = Clock()
    buf = bytearray(protocol.MAP_SIZE)
    ex = mod.Executor(RUN, KEY, api.as_api(), buf, clock)
    ex.beat_gateway(clock.now)
    return mod, ex, api, buf, clock


def send(ex, clock, kind='place', command=None, attempt='b' * 32, seq=1, now=None):
    raw = protocol.build_request(run_id=RUN, seq=seq, kind=kind, command=command or {},
                                 attempt_id=attempt, session_nonce='c' * 32,
                                 now_ms=clock.now if now is None else now, key=KEY)
    return protocol.parse_receipt(ex.receive(None, raw), KEY)


def place(symbol='510300.SH', side='buy', qty=100, order_type='limit', price=4.0, cid=CID,
          expires=NOW + 60_000):
    return dict(client_order_id=cid, symbol=symbol, side=side, qty=qty, order_type=order_type,
                limit_price=price if order_type == 'limit' else None, expires_at_ms=expires)


def passorders(api):
    return [c for c in api.calls if c[0] == 'passorder']


@pytest.mark.parametrize('symbol', ['688001.SH', '920001.BJ', '430001.BJ', '830001.BJ'])
def test_board_whitelist_rejects_buys(symbol):
    _, ex, api, _, clock = make()
    receipt = send(ex, clock, command=place(symbol=symbol))
    assert (receipt['status'], receipt['reason']) == ('rejected', 'market_blocked')
    assert passorders(api) == []


def test_chinext_buy_allowed_and_star_sell_allowed():
    _, ex, api, _, clock = make()
    assert send(ex, clock, command=place(symbol='300750.SZ', price=50.0))['status'] == 'accepted'
    receipt = send(ex, clock, command=place(symbol='688001.SH', side='sell', cid='q261109002abcdef012345'),
                   attempt='d' * 32, seq=2)
    assert receipt['status'] == 'accepted'
    assert [c[1] for c in passorders(api)] == [23, 24]


def test_order_notional_cap_10000():
    _, ex, api, _, clock = make()
    receipt = send(ex, clock, command=place(qty=100, price=100.01))
    assert receipt['reason'] == 'order_notional'
    assert send(ex, clock, command=place(qty=100, price=100.0), attempt='d' * 32, seq=2)['status'] == 'accepted'


def test_daily_buy_cap_accumulates_and_resets_next_day():
    _, ex, api, _, clock = make()
    assert send(ex, clock, command=place(qty=1500, price=4.0))['status'] == 'accepted'   # 6000
    second = place(qty=1100, price=4.0, cid='q261109002abcdef012345')                       # +4400
    assert send(ex, clock, command=second, attempt='d' * 32, seq=2)['reason'] == 'daily_buy_limit'
    clock.now += 24 * 3600 * 1000
    ex.beat_gateway(clock.now)
    second['expires_at_ms'] = clock.now + 60_000
    assert send(ex, clock, command=second, attempt='e' * 32, seq=3)['status'] == 'accepted'


def test_market_sell_rejected_until_price_type_configured():
    _, ex, api, _, clock = make()
    receipt = send(ex, clock, command=place(side='sell', order_type='market'))
    assert receipt['reason'] == 'market_price_type_unverified'
    assert passorders(api) == []
    _, ex, api, _, clock = make(market=5)
    assert send(ex, clock, command=place(side='sell', order_type='market'))['status'] == 'accepted'
    assert passorders(api)[0][5] == 5


def test_market_buy_always_rejected():
    _, ex, api, _, clock = make(market=5)
    assert send(ex, clock, command=place(order_type='market'))['reason'] == 'buy_requires_limit'


def test_expired_command_rejected_not_placed():
    _, ex, api, _, clock = make()
    receipt = send(ex, clock, command=place(expires=NOW))
    assert receipt['reason'] == 'command_expired'
    assert passorders(api) == []


def test_stale_transport_frame_rejected():
    _, ex, api, _, clock = make()
    receipt = send(ex, clock, command=place(), now=NOW - protocol.TRANSPORT_TTL_MS)
    assert receipt['reason'] == 'transport_expired'


def test_existing_remark_reports_instead_of_reordering():
    api = FakeQmtApi()
    api.order(CID)
    _, ex, api, _, clock = make(api)
    receipt = send(ex, clock, command=place())
    assert receipt['status'] == 'accepted' and receipt['result']['status'] == 'already_present'
    assert passorders(api) == []


def test_same_attempt_returns_duplicate_receipt():
    _, ex, api, _, clock = make()
    first = send(ex, clock, command=place())
    again = send(ex, clock, command=place())
    assert first['status'] == 'accepted' and again['status'] == 'duplicate'
    assert len(passorders(api)) == 1


def test_attempt_reuse_with_other_content_conflicts():
    _, ex, api, _, clock = make()
    send(ex, clock, command=place())
    receipt = send(ex, clock, command=place(qty=200))
    assert receipt['reason'] == 'attempt_conflict'


def test_gateway_heartbeat_stale_blocks_buy_allows_sell_and_cancel():
    _, ex, api, _, clock = make()
    clock.now += 10_001
    assert send(ex, clock, command=place(expires=clock.now + 60_000))['reason'] == 'gateway_stale'
    sell = place(side='sell', cid='q261109002abcdef012345', expires=clock.now + 60_000)
    assert send(ex, clock, command=sell, attempt='d' * 32, seq=2)['status'] == 'accepted'
    receipt = send(ex, clock, kind='cancel', command={'client_order_id': 'q261109002abcdef012345'},
                   attempt='e' * 32, seq=3)
    assert receipt['status'] == 'accepted'


def test_kill_word_blocks_buy_and_cancels_all_once():
    mod, ex, api, buf, clock = make()
    api.order('q261109009abcdef012345', status=50)
    api.order('foreignmanualorder', status=50)
    api.order('q261109008abcdef012345', status=56)
    buf[protocol.CONTROL_OFFSET + 8:protocol.CONTROL_OFFSET + 12] = (1).to_bytes(4, 'little')
    ex.tick(None)
    ex.tick(None)
    assert sorted(c[1] for c in api.calls if c[0] == 'cancel') == ['S1', 'S2']
    assert send(ex, clock, command=place())['reason'] == 'kill_engaged'


def test_ring_full_blocks_new_buys_without_dropping_reports():
    mod, ex, api, buf, clock = make()
    for i in range(protocol.RING_SLOTS + 5):
        ex.record_order('callback', api.order(f'q2611090{i:02d}abcdef012345'[:22]))
    assert ex.backlog() == 5
    assert send(ex, clock, command=place(cid='q261109999abcdef012345'))['reason'] == 'ring_full'
    buf[protocol.CONTROL_OFFSET + 16:protocol.CONTROL_OFFSET + 24] = (protocol.RING_SLOTS).to_bytes(8, 'little')
    ex.tick(None)
    assert ex.backlog() == 0
    latest = int.from_bytes(buf[protocol.EXECUTOR_OFFSET + 40:protocol.EXECUTOR_OFFSET + 48], 'little')
    assert latest == protocol.RING_SLOTS + 5


def test_only_own_remark_shape_reported_and_no_account_value():
    mod, ex, api, buf, clock = make()
    assert not ex.record_order('callback', api.order('manual'))
    assert ex.record_order('callback', api.order(CID))
    slot = protocol.RING_OFFSET + 1 * protocol.RING_SLOT_SIZE
    size = int.from_bytes(buf[slot:slot + 4], 'little')
    row = json.loads(buf[slot + 12:slot + 12 + size])
    assert row['m_strRemark'] == CID and row['account_match'] is True
    assert ACCOUNT not in json.dumps(row)


def test_passorder_exception_is_reported_as_unknown_effect():
    api = FakeQmtApi()

    def boom(*args):
        raise RuntimeError('native')
    _, ex, _, _, clock = make(api)
    ex.api['passorder'] = boom
    receipt = send(ex, clock, command=place())
    assert receipt['status'] == 'accepted' and receipt['result']['status'] == 'call_exception'


def test_constants_match_protocol_module():
    mod = load_executor()
    for name in ('PROTOCOL', 'MAP_SIZE', 'REQUEST_OFFSET', 'RESPONSE_OFFSET', 'CONTROL_OFFSET',
                 'EXECUTOR_OFFSET', 'COORDINATOR_MARKER_OFFSET', 'SCHEDULED_MARKER_OFFSET',
                 'RING_OFFSET', 'RING_SLOT_SIZE', 'RING_SLOTS', 'FRAME_MAX', 'TRANSPORT_TTL_MS',
                 'KINDS', 'RECEIPTS'):
        assert getattr(mod, name) == getattr(protocol, name), name


def test_checked_in_source_is_inert():
    mod = load_executor()
    assert mod.ENABLED is False and mod.KEY_HEX == '' and mod.ACCOUNT_ID == '' and mod.RUN_ID == ''
    assert mod.PRICE_TYPE_MARKET is None
    assert mod.MAX_ORDER_NOTIONAL == 10000.0 and mod.MAX_DAILY_BUY_NOTIONAL == 10000.0
    assert mod.GATEWAY_STALE_MS == 10000
