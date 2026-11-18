"""契约基类与统一约定（总路线 v2 第 3 节）。

- 时间一律带时区：存储里的无时区时间按 Asia/Shanghai 解释，带时区的换算到该时区。
- 缺失值为 None，原因放在同级 ``missing`` 字典里；比例取 0–1，金额单位元。
- 契约 ``extra="forbid"``，多出来的字段是缺陷而不是“兼容”。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, BaseModel, ConfigDict

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def to_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=LOCAL_TZ)
    return value.astimezone(LOCAL_TZ)


Timestamp = Annotated[datetime, AfterValidator(to_aware)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ErrorDetail(Contract):
    code: str
    message: str


class ErrorBody(Contract):
    error: ErrorDetail
