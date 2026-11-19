"""临时兼容入口；最终集成完成后随旧 ``qsys.web`` 包删除。"""

from qsys.backtest.workbench import (
    BacktestJob,
    BacktestQueue,
    ManagedStrategy,
    WorkbenchStore,
    version_id_for,
)

__all__ = [
    "BacktestJob",
    "BacktestQueue",
    "ManagedStrategy",
    "WorkbenchStore",
    "version_id_for",
]
