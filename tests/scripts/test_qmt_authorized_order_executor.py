import hashlib
import importlib.util
import json
import mmap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

REFERENCE = Path(__file__).resolve().parents[2] / 'docs' / 'reference'
RUN = '0123456789abcdef0123456789abcdef'
KEY = bytes(range(32))
ACCOUNT = '777888999'


def load():
    spec = importlib.util.spec_from_file_location(
        'qsys_authorized_order_executor', REFERENCE / 'qmt-authorized-order-executor.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ACCOUNT_ID = ACCOUNT
    return module


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def plan_dict(**overrides):
    now = datetime.now(timezone.utc)
    plan = {
        'run_id': RUN,
        'not_before': iso(now - timedelta(minutes=5)),
        'not_after': iso(now + timedelta(minutes=55)),
        'steps': [
            {'step': 1, 'action': 'place', 'client_order_id': 'q601-01234567-a',
             'symbol': '510300.SH', 'side': 'buy', 'qty': 100, 'limit_price': 3.9},
            {'step': 2, 'action': 'cancel', 'client_order_id': 'q601-01234567-a'},
            {'step': 3, 'action': 'place', 'client_order_id': 'q601-01234567-b',
             'symbol': '510300.SH', 'side': 'buy', 'qty': 100, 'limit_price': 4.2},
            {'step': 4, 'action': 'cancel', 'client_order_id': 'q601-01234567-b'},
        ],
    }
    plan.update(overrides)
    return plan


def plan_text(module, **overrides):
    return module.canonical(plan_dict(**overrides)).decode('ascii')


class FakeApi:
    def __init__(self):
        self.calls = []
        self.orders, self.deals, self.positions = [], [], []

    def passorder(self, *args):
        self.calls.append(('passorder',) + args)

    def cancel(self, *args):
        self.calls.append(('cancel',) + args)
        return True

    def get_trade_detail_data(self, account, account_type, datatype):
        self.calls.append(('query', account, account_type, datatype))
        return {'ORDER': self.orders, 'DEAL': self.deals, 'POSITION': self.positions}[datatype]

    def table(self):
        return {'passorder': self.passorder, 'cancel': self.cancel,
                'get_trade_detail_data': self.get_trade_detail_data}


def order(remark, status=50, sysid='SYS1', account=ACCOUNT):
    return SimpleNamespace(m_strRemark=remark, m_strOrderSysID=sysid, m_nOrderStatus=status,
                           m_nVolumeTotalOriginal=100, m_nVolumeTraded=0, m_dLimitPrice=3.9,
                           m_strAccountID=account, m_strInstrumentID='510300')


def make_executor(module, api=None, reports=None):
    api = api or FakeApi()
    reports = [] if reports is None else reports
    plan = module.validate_plan(plan_text(module), RUN)
    return module.Executor(RUN, KEY, plan, api.table(), reports.append), api, reports


def frame(module, kind, seq, command, attempt=None, now=None, key=KEY, **overrides):
    now = module.now_ms() if now is None else now
    command_json = module.canonical(command).decode('ascii')
    obj = dict(protocol=module.PROTOCOL, scope=module.SCOPE, kind=kind, run_id=RUN, seq=seq,
               attempt_id=attempt or hashlib.md5(f'{kind}{seq}{command_json}'.encode()).hexdigest(),
               session_nonce='f' * 32,
               command_sha256=hashlib.sha256(command_json.encode('ascii')).hexdigest(),
               command_json=command_json, issued_at_ms=now - 10, expires_at_ms=now + 3000)
    obj.update(overrides)
    obj['mac'] = module.sign(obj, key)
    return module.canonical(obj)


def reply(raw):
    return json.loads(raw)


# -- plan validation ---------------------------------------------------------

def test_valid_plan_is_accepted_and_indexes_remarks():
    module = load()
    plan = module.validate_plan(plan_text(module), RUN)
    assert plan['remarks'] == {'q601-01234567-a', 'q601-01234567-b'}


@pytest.mark.parametrize('field, value, reason', [
    ('symbol', '920001.BJ', 'plan_symbol'),
    ('symbol', '830799.BJ', 'plan_symbol'),
    ('symbol', '510300.SZ', 'plan_symbol_exchange'),
    ('symbol', '688001.SH', 'plan_symbol'),
    ('symbol', '300750.SZ', 'plan_symbol'),
    ('qty', 150, 'plan_qty'),
    ('qty', 200, 'plan_qty'),
    ('qty', 100.0, 'plan_qty'),
    ('limit_price', 3.9001, 'plan_price'),
    ('limit_price', 11.0, 'plan_price'),
    ('limit_price', 4, 'plan_price'),
    ('side', 'short', 'plan_side'),
    ('client_order_id', 'q601-ffffffff-a', 'plan_order_identity'),
])
def test_plan_rejects_out_of_bounds_place(field, value, reason):
    module = load()
    plan = plan_dict()
    plan['steps'][0][field] = value
    with pytest.raises(ValueError, match=reason):
        module.validate_plan(module.canonical(plan).decode('ascii'), RUN)


def test_plan_rejects_cancel_without_prior_place_and_long_window():
    module = load()
    plan = plan_dict()
    plan['steps'][1]['client_order_id'] = 'q601-01234567-z'
    with pytest.raises(ValueError, match='plan_cancel_target'):
        module.validate_plan(module.canonical(plan).decode('ascii'), RUN)
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match='plan_window'):
        module.validate_plan(plan_text(module, not_after=iso(now + timedelta(hours=4))), RUN)
    with pytest.raises(ValueError, match='plan_run'):
        module.validate_plan(plan_text(module), 'f' * 32)


