"""策略基类。

所有钩子默认空实现——策略只覆盖自己关心的那几个。
ctx 显式传入，不使用全局变量。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class Strategy:
    def on_init(self, ctx: Any) -> None:
        """一次性初始化：声明参数、注册因子、订阅数据。"""

    def on_session_start(self, ctx: Any) -> None:
        """每日盘前：选股、算目标仓位。此时当日 bar 尚不可见。"""

    def on_auction(self, ctx: Any) -> None:
        """集合竞价结算后、开盘撮合前。

        此时当日**开盘价已确定**（竞价成交价即开盘价），但当日 bar 的
        high/low/close 仍不可见。在此提交的订单按当日开盘价成交。

        对应原策略的 `run_daily(buy, '09:26')`。
        """

    def on_checkpoint(self, ctx: Any, at: Any) -> None:
        """盘中检查点。`at` 是该检查点的时刻（datetime.time）。

        此时只有该时刻及更早的分钟线可见，当日 bar 尚不可见。
        在此提交的订单**当场按该时刻价格成交**，不排到次日开盘。
        """

    def on_bar(self, ctx: Any, bars: Mapping[Any, Any]) -> None:
        """主逻辑。此时当日 bar 已可见，下单将在下一交易日开盘成交。"""

    def on_fill(self, ctx: Any, fill: Any) -> None:
        """成交回报。"""

    def on_session_end(self, ctx: Any) -> None:
        """收盘后：记录、复盘。"""
