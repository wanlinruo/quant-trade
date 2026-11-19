"""Web 写入口需要的交易日生效日期计算。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from fastapi import HTTPException

from qsys.core.calendar import AShareCalendar
from qsys.paper.calendar import first_complete_session


def paper_activation(
    now: datetime,
    calendar: Callable[[], AShareCalendar] | None,
) -> date:
    """返回第一个完整交易日；拿不到直取日历就拒绝，不按工作日猜。"""
    try:
        if calendar is None:
            raise RuntimeError("未配置交易日历")
        return first_complete_session(now, calendar())
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"交易日历不可用，暂不能确定生效日：{exc}",
        ) from exc