# -- executor ----------------------------------------------------------------

def test_place_calls_passorder_exactly_once_with_authorized_arguments():
    module = load()
    executor, api, _ = make_executor(module)
    step = plan_dict()['steps'][0]
    raw = frame(module, 'step', 1, step)
    context = object()

    first = reply(executor.receive(context, raw, module.now_ms()))
    again = reply(executor.receive(context, raw, module.now_ms()))

    assert first['status'] == 'handled' and first['result'] == {'status': 'passorder_called'}
    assert first['broker_accepted'] is None
    assert again == first
    placed = [c for c in api.calls if c[0] == 'passorder']
    assert placed == [('passorder', 23, 1101, ACCOUNT, '510300.SH', 11, 3.9, 100,
                       'qsys601', 2, 'q601-01234567-a', context)]
    other_attempt = frame(module, 'step', 1, step, attempt='e' * 32)
    assert reply(executor.receive(context, other_attempt, module.now_ms()))['reason'] == 'step_order'
    assert len([c for c in api.calls if c[0] == 'passorder']) == 1


def test_place_reports_existing_remark_instead_of_ordering_again():
    module = load()
    api = FakeApi()
    api.orders = [order('q601-01234567-a')]
    executor, api, reports = make_executor(module, api)

    result = reply(executor.receive(None, frame(module, 'step', 1, plan_dict()['steps'][0]),
                                    module.now_ms()))

    assert result['result'] == {'status': 'already_present', 'orders': 1}
    assert not [c for c in api.calls if c[0] == 'passorder']
    assert reports[0]['kind'] == 'order' and reports[0]['source'] == 'query'


def test_unauthorized_step_content_is_rejected_without_consuming_step():
    module = load()
    executor, api, _ = make_executor(module)
    changed = dict(plan_dict()['steps'][0], limit_price=3.8)

    result = reply(executor.receive(None, frame(module, 'step', 1, changed), module.now_ms()))

    assert result['status'] == 'rejected' and result['reason'] == 'step_not_authorized'
    assert executor.next_step == 1 and not api.calls


@pytest.mark.parametrize('status, expected', [
    (50, 'cancel_signal_sent'), (55, 'cancel_signal_sent'),
    (54, 'cancel_not_needed'), (56, 'cancel_not_needed'), (57, 'cancel_not_needed'),
])
def test_cancel_uses_order_sys_id_only_for_live_orders(status, expected):
    module = load()
    api = FakeApi()
    api.orders = [order('q601-01234567-a', status=status, sysid='SYS9')]
    executor, api, _ = make_executor(module, api)
    executor.next_step = 2

    result = reply(executor.receive('ctx', frame(module, 'step', 2, plan_dict()['steps'][1]),
                                    module.now_ms()))

    assert result['result']['status'] == expected
    cancels = [c for c in api.calls if c[0] == 'cancel']
    if expected == 'cancel_signal_sent':
        assert cancels == [('cancel', 'SYS9', ACCOUNT, 'STOCK', 'ctx')]
        assert result['result']['cancel_result'] is True
    else:
        assert cancels == []


