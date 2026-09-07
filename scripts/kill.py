"""独立 kill switch —— 不依赖 Web、不依赖核心进程。

即使 Web 服务挂了、核心进程崩了，只要能运行这个脚本就能停机。
连 Redis 都连不上时，它仍会写下本地停机标记。

用法：
  uv run python scripts/kill.py stop "原因"
  uv run python scripts/kill.py status
  uv run python scripts/kill.py resume
"""

from __future__ import annotations

import sys
from pathlib import Path

LOCAL_KILL_FILE = Path("var/KILL")


def _switch():
    from qsys.live.kill_switch import KillSwitch
    from qsys.live.transport import MemoryTransport

    try:
        from qsys.live.config import load_deploy_config
        from qsys.live.redis_transport import RedisTransport

        transport = RedisTransport.from_config(load_deploy_config())
        transport.get_key("control:kill")  # 探活
    except Exception as exc:  # Redis 不可达也必须能停机
        print(f"[warn] Redis 不可用（{exc}），仅使用本地停机标记", file=sys.stderr)
        transport = MemoryTransport()

    return KillSwitch(transport, local_path=LOCAL_KILL_FILE)


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in {"stop", "resume", "status"}:
        print(__doc__)
        return 2

    action = sys.argv[1]
    switch = _switch()

    if action == "stop":
        reason = sys.argv[2] if len(sys.argv) > 2 else "manual"
        switch.engage(reason)
        print(f"已停机: {reason}")
    elif action == "resume":
        switch.release()
        print("已恢复")
    else:
        engaged = switch.is_engaged()
        print(f"状态: {'已停机' if engaged else '运行中'}")
        if engaged:
            print(f"原因: {switch.reason()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
