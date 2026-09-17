"""模拟交易用的交易日历：只认 Tushare trade_cal，不按星期几推断。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from qsys.core.calendar import AShareCalendar
from qsys.data.providers.tushare_client import TushareClient
from qsys.live.config import load_tushare_config

# 开盘事件时刻；此后恢复或创建的账户，当天已不是完整交易日
SESSION_START = time(9, 15)


def load_trading_calendar(config_path: str | None) -> AShareCalendar:
    cfg = load_tushare_config(config_path)
    today = date.today()
    rows = TushareClient(cfg.token).call("trade_cal", {
        "exchange": "SSE",
        "start_date": (today - timedelta(days=370)).strftime("%Y%m%d"),
        "end_date": (today + timedelta(days=370)).strftime("%Y%m%d"),
        "is_open": "1",
    }, "cal_date,is_open")
    # Tushare currently returns trade_cal rows newest-first.  Do not attach
    # correctness to provider ordering: the coverage check needs the actual
    # latest session, and AShareCalendar also benefits from normalized input.
    sessions = sorted(
        datetime.strptime(str(row["cal_date"]), "%Y%m%d").date()
        for row in rows if str(row.get("is_open", "1")) == "1"
    )
    if not sessions or sessions[-1] < today + timedelta(days=30):
        raise RuntimeError("Tushare 交易日历为空或未来覆盖不足 30 天")
    return AShareCalendar(sessions)


def first_complete_session(now: datetime, calendar: AShareCalendar) -> date:
    """`now` 之后第一个能从开盘事件跑起的交易日。

    节假日不是生效日：原实现只跳周末，国庆前恢复的账户会把生效日记成 10-01。
    """
    today = now.date()
    if calendar.is_trading_day(today) and now.time() < SESSION_START:
        return today
    return calendar.next_session(today)
