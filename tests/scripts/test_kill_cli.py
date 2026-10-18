"""独立 kill switch 的命令行：影子核心与实盘核心是两套开关，不能只停一个。

2026-09-27 发现：脚本一直只操作实盘那套（`control:kill` / `var/KILL`），
而观察期里真正在跑的是影子核心（`shadow:control:kill` / `var/SHADOW_KILL`）。
`status` 会报「运行中」——报的却是停用中的实盘核心，比没查更危险。
"""

import pytest

from qsys.live.transport import MemoryTransport
from scripts import kill


@pytest.fixture
def at(tmp_path, monkeypatch):
    """把两套开关的本地标记落到临时目录，并接管 Redis。"""
    transport = MemoryTransport()
    monkeypatch.setattr(kill, "TARGETS", {
        "live": ("control:kill", tmp_path / "KILL"),
        "shadow": ("shadow:control:kill", tmp_path / "SHADOW_KILL"),
    })
    monkeypatch.setattr(kill, "_transport", lambda: (transport, None))
    monkeypatch.setattr(kill, "SHADOW_COMPONENT_DROPIN", tmp_path / "isolated.conf")
    monkeypatch.setattr(kill, "SHADOW_COMPONENT_KILL", tmp_path / "isolated/SHADOW_KILL")
    return tmp_path, transport


def _run(monkeypatch, *args) -> int:
    monkeypatch.setattr("sys.argv", ["kill.py", *args])
    return kill.main()


def test_stop_halts_both_cores_by_default(at, monkeypatch):
    """紧急时要的是全停；停掉没在跑的那个也无害，漏停在跑的那个才要命。"""
    tmp_path, _ = at

    assert _run(monkeypatch, "stop", "演练") == 0

    assert (tmp_path / "KILL").exists()
    assert (tmp_path / "SHADOW_KILL").exists()


def test_stop_can_target_one_core(at, monkeypatch):
    tmp_path, _ = at

    assert _run(monkeypatch, "stop", "只停影子", "shadow") == 0

    assert (tmp_path / "SHADOW_KILL").exists()
    assert not (tmp_path / "KILL").exists()


def test_status_reports_every_core(at, monkeypatch, capsys):
    """只报一个就可能查错对象——两个都报，才不会误以为系统在跑。"""
    tmp_path, _ = at
    (tmp_path / "SHADOW_KILL").write_text("演练停机")

    _run(monkeypatch, "status")

    out = capsys.readouterr().out
    assert "live" in out and "shadow" in out
    assert "运行中" in out and "已停机" in out
    assert "演练停机" in out


def test_resume_refuses_without_an_explicit_target(at, monkeypatch, capsys):
    """解除停机是危险方向，不接受顺手全开。"""
    tmp_path, _ = at
    (tmp_path / "SHADOW_KILL").write_text("x")

    assert _run(monkeypatch, "resume") == 2
    assert (tmp_path / "SHADOW_KILL").exists()
    assert "必须指明" in capsys.readouterr().err


def test_resume_clears_only_the_named_core(at, monkeypatch):
    tmp_path, _ = at
    _run(monkeypatch, "stop", "两个都停")

    assert _run(monkeypatch, "resume", "shadow") == 0

    assert not (tmp_path / "SHADOW_KILL").exists()
    assert (tmp_path / "KILL").exists()


def test_resume_rejects_an_unknown_target(at, monkeypatch):
    assert _run(monkeypatch, "resume", "paper") == 2


def test_isolated_shadow_local_stop_status_and_resume_without_redis(at, monkeypatch, capsys):
    from qsys.live.kill_switch import KillSwitch

    tmp_path, _ = at
    kill.SHADOW_COMPONENT_DROPIN.write_text("[Service]\n")
    # _transport 在 Redis 故障时使用同样的本地 MemoryTransport 兜底。
    isolated = KillSwitch(MemoryTransport(), key="shadow:control:kill",
                          local_path=kill.SHADOW_COMPONENT_KILL)
    assert _run(monkeypatch, "stop", "独立组件急停", "shadow") == 0
    assert isolated.is_engaged()
    assert not (tmp_path / "SHADOW_KILL").exists()
    assert _run(monkeypatch, "status") == 0
    assert "独立组件急停" in capsys.readouterr().out
    assert _run(monkeypatch, "resume", "shadow") == 0
    assert not isolated.is_engaged()


def test_isolated_shadow_kill_path_matches_production_command():
    import json
    import shlex
    from pathlib import Path

    specs = json.loads(Path("deploy/component-releases.json").read_text())
    command = next(line.removeprefix("ExecStart=") for line in
                   specs["components"]["shadow-core"]["dropin_lines"]
                   if line.startswith("ExecStart=") and line != "ExecStart=")
    args = shlex.split(command)
    assert kill.SHADOW_COMPONENT_KILL == Path(args[args.index("--kill-switch") + 1])
    assert str(kill.SHADOW_COMPONENT_DROPIN) == specs["components"]["shadow-core"]["dropin"]


def test_the_targets_match_what_the_cores_actually_use():
    """这张表是手工对齐的，抄一遍常量测不出走样——去对真正的来源。

    实盘那套对 LiveRuntime 的默认值，影子那套对 run_shadow 传进去的字面量。
    哪天有人改了核心却忘了改急停脚本，这里要红。
    """
    import inspect
    import re
    from pathlib import Path

    from qsys.live.runtime import LiveRuntime

    live_default = inspect.signature(LiveRuntime).parameters["local_kill_path"].default
    assert kill.TARGETS["live"] == ("control:kill", live_default)

    # runtime 按 shadow 标志切换键名，脚本里的影子键必须与之一致
    source = inspect.getsource(LiveRuntime.__init__)
    assert kill.TARGETS["shadow"][0] in source

    shadow_runner = Path(__file__).resolve().parents[2] / "src/qsys/live/shadow_cli.py"
    runner_source = shadow_runner.read_text(encoding="utf-8")
    default = re.search(
        r'add_argument\("--kill-switch", default="([^"]+)"\)', runner_source,
    )
    assert default is not None, "shadow_cli.py 不再声明 kill-switch 默认值了"
    assert "local_kill_path=Path(args.kill_switch)" in runner_source
    assert kill.TARGETS["shadow"][1] == Path(default.group(1))
