"""基础设施观测：问不出来要说问不出来，不能假装健康。"""

import subprocess

from qsys.ops import infra


def _runner(stdout="", code=0):
    def run(command):
        return subprocess.CompletedProcess(command, code, stdout, "")
    return run


def _explodes(exc):
    def run(command):
        raise exc
    return run


# ---- systemd ----------------------------------------------------------------

def test_failed_units_are_named():
    out = ("fwupd-refresh.service loaded failed failed Refresh fwupd metadata\n"
           "qsys-tushare-sync.service loaded failed failed Sync\n")

    assert infra.failed_units(_runner(out)) == (
        "fwupd-refresh.service", "qsys-tushare-sync.service")


def test_no_failed_units_is_an_empty_tuple_not_none():
    """「一个都没失败」和「问不出来」必须分得开。"""
    assert infra.failed_units(_runner("")) == ()


def test_an_unavailable_systemd_reports_unknown_not_healthy():
    assert infra.failed_units(_explodes(FileNotFoundError("systemctl"))) is None
    assert infra.failed_units(_runner("", code=1)) is None


def test_a_hung_systemctl_does_not_take_the_digest_down():
    assert infra.failed_units(_explodes(subprocess.TimeoutExpired("systemctl", 5))) is None


def test_systemctl_show_drops_empty_and_na_values():
    out = "ActiveState=active\nSubState=running\nResult=\nLastTriggerUSec=n/a\n"
    props = infra.systemctl_show("qsys-paper", runner=_runner(out))

    assert props == {"ActiveState": "active", "SubState": "running"}


def test_systemctl_show_is_empty_when_the_unit_is_unknown():
    assert infra.systemctl_show("nope", runner=_runner("", code=4)) == {}


# ---- 内存 --------------------------------------------------------------

def test_memory_uses_available_not_free(tmp_path):
    """MemFree 在健康机器上也常年很低（页缓存），拿它报警是天天狼来了。"""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16000000 kB\n"
                       "MemFree:          200000 kB\n"
                       "MemAvailable:   12000000 kB\n")
    reading = infra.memory(meminfo)

    assert reading is not None
    assert round(reading.total_gib) == 15
    assert reading.used_pct == 25


def test_memory_is_unknown_without_proc(tmp_path):
    assert infra.memory(tmp_path / "absent") is None


def test_memory_is_unknown_when_the_file_is_unusable(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       not-a-number kB\n")

    assert infra.memory(meminfo) is None
