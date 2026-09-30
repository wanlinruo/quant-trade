"""基础设施看护：报得准，而且不刷屏。"""

from qsys.ops.alerts import Severity
from qsys.ops.infra_watch import EdgeTracker, Finding, survey
from qsys.ops.storage import GIB


def _keys(findings):
    return {f.key for f in findings}


def _by_key(findings, key):
    return next(f for f in findings if f.key == key)


# ---- 判定 --------------------------------------------------------------------

def test_a_healthy_reading_says_nothing():
    assert survey({"disk_free_bytes": 30 * GIB, "cert_days": 86,
                   "failed_units": (), "kill_switch": None}) == []


def test_an_unknown_reading_stays_silent_rather_than_guessing():
    """实时通道只报确凿的坏消息；「问不出来」交给每晚质检和日报去说。"""
    assert survey({}) == []
    assert survey({"disk_free_bytes": None, "cert_days": None,
                   "failed_units": None}) == []


def test_disk_has_two_levels():
    low = survey({"disk_free_bytes": 6 * GIB})
    thin = survey({"disk_free_bytes": 15 * GIB})

    assert _by_key(low, "disk").severity is Severity.CRITICAL
    assert _by_key(thin, "disk").severity is Severity.WARNING


def test_the_certificate_warns_before_it_fails():
    assert _by_key(survey({"cert_days": 9}), "cert").severity is Severity.CRITICAL
    assert _by_key(survey({"cert_days": 20}), "cert").severity is Severity.WARNING
    assert survey({"cert_days": 21}) == []


def test_the_certificate_finding_says_where_to_look():
    assert "certbot" in _by_key(survey({"cert_days": 3}), "cert").detail


def test_each_failed_unit_gets_its_own_key():
    """两个单元先后坏掉，不能因为「已经报过磁盘之外的事」而漏掉第二个。"""
    findings = survey({"failed_units": ("a.service", "b.service")})

    assert _keys(findings) == {"unit:a.service", "unit:b.service"}


def test_an_engaged_kill_switch_is_critical():
    finding = _by_key(survey({"kill_switch": "2026-10-01 手工停机"}), "kill")

    assert finding.severity is Severity.CRITICAL
    assert "手工停机" in finding.detail


def test_an_unreachable_redis_is_critical():
    assert _by_key(survey({"redis_error": "timeout"}), "redis").severity is Severity.CRITICAL


# ---- 边沿触发 ----------------------------------------------------------------

BAD = Finding("disk", Severity.CRITICAL, "磁盘即将写满", "剩余 6.0 GiB")


def test_a_new_problem_is_announced_once():
    tracker = EdgeTracker()

    assert len(tracker.update([BAD])) == 1
    assert tracker.update([BAD]) == []
    assert tracker.update([BAD]) == []


def test_a_seven_day_outage_is_one_alert_not_two_thousand():
    """国庆七天 Windows 不开机，连续报警和不报警一样没用——人会开始无视它。"""
    tracker = EdgeTracker()
    sent = sum(len(tracker.update([BAD])) for _ in range(2016))

    assert sent == 1


def test_recovery_is_announced_too():
    tracker = EdgeTracker()
    tracker.update([BAD])
    notices = tracker.update([])

    assert len(notices) == 1
    assert notices[0].severity is Severity.INFO
    assert "已恢复" in notices[0].title


def test_a_problem_that_comes_back_is_announced_again():
    tracker = EdgeTracker()
    tracker.update([BAD])
    tracker.update([])

    assert len(tracker.update([BAD])) == 1


def test_two_problems_are_tracked_independently():
    tracker = EdgeTracker()
    other = Finding("cert", Severity.WARNING, "证书进入续期窗口", "剩余 20 天")
    tracker.update([BAD])

    notices = tracker.update([BAD, other])
    assert [n.title for n in notices] == ["证书进入续期窗口"]

    notices = tracker.update([other])
    assert [n.title for n in notices] == ["磁盘即将写满：已恢复"]
    assert tracker.open_keys == {"cert"}
