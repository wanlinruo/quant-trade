from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
import json

import pytest

from qsys.data.quality.public_minute_prefix import (
    EvidenceRejected, Point, assess_prefix, number, sina_points, tencent_points,
)

DAY = date(2026, 11, 2)
START = datetime(2026, 11, 2, 9, 30)


def tencent_payload(symbol='603828.SH', leading=4):
    width = 3 if symbol.endswith('.BJ') else 4
    rows = []
    for i in range(leading + 1):
        fields = [(START + timedelta(minutes=i)).strftime('%H%M'), '4.620',
                  '0' if i < leading else '107']
        if width == 4:
            fields.append('0.00' if i < leading else '48476.00')
        rows.append(' '.join(fields))
    code = symbol[-2:].lower() + symbol[:6]
    return json.dumps({'code': 0, 'data': {code: {'data': [{'date': '20261102', 'data': rows}]}}}).encode()


def sina_payload(leading=4, earlier=True):
    rows = ([{'day': '2026-10-30 15:00:00', 'volume': '1', 'amount': '4.62'}]
            if earlier else [])
    rows.append({'day': (START + timedelta(minutes=leading)).strftime('%Y-%m-%d %H:%M:%S'),
                 'volume': '10700', 'amount': '48476.0032'})
    return ('/*<script>location.href=\'//sina.com\';</script>*/\nvar _data=(' +
            json.dumps(rows) + ');').encode()


def warehouse(leading=4):
    return [{'symbol': '603828.SH', 'dt': (START + timedelta(minutes=i)).isoformat(),
             'open': 4.62, 'high': 4.62, 'low': 4.62, 'close': 4.62,
             'volume': 0 if i < leading else 107, 'amount': 0 if i < leading else 48476}
            for i in range(leading + 1)]


def assess(rows=None, tencent=None, sina=None, leading=4):
    return assess_prefix('603828.SH', DAY, warehouse(leading) if rows is None else rows,
        tencent_points(tencent_payload(leading=leading), '603828.SH', DAY) if tencent is None else tencent,
        sina_points(sina_payload(leading=leading), DAY) if sina is None else sina)


@pytest.mark.parametrize('leading', [1, 4])
def test_two_publishers_support_first_positive_label_without_unit_conversion(leading):
    before = warehouse(leading)
    result = assess(before, leading=leading)
    assert result['supported'] and result['leading_rows'] == leading
    assert before == warehouse(leading)
    # 107 and 10700 remain different native numbers; no /100 compatibility conversion.
    assert tencent_points(tencent_payload(), '603828.SH', DAY)[-1].volume == Decimal('107')
    assert sina_points(sina_payload(), DAY)[0].volume == Decimal('10700')


def test_beijing_missing_amount_is_null_not_zero():
    rows = tencent_points(tencent_payload('920009.BJ', 1), '920009.BJ', DAY)
    assert rows[0].amount is None
    assert rows[1].volume == Decimal('107')


@pytest.mark.parametrize('value', [None, True, 'NaN', 'Infinity', 'missing'])
def test_missing_or_invalid_numbers_are_not_repaired(value):
    with pytest.raises(EvidenceRejected):
        number(value)


@pytest.mark.parametrize('row_change,code', [
    ({'amount': 1}, 'warehouse_prefix_volume_or_amount_not_zero'),
    ({'volume': -1}, 'warehouse_negative_volume'),
    ({'open': None}, 'missing_or_boolean_number'),
    ({'high': 5}, 'warehouse_zero_prefix_price_not_flat'),
    ({'symbol': '000001.SZ'}, 'warehouse_identity_mismatch'),
])
def test_invalid_warehouse_values_never_gain_public_observation_support(row_change, code):
    rows = warehouse();rows[0].update(row_change)
    result = assess(rows)
    assert not result['supported'] and code in result['errors']


def test_absent_prefix_start_and_grid_gap_are_rejected():
    assert assess(warehouse()[1:])['errors'] == ['warehouse_prefix_start_not_proven']
    rows = warehouse();rows.pop(1)
    assert assess(rows)['errors'] == ['warehouse_prefix_grid_gap']


def test_one_agreeing_provider_cannot_outvote_a_conflicting_provider():
    result = assess(sina=[Point(START + timedelta(minutes=3), Decimal(1), Decimal(1))])
    assert not result['supported'] and result['errors'] == ['sina_first_positive_mismatch']


def test_missing_tencent_prefix_point_is_not_filled():
    points = tencent_points(tencent_payload(), '603828.SH', DAY);points.pop(1)
    assert assess(tencent=points)['errors'] == ['tencent_zero_prefix_observation_missing']


def test_tencent_positive_amount_with_zero_quantity_is_not_exempted():
    points = tencent_points(tencent_payload(), '603828.SH', DAY)
    points[0] = Point(START, Decimal(0), Decimal(5))
    assert assess(tencent=points)['errors'] == ['tencent_prefix_not_zero']


def test_sina_truncated_response_does_not_prove_first_trade():
    with pytest.raises(EvidenceRejected, match='sina_prefix_window_not_proven'):
        sina_points(sina_payload(earlier=False), DAY)


@pytest.mark.parametrize('payload', [b'var _data=(null);', b'var _data=([]);',
    b'alert(1); var _data=([]);', b'var other=([]);', b'var _data=(JSON.parse("[]"));'])
def test_unknown_jsonp_or_empty_response_is_not_executed_or_used(payload):
    with pytest.raises((EvidenceRejected, ValueError)):
        sina_points(payload, DAY)


def test_sina_negative_or_inconsistent_values_reject():
    payload = sina_payload().replace(b'"10700"', b'"-1"')
    with pytest.raises(EvidenceRejected, match='sina_negative_value'):
        sina_points(payload, DAY)


def test_tencent_wrong_date_identity_width_and_duplicates_reject():
    with pytest.raises(EvidenceRejected, match='target_day'):
        tencent_points(tencent_payload().replace(b'20261102', b'20261101'), '603828.SH', DAY)
    with pytest.raises(KeyError):
        tencent_points(tencent_payload(), '000001.SZ', DAY)
    with pytest.raises(EvidenceRejected, match='row_width'):
        tencent_points(tencent_payload().replace(b'0930 4.620 0 0.00', b'0930 4.620 0'), '603828.SH', DAY)
    with pytest.raises(EvidenceRejected, match='not_unique'):
        tencent_points(tencent_payload().replace(b'0931', b'0930'), '603828.SH', DAY)


def test_decreasing_cumulative_volume_rejects():
    raw = tencent_payload(leading=1).replace(b'0930 4.620 0', b'0930 4.620 108')
    with pytest.raises(EvidenceRejected, match='decreased'):
        tencent_points(raw, '603828.SH', DAY)
