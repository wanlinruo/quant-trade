"""健康观察仍使用的 Windows 任务只读查询契约。"""

from __future__ import annotations

import base64
import subprocess

from qsys.ops.service_console import (
    WINDOWS_TASK_TIMEOUT,
    parse_schtasks_csv,
    windows_task_show,
)

ZH_TASK_CSV = (
    '"主机名","任务名","下次运行时间","状态","登录模式","上次运行时间","上次结果",'
    '"创建者","要运行的任务"\n'
    '"WIN","\\\\qsys-watchdog","2026/9/26 23:24:00","就绪","交互式/后台",'
    '"2026/9/26 23:19:00","0","admin","powershell"\n'
)


def test_windows_task_query_uses_read_only_encoded_powershell(monkeypatch, tmp_path):
    def fake_run(args, **kwargs):
        script = base64.b64decode(args[-1]).decode("utf-16le")
        assert "schtasks /query /tn 'qsys-watchdog' /fo CSV /v" in script
        assert "/run" not in script and "/change" not in script
        return subprocess.CompletedProcess(
            args,
            0,
            ZH_TASK_CSV.encode("utf-8"),
            b"\xd5\xfd\xd4\xda",
        )

    monkeypatch.setattr("qsys.ops.service_console.subprocess.run", fake_run)
    assert windows_task_show("qsys-watchdog", host="win.example", key=tmp_path / "key") == {
        "State": "Ready",
        "LastTaskResult": 0,
        "LastRunTime": "2026-09-26T23:19:00",
    }


def test_localised_and_english_schtasks_output_is_parsed_by_position():
    assert parse_schtasks_csv(ZH_TASK_CSV) == {
        "State": "Ready",
        "LastTaskResult": 0,
        "LastRunTime": "2026-09-26T23:19:00",
    }
    english = (
        '"HostName","TaskName","Next Run Time","Status","Logon Mode",'
        '"Last Run Time","Last Result","Author","Task To Run"\n'
        '"WIN","\\\\qsys-watchdog","N/A","Disabled","Interactive only",'
        '"11/30/1999 12:00:00 AM","267011","admin","powershell"\n'
    )
    got = parse_schtasks_csv(english)
    assert got["State"] == "Disabled"
    assert got["LastTaskResult"] == 267011


def test_unparseable_output_fails_unknown_and_timeout_allows_busy_node():
    assert parse_schtasks_csv("") == {}
    assert parse_schtasks_csv("错误: 系统找不到指定的文件。") == {}
    assert parse_schtasks_csv(ZH_TASK_CSV.replace('"0","admin"', '"—","admin"')) == {}
    assert WINDOWS_TASK_TIMEOUT >= 15.0