def test_cancel_target_absent_or_ambiguous_never_cancels():
    module = load()
    executor, api, _ = make_executor(module)
    executor.next_step = 2
    cancel_step = plan_dict()['steps'][1]
    result = reply(executor.receive(None, frame(module, 'step', 2, cancel_step), module.now_ms()))
    assert result['result']['status'] == 'cancel_target_absent'

    executor, api, _ = make_executor(module)
    api.orders.extend([order('q601-01234567-a', sysid='S1'), order('q601-01234567-a', sysid='S2')])
    executor.next_step = 2
    result = reply(executor.receive(None, frame(module, 'step', 2, cancel_step), module.now_ms()))
    assert result['result'] == {'status': 'cancel_target_ambiguous', 'orders': 2}
    assert not [c for c in api.calls if c[0] == 'cancel']


def test_steps_may_skip_forward_but_never_go_back():
    module = load()
    executor, api, _ = make_executor(module)
    steps = plan_dict()['steps']
    assert reply(executor.receive(None, frame(module, 'step', 3, steps[2]), module.now_ms()))[
        'result'] == {'status': 'passorder_called'}
    back = reply(executor.receive(None, frame(module, 'step', 1, steps[0]), module.now_ms()))
    assert back['reason'] == 'step_order'


def test_native_exception_consumes_step_and_keeps_executor_alive():
    module = load()
    api = FakeApi()

    def boom(*args):
        raise RuntimeError('native failure')

    executor, api, _ = make_executor(module, api)
    executor.api = dict(api.table(), passorder=boom)
    steps = plan_dict()['steps']

    result = reply(executor.receive(None, frame(module, 'step', 1, steps[0]), module.now_ms()))

    assert result['result'] == {'status': 'step_exception', 'error_type': 'RuntimeError'}
    assert executor.next_step == 2
    snap = reply(executor.receive(None, frame(module, 'snapshot', 1, {'action': 'snapshot', 'n': 1}),
                                  module.now_ms()))
    assert snap['status'] == 'handled'


def test_signature_window_and_expiry_are_enforced():
    module = load()
    executor, api, _ = make_executor(module)
    step = plan_dict()['steps'][0]
    now = module.now_ms()

    assert reply(executor.receive(None, frame(module, 'step', 1, step, key=b'x' * 32), now))[
        'reason'] == 'signature'
    assert reply(executor.receive(None, frame(module, 'step', 1, step, now=now - 60000), now))[
        'reason'] == 'transport_expired'
    late = now + 2 * 3600 * 1000
    assert reply(executor.receive(None, frame(module, 'step', 1, step, now=late), late))[
        'reason'] == 'outside_authorized_window'
    assert not api.calls


def test_callbacks_record_only_plan_remarks_without_account_id():
    module = load()
    executor, _, reports = make_executor(module)

    assert executor.record_order('callback', order('someone-else')) is False
    assert executor.record_order('callback', order('q601-01234567-a', account='999')) is True
    deal = SimpleNamespace(m_strRemark='q601-01234567-b', m_strTradeID='T1', m_strOrderSysID='S',
                           m_dPrice=4.1, m_nVolume=100, m_dComssion=5.0, m_strAccountID=ACCOUNT)
    assert executor.record_deal('callback', deal) is True
    error = SimpleNamespace(strategyName='qsys601_&&&_q601-01234567-b', orderCode='SH510300', prType=11)
    assert executor.record_error(error, 'price invalid') is True
    assert executor.record_error(SimpleNamespace(strategyName='other'), 'x') is False
    assert reports[-1]['kind'] == 'order_error_unmatched' and reports[-1]['m_strRemark'] is None
    reports.pop()

    text = json.dumps(reports)
    assert ACCOUNT not in text and '999' not in text
    assert reports[0]['account_match'] is False and reports[1]['account_match'] is True
    assert reports[1]['m_dComssion'] == 5.0 and 'm_dCommission' not in reports[1]
    assert reports[2]['kind'] == 'order_error'


