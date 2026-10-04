"""重启演练的判定。重启生产节点这件事，判错一次代价很大。"""

from datetime import date

from qsys.ops.reboot_drill import assess, should_run

DRILL_DAY = date(2026, 10, 31)


def _ok(**over):
    base = {"boot_seconds_ago": 330.0, "session_name": "console", "rdp_logons": 0,
            "broker_endpoint": "58.34.120.85:56000", "agent_age": 1.0,
            "market_age": 20.0, "elapsed_seconds": 330.0}
    return {**base, **over}


def _failed(result, name):
    return next(f for f in result.failures if f.name == name)


# ---- 该不该跑 ----

def test_the_drill_runs_only_on_the_month_s_closed_day():
    assert should_run(DRILL_DAY, DRILL_DAY)[0] is True


def test_any_other_day_is_refused():
    """判错一次就是在交易时段把实盘链路打断 5 分钟。"""
    for day in (date(2026, 10, 30), date(2026, 11, 1), date(2026, 10, 1)):
        ran, why = should_run(day, DRILL_DAY)
        assert ran is False
        assert str(DRILL_DAY) in why


def test_a_human_can_force_it():
    assert should_run(date(2026, 10, 15), DRILL_DAY, force=True)[0] is True


# ---- 判据 ----

def test_a_clean_recovery_passes():
    result = assess(_ok())

    assert result.passed is True
    assert "全部通过" in result.summary() and "5.5 分钟" in result.summary()


def test_a_machine_that_never_rebooted_fails():
    """开机时刻没变，说明重启压根没发生——不能当成功。"""
    result = assess(_ok(boot_seconds_ago=86400.0))

    assert _failed(result, "机器已重启")


def test_a_login_screen_instead_of_a_session_fails():
    result = assess(_ok(session_name=""))

    assert "没有任何登录会话" in _failed(result, "Windows 自动登录").detail


def test_someone_rescuing_it_by_rdp_does_not_count_as_passing():
    """人进去点一下当然能好——那不是无人值守。"""
    result = assess(_ok(rdp_logons=1))

    assert "不算无人值守" in _failed(result, "全程无人介入").detail


def test_qmt_up_but_not_logged_in_fails():
    """2026-10-04 的病根：进程在跑，却一个券商连接都没有。"""
    result = assess(_ok(broker_endpoint=None))

    assert "没有任何券商连接" in _failed(result, "QMT 已连券商").detail


def test_a_dead_agent_fails_even_if_everything_else_is_fine():
    result = assess(_ok(agent_age=None))

    assert _failed(result, "影子盘代理心跳").detail == "无心跳"


def test_a_stale_heartbeat_is_not_a_live_one():
    result = assess(_ok(agent_age=600.0))

    assert _failed(result, "影子盘代理心跳")


def test_the_recovery_budget_allows_a_full_watchdog_cycle():
    """看门狗每 5 分钟才拉一次代理，最坏情况要等满一轮。"""
    from qsys.ops.reboot_drill import RECOVERY_BUDGET_SECONDS

    assert RECOVERY_BUDGET_SECONDS >= 330 + 300


def test_an_empty_reading_is_a_failure_not_a_pass():
    """什么都没采到时，`all([])` 为真——不能让它变成"通过"。"""
    result = assess({})

    assert result.passed is False
    assert len(result.failures) == 6
