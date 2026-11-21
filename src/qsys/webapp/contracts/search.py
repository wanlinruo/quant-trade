"""全局搜索契约：只返回可跳转的脱敏索引，不返回业务原文或凭据。"""

from __future__ import annotations

from typing import Literal

from qsys.webapp.contracts.base import Contract


class SearchItem(Contract):
    kind: Literal["委托", "服务", "策略", "数据表", "指标", "告警", "节点"]
    key: str
    label: str
    hint: str
    page: str
    params: dict[str, str]


class SearchIndex(Contract):
    schema_version: Literal[1] = 1
    items: list[SearchItem]
    missing: dict[str, str]
