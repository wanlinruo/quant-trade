"""对外契约清单：前端类型由这里列出的模型生成（scripts/gen_frontend_contracts.py）。

新增或修改接口的响应模型后，必须登记到 EXPORTED 并重新生成前端类型；
测试会校验路由的响应模型都已登记、生成物与仓库一致。
"""

from __future__ import annotations

from qsys.webapp.contracts.base import Contract, ErrorBody
from qsys.webapp.contracts.market import MarketDisabled, MarketOverview
from qsys.webapp.contracts.shell import ShellStatus

EXPORTED: tuple[type[Contract], ...] = (
    ErrorBody,
    ShellStatus,
    MarketOverview,
    MarketDisabled,
)
