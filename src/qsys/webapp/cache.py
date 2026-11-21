"""服务端单飞缓存（#662，设计文档 §10）。

多个标签页、多个用户同时请求时只读一次上游；读 Redis 的接口用它把读取频率压到
不快于数据发布间隔（AGENTS.md §2.1，#666）。读取失败不缓存，异常原样上抛。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class SingleFlightCache(Generic[T]):
    def __init__(self, fn: Callable[[], T], ttl_seconds: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._fn = fn
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._value: T | None = None
        self._at: float | None = None

    def get(self, *, stale_on_error: Callable[[T, Exception], T] | None = None) -> T:
        with self._lock:
            if self._at is not None and self._clock() - self._at < self._ttl:
                return self._value  # type: ignore[return-value]
            try:
                value = self._fn()
            except Exception as exc:
                if self._at is None or stale_on_error is None:
                    raise
                return stale_on_error(self._value, exc)  # type: ignore[arg-type]
            self._value, self._at = value, self._clock()
            return value
