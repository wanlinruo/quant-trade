"""#537: real callback exclusion, timing boundaries and unknown-effect replay."""
import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from threading import Event

import pytest

ROOT = Path(__file__).resolve().parents[2]
DAY = date(2026, 10, 23)
AT = datetime(2026, 10, 23, 9, 25, 10)
RUN = 'offline-instance-one'


@pytest.fixture(scope='module')
def api():
    path = ROOT / 'docs/reference/qmt-inner-runtime-guard.py'
    spec = importlib.util.spec_from_file_location('guard_specimen', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guard(api):
    # These explicit durations are synthetic test policy, never production defaults.
    return api.CallbackGuard(DAY, RUN, [
        api.Checkpoint('auction', AT, timedelta(seconds=5), True, timedelta(seconds=2)),
        api.Checkpoint('exit', AT.replace(hour=11, minute=25, second=0),
                       timedelta(seconds=3), False, None),
    ])


def evidence(api, **changes):
    values = dict(session=DAY, run_id=RUN, checked_at=AT - timedelta(seconds=1),
                  valid_until=AT + timedelta(hours=2))
    values.update({name: True for name in api.RECOVERY_CHECKS})
    values.update(changes)
    return api.RecoveryEvidence(**values)


def reject(guard, recovery, *, reason, now=AT, quote_at=AT, name='auction'):
    result = guard.dispatch(name, now=now, quote_at=quote_at, recovery=recovery,
                            callback=lambda: pytest.fail('business callback entered'))
    assert not result.completed and result.reason == reason and result.value is None


def test_py36_and_no_runtime_io_or_qmt_entrypoints(api):
    source = (ROOT / 'docs/reference/qmt-inner-runtime-guard.py').read_text()
    tree = ast.parse(source, feature_version=(3, 6))
    assert {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} == {
        'collections', 'datetime', 'threading',
    }
    assert not any(isinstance(n, ast.Import) for n in ast.walk(tree))
    calls = {n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not calls & {'open', 'exec', 'eval', '__import__', 'passorder', 'cancel',
                        'schedule_run', 'get_trade_detail_data'}
    assert not hasattr(api, 'init') and not hasattr(api, 'handlebar')


@pytest.mark.parametrize('value', [None, False, True, {}, 'verified'])
def test_unrestored_restart_never_enters_business_callback(api, guard, value):
    reject(guard, value, reason='recovery_missing' if value is None else 'recovery_invalid')


@pytest.mark.parametrize('flag', [
    'identity_verified', 'account_fresh', 'required_history_complete', 'lots_restored',
    'orders_reconciled', 'dedup_restored', 'single_owner_confirmed',
    'stop_control_verified', 'calendar_verified', 'clock_verified',
])
@pytest.mark.parametrize('value', [None, False, 1, 'true'])
def test_each_unverified_fact_blocks_business(api, guard, flag, value):
    reject(guard, evidence(api, **{flag: value}), reason='recovery_unverified')


@pytest.mark.parametrize('changes,reason', [
    ({'session': DAY + timedelta(days=1)}, 'recovery_session_mismatch'),
    ({'run_id': 'other-instance'}, 'recovery_run_mismatch'),
    ({'checked_at': AT + timedelta(microseconds=1)}, 'recovery_from_future'),
    ({'valid_until': AT - timedelta(microseconds=1)}, 'recovery_expired'),
    ({'checked_at': AT.replace(tzinfo=timezone.utc)}, 'recovery_time_invalid'),
    ({'valid_until': AT + timedelta(days=1)}, 'recovery_time_invalid'),
    ({'checked_at': AT, 'valid_until': AT - timedelta(seconds=1)}, 'recovery_time_invalid'),
])
def test_evidence_is_bound_to_instance_session_and_validity(api, guard, changes, reason):
    reject(guard, evidence(api, **changes), reason=reason)


@pytest.mark.parametrize('now,quote,reason', [
    (None, AT, 'clock_invalid'),
    (AT.replace(tzinfo=timezone.utc), AT, 'clock_invalid'),
    (AT + timedelta(days=1), AT, 'session_mismatch'),
    (AT - timedelta(microseconds=1), AT, 'too_early'),
    (AT + timedelta(seconds=5, microseconds=1), AT, 'too_late'),
    (AT, None, 'quote_missing'),
    (AT, 0, 'quote_time_invalid'),
    (AT, AT.replace(tzinfo=timezone.utc), 'quote_time_invalid'),
    (AT, AT - timedelta(days=1), 'quote_session_mismatch'),
    (AT, AT + timedelta(microseconds=1), 'quote_from_future'),
    (AT, AT - timedelta(seconds=2, microseconds=1), 'quote_expired'),
])
def test_rejected_observations_do_not_enter_callback(api, guard, now, quote, reason):
    reject(guard, evidence(api), reason=reason, now=now, quote_at=quote)


@pytest.mark.parametrize('delay', [timedelta(0), timedelta(seconds=5)])
def test_exact_deadlines_and_quote_age_are_inclusive(api, guard, delay):
    now = AT + delay
    calls = []
    result = guard.dispatch('auction', now=now, quote_at=now - timedelta(seconds=2),
                            recovery=evidence(api), callback=lambda: calls.append('one') or 'done')
    assert result == api.DispatchResult(True, 'callback_completed', 'done')
    assert calls == ['one']
    reject(guard, None, reason='checkpoint_consumed', now=now, quote_at=None)


def test_early_or_missing_quote_can_be_rechecked_before_claim(api, guard):
    proof = evidence(api)
    reject(guard, proof, reason='too_early', now=AT - timedelta(microseconds=1))
    reject(guard, proof, reason='quote_missing', quote_at=None)
    assert guard.dispatch('auction', now=AT, quote_at=AT, recovery=proof,
                          callback=lambda: 'one').completed


def test_reentrant_callback_cannot_dispatch_again(api, guard):
    proof = evidence(api)
    calls = []

    def handler():
        calls.append('entered')
        reject(guard, proof, reason='dispatch_busy')
        return 'done'

    assert guard.dispatch('auction', now=AT, quote_at=AT, recovery=proof,
                          callback=handler).value == 'done'
    assert calls == ['entered']


@pytest.mark.parametrize('error', [RuntimeError('delivery result unknown'), KeyboardInterrupt()])
def test_unknown_callback_effect_is_not_replayed(api, guard, error):

    def handler():
        raise error

    with pytest.raises(type(error)) as caught:
        guard.dispatch('auction', now=AT, quote_at=AT, recovery=evidence(api), callback=handler)
    assert caught.value is error
    reject(guard, evidence(api), reason='checkpoint_consumed')


def test_invalid_callback_does_not_consume_or_keep_lock(api, guard):
    with pytest.raises(ValueError, match='callable'):
        guard.dispatch('auction', now=AT, quote_at=AT, recovery=evidence(api), callback=None)
    assert guard.dispatch('auction', now=AT, quote_at=AT, recovery=evidence(api),
                          callback=lambda: 'valid').value == 'valid'


def test_exact_evidence_window_is_accepted(api, guard):
    proof = evidence(api, checked_at=AT, valid_until=AT)
    assert guard.dispatch('auction', now=AT, quote_at=AT, recovery=proof,
                          callback=lambda: 'boundary').completed


def test_busy_dispatch_is_rejected_without_queuing_old_inputs(api, guard):
    entered, release, finished, second_started = (Event() for _ in range(4))
    proof = evidence(api)

    def first():
        entered.set()
        assert release.wait(5)
        finished.set()
        return 'first'

    def second():
        second_started.set()

        def handler():
            assert finished.is_set()
            return 'second'

        return guard.dispatch('exit', now=AT.replace(hour=11, minute=25, second=0),
                              quote_at=None, recovery=proof, callback=handler)

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(guard.dispatch, 'auction', now=AT, quote_at=AT,
                          recovery=proof, callback=first)
        try:
            assert entered.wait(5)
            two = pool.submit(second)
            assert second_started.wait(5)
            assert two.result(timeout=5) == api.DispatchResult(False, 'dispatch_busy', None)
            assert not finished.is_set()
        finally:
            release.set()
        assert one.result(timeout=5).value == 'first'
        assert second().value == 'second'


def test_distinct_checkpoints_and_new_instance_need_separate_proof(api, guard):
    proof = evidence(api)
    assert guard.dispatch('auction', now=AT, quote_at=AT, recovery=proof,
                          callback=lambda: 'auction').completed
    result = guard.dispatch('exit', now=AT.replace(hour=11, minute=25, second=0),
                            quote_at=None, recovery=proof, callback=lambda: 'exit')
    assert result.value == 'exit'
    next_instance = api.CallbackGuard(DAY, 'restarted-instance', [
        api.Checkpoint('auction', AT, timedelta(seconds=5), True, timedelta(seconds=2)),
    ])
    reject(next_instance, None, reason='recovery_missing')
    reject(next_instance, proof, reason='recovery_run_mismatch')


def test_complete_native_account_identity_alone_cannot_authorize_callback(api, guard):
    # Mirrors the available *evidence scope*, without injecting real account data.
    values = {key: None for key in api.RECOVERY_CHECKS}
    values['identity_verified'] = True
    reject(guard, evidence(api, **values), reason='recovery_unverified')


@pytest.mark.parametrize('name', ['unknown', '', None, ['auction']])
def test_unknown_checkpoint_refuses(api, guard, name):
    reject(guard, evidence(api), reason='checkpoint_unknown', name=name)


@pytest.mark.parametrize('changes', [
    {'max_lateness': None}, {'max_lateness': timedelta(seconds=-1)},
    {'requires_quote': 1}, {'max_quote_age': None},
    {'max_quote_age': timedelta(seconds=-1)},
    {'at': AT.replace(tzinfo=timezone.utc)}, {'at': AT + timedelta(days=1)},
])
def test_no_implicit_timing_or_quote_policy(api, changes):
    fields = dict(name='auction', at=AT, max_lateness=timedelta(seconds=5),
                  requires_quote=True, max_quote_age=timedelta(seconds=2))
    fields.update(changes)
    with pytest.raises(ValueError):
        api.CallbackGuard(DAY, RUN, [api.Checkpoint(**fields)])


def test_duplicate_labels_rejected_and_mutable_input_cannot_change_policy(api):
    point = api.Checkpoint('auction', AT, timedelta(seconds=5), True, timedelta(seconds=2))
    with pytest.raises(ValueError, match='duplicate'):
        api.CallbackGuard(DAY, RUN, [point, point])
    points = [point]
    instance = api.CallbackGuard(DAY, RUN, points)
    points.clear()
    assert instance.dispatch('auction', now=AT, quote_at=AT, recovery=evidence(api),
                             callback=lambda: None).completed
