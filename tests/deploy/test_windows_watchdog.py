"""看门狗脚本的两条硬约束 + 新判据的结构。

编码那条是 2026-09-18 实际栽过的：PowerShell 5.1 读没有 BOM 的 .ps1 时按 GBK
解码，中文注释会被读乱并导致整个脚本解析失败——而脚本解析失败时看门狗什么都
不做，且没人会知道。
"""

from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/windows/qsys_watchdog.ps1"


@pytest.fixture(scope="module")
def raw() -> bytes:
    return SCRIPT.read_bytes()


def test_the_script_keeps_its_utf8_bom(raw):
    assert raw.startswith(b"\xef\xbb\xbf"), (
        "缺 BOM：PowerShell 5.1 会按 GBK 读中文注释，脚本解析失败而看门狗静默停摆"
    )


def test_the_script_keeps_crlf_line_endings(raw):
    body = raw[3:]
    assert b"\r\n" in body
    assert body.replace(b"\r\n", b"").count(b"\n") == 0, "混有裸 LF 行尾"


def test_it_still_restarts_a_missing_process(raw):
    """崩溃型故障的那道判据不能在加新判据时被改掉。

    代理的停滞自检靠 os._exit 退出，退出之后正是这道判据把它拉回来。
    """
    text = raw.decode("utf-8-sig")
    assert "if (-not $running) {" in text
    assert "未在运行" in text


def test_it_also_checks_the_heartbeat_when_the_process_exists(raw):
    """2026-10-05 的卡死型故障：进程在，所以旧判据直接 continue 了。"""
    text = raw.decode("utf-8-sig")
    assert "check_agent_alive.py" in text
    assert "Heartbeat = 'agent'" in text


def test_an_unreadable_heartbeat_does_not_restart_anything(raw):
    """Redis 读不到不等于代理死了：重启一个好进程比留着一个死进程更坏。"""
    text = raw.decode("utf-8-sig")
    assert "$code -eq 4" in text
    assert text.index("$code -eq 4") < text.index("Stop-Process"), (
        "不可达分支必须在杀进程之前返回"
    )


def test_a_wedged_process_is_killed_before_the_task_is_retriggered(raw):
    """不杀就会和新实例抢同一把单实例锁，新实例起不来。"""
    text = raw.decode("utf-8-sig")
    assert text.index("Stop-Process") < text.index("Start-Target $target '卡死已清除'")


def test_the_heartbeat_threshold_leaves_room_for_the_agent_to_self_exit(raw):
    """代理自检 60s 退出；看门狗 120s 才动手，免得抢在它自己退出前面。"""
    text = raw.decode("utf-8-sig")
    assert "MaxAge = 120" in text
