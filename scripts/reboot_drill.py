"""兼容入口：重启演练须显式指定模式和本次执行确认。"""

from qsys.ops.reboot_drill_cli import (
    SSH_TIMEOUT, SETTLE_SECONDS, _ssh, _powershell, _PROBE,
    probe_windows, probe_heartbeats, _alerts, _run, main,
)


if __name__ == "__main__":
    raise SystemExit(main())
