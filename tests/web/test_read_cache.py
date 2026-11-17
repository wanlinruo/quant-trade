"""服务端单飞缓存（#662，设计文档 §10）。"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from qsys.web.read_cache import SingleFlightCache


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_single_flight_cache_shares_one_call_within_ttl() -> None:
    calls = 0
    lock = threading.Lock()

    def fn() -> int:
        nonlocal calls
        with lock:
            calls += 1
        return calls

    clock = Clock()
    cache = SingleFlightCache(fn, ttl_seconds=15, clock=clock)
    with ThreadPoolExecutor(8) as ex:
        assert set(ex.map(lambda _: cache.get(), range(32))) == {1}
    assert calls == 1
    clock.t += 15.01
    assert cache.get() == 2
    assert calls == 2


def test_single_flight_cache_does_not_cache_errors() -> None:
    state = {"fail": True, "calls": 0}

    def fn() -> str:
        state["calls"] += 1
        if state["fail"]:
            raise RuntimeError("boom")
        return "ok"

    cache = SingleFlightCache(fn, ttl_seconds=15, clock=Clock())
    with pytest.raises(RuntimeError):
        cache.get()
    state["fail"] = False
    assert cache.get() == "ok"
    assert state["calls"] == 2
