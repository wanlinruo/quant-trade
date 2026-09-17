"""A：行情偶发不可用不能让模拟账户永久停下；持续不可用或其他异常仍须失败关闭。"""

from datetime import date, datetime, timedelta

import pytest

from qsys.paper.quotes import QuoteTimeout, QuoteUnavailable
from qsys.paper.store import PaperStore
from qsys.web.workbench import WorkbenchStore
from scripts.run_paper import run_account_loop

SOURCE = """
from qsys.strategy.base import Strategy
class Demo(Strategy):
    pass
"""
TIMEOUT = "QMT 行情请求超时（1 个标的）"


@pytest.fixture
def account(tmp_path):
    path = tmp_path / "workbench.sqlite3"
    workbench = WorkbenchStore(path)
    strategy = workbench.create_strategy("demo", SOURCE)
    job = workbench.enqueue(strategy.id, {
        "start": "2026-01-01", "end": "2026-02-01", "cash": 100_000,
        "params": {}, "jq_compat": False,
    })
    workbench.claim_next()
    workbench.finish(job.id, "succeeded", run_name=job.id)
    store = PaperStore(path)
    return store, store.create(job.id, "模拟", 100_000, date.today()).id


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 17, 9, 31)

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


def _run(store, ident, steps, limit=timedelta(minutes=3)):
    """steps：每次 tick 依次抛出其中的异常；None 表示正常。用完后结束账户以退出循环。"""
    clock, pending, builds = Clock(), list(steps), []

    class Runtime:
        def __init__(self):
            builds.append(self)

        def tick(self):
            if not pending:
                store.transition(ident, "end")
                return
            step = pending.pop(0)
            if step is not None:
                raise step

    code = run_account_loop(store, ident, Runtime, poll=1.0, outage_limit=limit,
                            now=clock, sleep=clock.sleep)
    return code, len(builds)


def test_a_transient_quote_timeout_does_not_mark_the_account_error(account):
    store, ident = account
    code, builds = _run(store, ident, [QuoteTimeout(TIMEOUT), None])
    got = store.account(ident)
    assert code == 0
    assert got.status == "ended" and got.error == ""
    # 失败后从库里重建 runtime，丢掉事件半途被改过的内存状态
    assert builds == 2
    messages = [row["message"] for row in store.rows(ident, "logs")]
    assert any("行情暂不可用" in m for m in messages)
    assert any("行情已恢复" in m for m in messages)


def test_a_quote_outage_longer_than_the_limit_marks_the_account_error(account):
    store, ident = account
    code, _ = _run(store, ident, [QuoteTimeout(TIMEOUT)] * 400)
    got = store.account(ident)
    assert code == 1
    assert got.status == "error" and TIMEOUT in got.error


def test_a_non_quote_failure_still_fails_closed_immediately(account):
    store, ident = account
    code, builds = _run(store, ident, [ValueError("策略代码错误"), None])
    got = store.account(ident)
    assert code == 1
    assert got.status == "error" and got.error == "策略代码错误"
    assert builds == 1


def test_gateway_errors_and_timeouts_are_both_quote_unavailable():
    assert issubclass(QuoteTimeout, QuoteUnavailable)
