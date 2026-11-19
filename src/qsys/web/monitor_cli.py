"""临时兼容入口；最终集成完成后随旧 ``qsys.web`` 包删除。"""

from qsys.webapp.cli import build_app, main

__all__ = ["build_app", "main"]
