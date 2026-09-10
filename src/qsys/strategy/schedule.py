"""策略自己声明调度。对应聚宽的 `run_daily(func, '09:26')`。

**为什么要它**：调度是策略的一部分，不是运行器的。检查点改到 14:40，
应该改策略文件，而不是改跑它的脚本——否则同一个策略在不同 runner 下
行为不同，这正是「多系统拼接脆弱」的老毛病。

用法（在 `on_init` 里）：

    def on_init(self, ctx):
        ctx.schedule.auction("09:30")
        ctx.schedule.checkpoint("11:25", "14:25", "14:50")

**不声明就沿用引擎构造时给的值**，老策略与老调用方式一字不动。
"""

from __future__ import annotations

from datetime import time


def parse_time(value: str | time) -> time:
    """接受 `time(9, 30)` 或 `"09:30"` / `"09:30:00"`。"""
    if isinstance(value, time):
        return value
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise ValueError(f"时刻要写成 HH:MM 或 HH:MM:SS，收到 {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    second = int(parts[2]) if len(parts) == 3 else 0
    try:
        return time(hour, minute, second)
    except ValueError as exc:
        raise ValueError(f"不是合法时刻: {value!r}") from exc


class Schedule:
    """`on_init` 期间的调度收集器。声明一次，之后只读。"""

    def __init__(self) -> None:
        self._auction_at: time | None = None
        self._checkpoints: list[time] = []
        self._declared = False

    def auction(self, at: str | time) -> None:
        """集合竞价结算后的下单时刻。在此提交的单按当日开盘价成交。"""
        self._auction_at = parse_time(at)
        self._declared = True

    def checkpoint(self, *ats: str | time) -> None:
        """盘中检查点。在此提交的单**当场按该时刻价格成交**，不排到次日。"""
        for at in ats:
            moment = parse_time(at)
            if moment not in self._checkpoints:
                self._checkpoints.append(moment)
        self._declared = True

    @property
    def declared(self) -> bool:
        """策略有没有声明过。没有就沿用引擎构造时给的值。"""
        return self._declared

    @property
    def auction_at(self) -> time | None:
        return self._auction_at

    @property
    def checkpoints(self) -> tuple[time, ...]:
        return tuple(sorted(self._checkpoints))

    def __repr__(self) -> str:
        return (
            f"Schedule(auction_at={self._auction_at}, "
            f"checkpoints={self.checkpoints})"
        )