def test_snapshot_filters_orders_deals_and_positions():
    module = load()
    api = FakeApi()
    api.orders = [order('q601-01234567-a'), order('foreign')]
    api.deals = [SimpleNamespace(m_strRemark='foreign')]
    api.positions = [SimpleNamespace(m_strInstrumentID='510300', m_nVolume=100, m_strAccountID=ACCOUNT),
                     SimpleNamespace(m_strInstrumentID='600000', m_nVolume=900)]
    executor, api, reports = make_executor(module, api)

    result = reply(executor.receive(None, frame(module, 'snapshot', 1, {'action': 'snapshot', 'n': 1}),
                                    module.now_ms()))

    assert result['result'] == {'orders': 1, 'deals': 0, 'positions': 1}
    assert [r['kind'] for r in reports] == ['order', 'position']


def test_report_ring_appends_slots_and_flags_overflow():
    module = load()
    memory = mmap.mmap(-1, module.MAP_SIZE)
    ring = module.ReportRing(memory)
    for index in range(module.REPORT_LIMIT + 2):
        ring({'kind': 'order', 'n': index})
    memory.seek(module.REPORT_HEADER_OFFSET)
    assert int.from_bytes(memory.read(4), 'little') == module.REPORT_LIMIT
    assert int.from_bytes(memory.read(4), 'little') == 1
    memory.seek(module.REPORT_SLOTS_OFFSET)
    size = int.from_bytes(memory.read(4), 'little')
    assert json.loads(memory.read(size)) == {'kind': 'order', 'n': 0}
    assert module.REPORT_SLOTS_OFFSET + module.REPORT_LIMIT * module.REPORT_SLOT_SIZE <= module.MAP_SIZE


def test_checked_in_candidate_is_inert_and_has_no_secrets():
    source = (REFERENCE / 'qmt-authorized-order-executor.py').read_text(encoding='utf-8')
    for literal in ("RUN_ID = ''", "CLIENT_BUILD = ''", "ACCOUNT_ID = ''", "KEY_HEX = ''",
                    "AUTHORIZED_PLAN = ''", 'ENABLED = False', 'MAX_QTY = 100',
                    'MAX_NOTIONAL = 1000.0'):
        assert literal in source


def test_init_disabled_does_nothing():
    module = load()
    module.ACCOUNT_ID = ''

    class Context:
        def __getattr__(self, name):
            raise AssertionError('disabled candidate must not touch ContextInfo')

    module.init(Context())
    assert module._STATE is None


# -- F0: production-shaped order ids and latest-price sells (#601) -------------

LONG_A = 'q261110001' + RUN[:12]
LONG_B = 'q261110002' + RUN[:12]


def f0_plan(**sell):
    now = datetime.now(timezone.utc)
    step = {'step': 3, 'action': 'place', 'client_order_id': LONG_B, 'symbol': '510300.SH',
            'side': 'sell', 'qty': 100, 'limit_price': None, 'price_type': 5}
    step.update(sell)
    return {'run_id': RUN, 'not_before': iso(now - timedelta(minutes=5)),
            'not_after': iso(now + timedelta(minutes=55)), 'steps': [
                {'step': 1, 'action': 'place', 'client_order_id': LONG_A, 'symbol': '510300.SH',
                 'side': 'buy', 'qty': 100, 'limit_price': 3.9},
                {'step': 2, 'action': 'cancel', 'client_order_id': LONG_A},
                step,
                {'step': 4, 'action': 'cancel', 'client_order_id': LONG_B}]}


def test_long_order_id_and_latest_price_sell_accepted():
    module = load()
    plan = module.validate_plan(module.canonical(f0_plan()).decode('ascii'), RUN)
    assert plan['remarks'] == {LONG_A, LONG_B}


@pytest.mark.parametrize('change', [
    {'side': 'buy'}, {'price_type': 42}, {'limit_price': 4.0}, {'price_type': '5'},
    {'client_order_id': 'q261110002' + 'f' * 12},
])
def test_latest_price_step_bounds(change):
    module = load()
    with pytest.raises(ValueError):
        module.validate_plan(module.canonical(f0_plan(**change)).decode('ascii'), RUN)


def test_latest_price_sell_calls_passorder_with_type_5_and_zero_price():
    module = load()
    api = FakeApi()
    plan = module.validate_plan(module.canonical(f0_plan()).decode('ascii'), RUN)
    executor = module.Executor(RUN, KEY, plan, api.table(), [].append)
    executor.run_step(None, plan['steps'][2])
    call = [c for c in api.calls if c[0] == 'passorder'][-1]
    assert call[1] == module.STOCK_SELL and call[5] == 5 and call[6] == 0.0 and call[10] == LONG_B
