"""临时兼容入口；最终集成完成后随旧 ``qsys.web`` 包删除。"""

from qsys.webapp.live_kill_status import KillSource, LiveKillReader, LiveKillStatus

__all__ = ["KillSource", "LiveKillReader", "LiveKillStatus"]
