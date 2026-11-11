"""#601 F5：unknown 人工对账请求（执行器重启后自动“确认未下单”永久不可用）。"""
from datetime import timedelta
import json

from qsys.agent.qmt.reconcile import UnknownReconciler
from test_qmt_reconcile import CID, Rig, T, lost_dispatch


def request(rig, tmp_path, **override):
    folder = tmp_path / 'requests'
    folder.mkdir(exist_ok=True)
    body = dict(client_order_id=CID, outcome='absent', operator='maintainer',
                evidence='QMT 委托查询与当日成交查询均无该备注', requested_at=rig.now.isoformat())
    body.update(override)
    path = folder / f"req-{len(list(folder.iterdir()))}.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding='utf-8')
    return path


def reconciler(rig, d, tmp_path):
    return UnknownReconciler(d, rig.adapter, rig.mem, grace_s=60.0, clock=lambda: rig.now,
                             requests=tmp_path / 'requests')


def result(path):
    return json.loads(path.with_suffix('.result.json').read_text(encoding='utf-8'))


def restarted_unknown(rig, d):
    lost_dispatch(rig, d)
    rig.start_executor(rig.now + timedelta(seconds=5))   # executor restarted after creation
    rig.advance(125)                                     # past expiry + grace


def test_manual_absent_releases_only_after_two_fresh_empty_queries(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as d:
        restarted_unknown(rig, d)
        r = reconciler(rig, d, tmp_path)
        assert r.run_once() == []                 # automatic path refuses after restart
        path = request(rig, tmp_path)
        assert r.run_once() == []                 # first empty query
        rig.advance(3)
        assert r.run_once() == []                 # too soon
        rig.advance(3)
        assert r.run_once() == [(CID, 'absent')]
        assert not d.store.has_unresolved()
    assert result(path)['status'] == 'applied'
    assert not path.exists() and path.with_suffix('.done').exists()
    (reject,) = rig.rejects()
    assert reject.reason.startswith('reconciled_absent:manual operator=maintainer')
    assert 'QMT' not in reject.reason              # free text stays in the private result file
    assert [c for c in rig.api.calls if c[0] == 'passorder'] == []


def test_manual_absent_is_refused_when_the_order_exists(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as d:
        restarted_unknown(rig, d)
        path = request(rig, tmp_path)
        rig.api.order(CID)
        assert reconciler(rig, d, tmp_path).run_once() == [(CID, 'present')]
    assert result(path) == {**result(path), 'status': 'refused', 'reason': 'order_found'}
    assert rig.rejects() == []


def test_manual_absent_waits_for_expiry_and_fresh_heartbeat(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as d:
        lost_dispatch(rig, d)
        rig.start_executor(rig.now + timedelta(seconds=5))
        path = request(rig, tmp_path)
        r = reconciler(rig, d, tmp_path)
        rig.advance(30)                           # not yet expired
        assert r.run_once() == [] and r.run_once() == []
        assert path.exists() and not path.with_suffix('.result.json').exists()
        assert d.store.has_unresolved()


def test_manual_present_records_fact_without_release(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as d:
        restarted_unknown(rig, d)
        path = request(rig, tmp_path, outcome='present')
        assert reconciler(rig, d, tmp_path).run_once() == [(CID, 'present')]
        assert d.store.read(CID)['state'] == 'resolved_present'
    assert result(path)['status'] == 'applied' and rig.rejects() == []


def test_invalid_stale_or_unknownless_requests_are_refused_without_effect(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as d:
        restarted_unknown(rig, d)
        bad = request(rig, tmp_path, operator='bad name!')
        stale = request(rig, tmp_path, requested_at=(rig.now - timedelta(hours=2)).isoformat())
        other = request(rig, tmp_path, client_order_id='q261109009abcdef000000')
        extra = request(rig, tmp_path, note='x')
        reconciler(rig, d, tmp_path).run_once()
        assert d.store.has_unresolved()
    assert result(bad)['reason'] == 'invalid_request'
    assert result(extra)['reason'] == 'invalid_request'
    assert result(stale)['reason'] == 'request_expired'
    assert result(other)['reason'] == 'not_unknown'
    assert rig.rejects() == []


def test_manual_absent_across_days_is_refused(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as d:
        restarted_unknown(rig, d)
        rig.advance(24 * 3600)
        path = request(rig, tmp_path)
        reconciler(rig, d, tmp_path).run_once()
        assert d.store.has_unresolved()
    assert result(path)['reason'] == 'cross_day_requires_trade_evidence'
