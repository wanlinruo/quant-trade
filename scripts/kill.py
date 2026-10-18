"""独立 kill switch —— 不依赖 Web、不依赖核心进程。

即使 Web 服务挂了、核心进程崩了，只要能运行这个脚本就能停机。
连 Redis 都连不上时，它仍会写下本地停机标记。

**实盘核心与影子核心用的是两套开关**（Redis 键与本地文件都不同），所以：

- ``status`` 两个都报——查了却查错对象，比没查更危险；
- ``stop`` 默认两个都停——紧急时要的是全停，停掉没在跑的那个也无害；
- ``resume`` 必须指名——解除停机是危险方向，不接受"顺手全开"。

用法：
  uv run python scripts/kill.py stop "原因"          # 两个都停
  uv run python scripts/kill.py stop "原因" shadow   # 只停影子
  uv run python scripts/kill.py status
  uv run python scripts/kill.py resume live
  uv run python scripts/kill.py resume shadow
"""

from __future__ import annotations

import sys
from pathlib import Path

# 名称 -> (Redis 键, 本地标记)。取值与 runtime.py、run_shadow.py 必须一致，
# 由 tests/scripts/test_kill_cli.py 直接对着那两处校验，改一边忘另一边会红。
TARGETS: dict[str, tuple[str, Path]] = {
    "live": ("control:kill", Path("var/KILL")),
    "shadow": ("shadow:control:kill", Path("var/SHADOW_KILL")),
}

# 固定 installer 在首次激活时写入该 drop-in；存在时使用独立组件的本地急停路径。
SHADOW_COMPONENT_DROPIN = Path(
    "/etc/systemd/system/qsys-shadow-core.service.d/zz-isolated-release.conf"
)
SHADOW_COMPONENT_KILL = Path("/home/ubuntu/qsys/var/shadow-core/SHADOW_KILL")


def _transport():
    """拿到 Redis；连不上也要能继续，本地标记才是保底的那条路。"""
    from qsys.live.transport import MemoryTransport

    try:
        from qsys.live.config import load_deploy_config
        from qsys.live.redis_transport import RedisTransport

        cfg = load_deploy_config()
        transport = RedisTransport.from_config(cfg)
        transport.get_key("control:kill")  # 探活
        return transport, cfg
    except Exception as exc:  # Redis 不可达也必须能停机
        print(f"[warn] Redis 不可用（{exc}），仅使用本地停机标记", file=sys.stderr)
        return MemoryTransport(), None


def _switch(name: str, transport):
    from qsys.live.kill_switch import KillSwitch

    key, path = TARGETS[name]
    if name == "shadow" and SHADOW_COMPONENT_DROPIN.is_file():
        path = SHADOW_COMPONENT_KILL
    return KillSwitch(transport, key=key, local_path=path)


def _announce(transport, cfg, action: str, reason: str) -> None:
    """同时给执行代理发一条控制指令；Redis 不可用时跳过（本地标记已生效）。"""
    if cfg is None:
        return
    from datetime import datetime

    from qsys.live.messages import ControlCommand

    transport.append(
        cfg.streams.control,
        ControlCommand(action=action, at=datetime.now(), reason=reason).to_payload(),
    )


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] not in {"stop", "resume", "status"}:
        print(__doc__)
        return 2
    action = argv[0]

    if action == "status":
        transport, _ = _transport()
        for name in TARGETS:
            switch = _switch(name, transport)
            engaged = switch.is_engaged()
            print(f"{name:7} {'已停机' if engaged else '运行中'}"
                  + (f"  原因: {switch.reason()}" if engaged else ""))
        return 0

    if action == "resume":
        # 恢复必须指名，不接受默认值：解除停机是危险方向
        name = argv[1] if len(argv) > 1 else ""
        if name not in TARGETS:
            print(f"resume 必须指明恢复哪一个：{' / '.join(TARGETS)}", file=sys.stderr)
            return 2
        transport, cfg = _transport()
        _switch(name, transport).release()
        _announce(transport, cfg, "resume", "manual")
        print(f"已恢复: {name}")
        return 0

    # stop：默认全停
    reason = argv[1] if len(argv) > 1 else "manual"
    names = [argv[2]] if len(argv) > 2 and argv[2] in TARGETS else list(TARGETS)
    transport, cfg = _transport()
    for name in names:
        _switch(name, transport).engage(reason)
    _announce(transport, cfg, "kill", reason)
    print(f"已停机: {'、'.join(names)}（{reason}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
