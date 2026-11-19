"""临时兼容入口；最终集成完成后随旧 ``qsys.web`` 包删除。"""

from qsys.research.job_console import (
    ResearchJobConsole,
    ResearchJobView,
    ResearchSubmissionProfile,
)

__all__ = [
    "ResearchJobConsole",
    "ResearchJobView",
    "ResearchSubmissionProfile",
]
