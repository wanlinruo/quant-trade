"""临时兼容入口；最终集成完成后随旧 ``qsys.web`` 包删除。"""

from qsys.ops.service_console import (
    WINDOWS_TASK_TIMEOUT,
    Cell,
    ServiceConsole,
    parse_schtasks_csv,
    windows_task_show,
)

__all__ = [
    "WINDOWS_TASK_TIMEOUT",
    "Cell",
    "ServiceConsole",
    "parse_schtasks_csv",
    "windows_task_show",
]
