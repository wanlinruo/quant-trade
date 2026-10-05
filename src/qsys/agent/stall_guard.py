"""主循环停滞检测：进程活着但不再推进时，让它自己死掉。

**为什么不是「给调用加超时」。** 2026-10-05 的实际故障里，代理卡在：

    Future.result()            concurrent/futures/_base.py:451   ← 无 timeout
    common_op_sync_with_seq    xtquant/xttrader.py:313
    query_stock_trades         xtquant/xttrader.py:670
    query_trades               qsys/agent/xtquant_adapter.py:67

xtquant 的同步调用是「发请求 + 等回调填 Future」，而它自己调 `Future.result()`
不带 timeout。miniQMT 08:45:04 自行重启后回调永远不来，Future 永不 resolve。
超时参数传不进 xtquant 内部，而卡在 `Future.result()` 的线程也杀不掉——所以
逐调用包超时既覆盖不全（六个 QMT 入口都能这样卡），又会每次泄漏一个线程。

**所以转换故障形态。** 循环里的 `except Exception` 对永久阻塞永远不触发，
心跳因此冻结，而进程活着——按「进程是否存在」判活的 Windows 看门狗对这种
故障结构性失明（它是 2026-09-18 为崩溃型故障造的）。让进程自己退出，就把一个
看不见的卡死变成了看门狗已经能处理的崩溃。

进程被强杀不会留下僵死锁：`single_instance` 用的是 OS 级文件锁
（Windows `msvcrt.locking`），进程消失时操作系统自动释放。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from time import monotonic


class StallGuard:
    """看守主循环的推进。

    主循环每轮调一次 `progress()`；看守线程发现距上次推进超过 `timeout_s`
    就调用 `on_stall(reason)`——生产里那个回调写日志后 `os._exit`。

    判据是「循环还在转」，不是「循环干成了事」：某一轮抛异常也算推进，因为
    那种故障由循环自己的 `consecutive_errors` 落闸逻辑负责，且它在日志里看得见。
    本类只管那种**什么都不说**的故障。
    """

    def __init__(
        self,
        timeout_s: float,
        on_stall: Callable[[str], None],
        *,
        interval_s: float = 5.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError(f"timeout_s 必须为正数，收到 {timeout_s}")
        self._timeout_s = timeout_s
        self._on_stall = on_stall
        self._interval_s = interval_s
        self._clock = clock
        self._last_progress = clock()
        self._fired = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None

    def progress(self) -> None:
        """主循环每轮调用一次。已判定停滞后不再重新武装。"""
        with self._lock:
            if self._fired:
                return
            self._last_progress = self._clock()

    def check(self) -> bool:
        """返回是否处于停滞。首次判定时调用 `on_stall`，只调一次。"""
        with self._lock:
            if self._fired:
                return True
            idle = self._clock() - self._last_progress
            if idle <= self._timeout_s:
                return False
            self._fired = True
        self._on_stall(
            f"主循环 {idle:.1f}s 没有推进（阈值 {self._timeout_s:.0f}s）"
        )
        return True

    def start(self) -> None:
        if self.thread is not None:
            raise RuntimeError("StallGuard 已经启动")
        self.thread = threading.Thread(
            target=self._watch, name="stall-guard", daemon=True
        )
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _watch(self) -> None:
        while not self._stop.wait(self._interval_s):
            if self.check():
                return
