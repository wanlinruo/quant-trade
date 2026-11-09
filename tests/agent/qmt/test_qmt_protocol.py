import json

import pytest

from qsys.agent.qmt import protocol


KEY = bytes(range(32))


def _request(**overrides):
    args = dict(run_id='a' * 32, seq=1, kind='place',
                command={'client_order_id': 'q261109001abcdef012345'},
                attempt_id='b' * 32, session_nonce='c' * 32, now_ms=1_000, key=KEY)
    args.update(overrides)
    return protocol.build_request(**args)


def test_request_roundtrip_and_tamper_detection():
    obj = json.loads(_request())
    assert obj['protocol'] == protocol.PROTOCOL
    assert obj['expires_at_ms'] - obj['issued_at_ms'] == protocol.TRANSPORT_TTL_MS
    assert protocol.verify(dict(obj), KEY)
    obj['seq'] = 2
    assert not protocol.verify(obj, KEY)


def test_request_rejects_unknown_kind_and_oversize():
    with pytest.raises(ValueError):
        _request(kind='transfer')
    with pytest.raises(ValueError):
        _request(command={'pad': 'x' * protocol.FRAME_MAX})


def test_receipt_parse_checks_signature():
    row = {'protocol': protocol.PROTOCOL, 'status': 'accepted', 'reason': 'ok'}
    row['mac'] = protocol.sign(row, KEY)
    raw = protocol.canonical(row)
    assert protocol.parse_receipt(raw, KEY)['status'] == 'accepted'
    with pytest.raises(ValueError, match='receipt_signature'):
        protocol.parse_receipt(raw, bytes(32))


def test_layout_regions_do_not_overlap():
    assert protocol.RING_OFFSET + protocol.RING_SLOTS * protocol.RING_SLOT_SIZE <= protocol.MAP_SIZE
    assert protocol.FRAME_MAX < protocol.RESPONSE_OFFSET - protocol.REQUEST_OFFSET - 8
    regions = sorted([protocol.REQUEST_OFFSET, protocol.RESPONSE_OFFSET, protocol.CONTROL_OFFSET,
                      protocol.EXECUTOR_OFFSET, protocol.COORDINATOR_MARKER_OFFSET,
                      protocol.SCHEDULED_MARKER_OFFSET, protocol.RING_OFFSET])
    assert len(set(regions)) == len(regions)
