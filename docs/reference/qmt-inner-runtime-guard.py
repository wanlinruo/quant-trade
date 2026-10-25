# coding: utf-8
"""#537 / #541: portable callback guard specimen, NOT a QMT strategy.

No clock reads, QMT calls, persistence or scheduling. All dates are explicit
naive UTC+8 client times. Evidence must come from a future verified native
provider; constructing this record is not proof that recovery occurred.
The consumed set protects one instance only, not multiple models or restarts.
A callback exception halts this instance; no local reset is provided.
Quote timestamps do not certify price/volume content or source coverage.
"""
from collections import namedtuple as _GuardRecord
from datetime import date as _GuardDate, datetime as _GuardTime, timedelta as _GuardDelta
from threading import Lock as _GuardLock


RECOVERY_CHECKS = (
    'identity_verified', 'account_fresh', 'required_history_complete',
    'lots_restored', 'orders_reconciled', 'dedup_restored',
    'single_owner_confirmed', 'stop_control_verified',
    'calendar_verified', 'clock_verified',
)
RecoveryEvidence = _GuardRecord(
    'RecoveryEvidence', ('session', 'run_id', 'checked_at', 'valid_until') + RECOVERY_CHECKS)
Checkpoint = _GuardRecord(
    'Checkpoint', ('name', 'at', 'max_lateness', 'requires_quote', 'max_quote_age'))
DispatchResult = _GuardRecord('DispatchResult', ('completed', 'reason', 'value'))


def _guard_time(value):
    return isinstance(value, _GuardTime) and value.tzinfo is None


def _guard_span(value):
    return isinstance(value, _GuardDelta) and value >= _GuardDelta(0)


def _guard_name(value):
    return isinstance(value, str) and bool(value) and value == value.strip()


class CallbackGuard:
    """Check evidence/time, consume the event, then enter the callback once.

    All configuration is mandatory. Rejected observations do not consume an
    event. Once entered, a callback remains consumed even if it raises or
    re-enters this instance: its external effects may be unknown.
    """
    def __init__(self, session, run_id, checkpoints):
        if type(session) is not _GuardDate or not _guard_name(run_id):
            raise ValueError('explicit session date and run identity required')
        entries = tuple(checkpoints)
        if not entries:
            raise ValueError('at least one explicit checkpoint required')
        selected = {}
        for item in entries:
            if not isinstance(item, Checkpoint) or not _guard_name(item.name):
                raise ValueError('invalid checkpoint')
            if item.name in selected:
                raise ValueError('duplicate checkpoint name')
            if not _guard_time(item.at) or item.at.date() != session:
                raise ValueError('checkpoint must use this session and naive UTC+8 time')
            if not _guard_span(item.max_lateness):
                raise ValueError('explicit nonnegative lateness required')
            if type(item.requires_quote) is not bool:
                raise ValueError('quote requirement must be explicit')
            if item.requires_quote:
                if not _guard_span(item.max_quote_age):
                    raise ValueError('explicit nonnegative quote age required')
            elif item.max_quote_age is not None:
                raise ValueError('unused quote age must be None')
            selected[item.name] = item
        self._session = session
        self._run_id = run_id
        self._checkpoints = selected
        self._consumed = set()
        self._lock = _GuardLock()
        self._halted = False

    def _recovery_reason(self, evidence, now):
        if evidence is None:
            return 'recovery_missing'
        if not isinstance(evidence, RecoveryEvidence):
            return 'recovery_invalid'
        if type(evidence.session) is not _GuardDate or evidence.session != self._session:
            return 'recovery_session_mismatch'
        if evidence.run_id != self._run_id:
            return 'recovery_run_mismatch'
        # Truthy strings/integers must never stand in for verified metadata.
        if any(getattr(evidence, name) is not True for name in RECOVERY_CHECKS):
            return 'recovery_unverified'
        if not _guard_time(evidence.checked_at) or not _guard_time(evidence.valid_until):
            return 'recovery_time_invalid'
        if (evidence.checked_at.date() != self._session
                or evidence.valid_until.date() != self._session
                or evidence.checked_at > evidence.valid_until):
            return 'recovery_time_invalid'
        if evidence.checked_at > now:
            return 'recovery_from_future'
        if now > evidence.valid_until:
            return 'recovery_expired'
        return None

    def dispatch(self, name, *, now, quote_at, recovery, callback):
        # Do not queue a timestamp/quote snapshot while another handler runs.
        # Reject concurrent or recursive entry; the caller needs fresh inputs
        # for any later attempt. This is not a process/client-wide lock.
        if not self._lock.acquire(False):
            return DispatchResult(False, 'dispatch_busy', None)
        try:
            return self._dispatch(name, now=now, quote_at=quote_at,
                                  recovery=recovery, callback=callback)
        finally:
            self._lock.release()

    def _dispatch(self, name, *, now, quote_at, recovery, callback):
        if self._halted:
            return DispatchResult(False, 'instance_halted', None)
        if not _guard_name(name) or name not in self._checkpoints:
            return DispatchResult(False, 'checkpoint_unknown', None)
        if name in self._consumed:
            return DispatchResult(False, 'checkpoint_consumed', None)
        if not _guard_time(now):
            return DispatchResult(False, 'clock_invalid', None)
        if now.date() != self._session:
            return DispatchResult(False, 'session_mismatch', None)
        reason = self._recovery_reason(recovery, now)
        if reason is not None:
            return DispatchResult(False, reason, None)
        point = self._checkpoints[name]
        if now < point.at:
            return DispatchResult(False, 'too_early', None)
        if now - point.at > point.max_lateness:
            return DispatchResult(False, 'too_late', None)
        if point.requires_quote:
            if quote_at is None:
                return DispatchResult(False, 'quote_missing', None)
            if not _guard_time(quote_at):
                return DispatchResult(False, 'quote_time_invalid', None)
            if quote_at.date() != self._session:
                return DispatchResult(False, 'quote_session_mismatch', None)
            if quote_at > now:
                return DispatchResult(False, 'quote_from_future', None)
            if now - quote_at > point.max_quote_age:
                return DispatchResult(False, 'quote_expired', None)
        if not callable(callback):
            raise ValueError('business callback must be callable')
        self._consumed.add(name)
        try:
            value = callback()
        except BaseException:
            # An unknown side effect invalidates this whole in-memory runtime,
            # including other checkpoints. No local reset can prove recovery.
            self._halted = True
            raise
        return DispatchResult(True, 'callback_completed', value)
