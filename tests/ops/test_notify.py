"""策略通知：推什么、不推什么。

三个引擎每天各 7 个事件，全推是骚扰；推少了又看不出策略在不在跑。
这条线划在哪，是这个模块唯一要紧的事。
"""

from datetime import date, datetime

import pytest

from qsys.ops.alerts import AlertBus, RecordingAlertSink, Severity
from qsys.ops.notify import StrategyNotifier, compose, should_notify
from qsys.ops.strategy_log import RunRecord

T = datetime(2026, 9, 28, 9, 25, 10)


def record(**kw) -> RunRecord:
    base = dict(
        event_key="2026-09-28:checkpoint:11:25:00", session=date(2026, 9, 28),
        phase="checkpoint", started_at=T, finished_at=T, duration_ms=10,
        cash=100_000.0, market_value=0.0, total_value=100_000.0,
        position_count=0, candidates=None, routed=None, passed=None,
        orders=0, rejects=0, summary="持仓 0 只，无下单",
    )
    base.update(kw)
    return RunRecord(**base)


def _bus():
    sink = RecordingAlertSink()
    return AlertBus([sink]), sink


# ---- 推 ----

def test_an_order_is_worth_telling_you_about():
    assert should_notify(record(orders=1, summary="买入 1 只")) is True


def test_a_reject_is_worth_telling_you_about():
    assert should_notify(record(rejects=1)) is True


def test_an_error_is_always_reported():
    assert should_notify(record(error="行情超时")) is True


def test_the_auction_is_reported_every_day_even_with_no_trade():
    """即使不交易也推：这是每天确认策略还活着的那条消息。"""
    assert should_notify(record(phase="auction", summary="候选 36 只 → 通过 0 只")) is True


def test_the_close_is_reported_every_day():
    assert should_notify(record(phase="session_end")) is True


# ---- 不推 ----

@pytest.mark.parametrize("phase", ["session_start", "checkpoint", "bar"])
def test_a_quiet_routine_event_is_not_worth_a_message(phase):
    """一天 7 个事件里这几个最常见，没买卖就不该打扰。"""
    assert should_notify(record(phase=phase)) is False


# ---- 消息内容 ----

def test_the_title_says_what_happened():
    title, _ = compose("实盘", record(orders=2, rejects=1))

    assert "实盘" in title and "下单 2 笔" in title and "拒单 1 笔" in title


def test_the_body_carries_the_funnel_and_the_account():
    _, body = compose("影子盘", record(
        phase="auction", summary="候选 36 只 → 入档 1 只 → 闸门通过 0 只",
        reasons={"入档前·不在低开/高开价格带": 35},
    ))

    assert "候选 36 只" in body
    assert "入档前·不在低开/高开价格带 35" in body
    assert "100,000.00" in body


def test_an_error_message_leads_with_the_error():
    title, body = compose("实盘", record(error="reconcile: INCONSISTENT"))

    assert "出错" in title
    assert "reconcile: INCONSISTENT" in body


# ---- 分级与静默 ----

def test_severity_matches_how_bad_it_is():
    bus, sink = _bus()
    notifier = StrategyNotifier(bus, "实盘")

    notifier.on_run(record(error="炸了"))
    notifier.on_run(record(rejects=1))
    notifier.on_run(record(orders=1))

    assert [a.severity for a in sink.alerts] == [
        Severity.CRITICAL, Severity.WARNING, Severity.INFO,
    ]


def test_no_webhook_means_silence_not_an_error():
    """没填通知链接的账户照常跑，不能因为没配就报错。"""
    notifier = StrategyNotifier(None, "模拟一号")

    assert notifier.on_run(record(orders=1)) is False


def test_a_broken_sink_never_breaks_the_caller():
    """通知是旁路：发送失败绝不能连累交易。"""
    class Broken:
        def send(self, alert):
            raise RuntimeError("企微挂了")

    notifier = StrategyNotifier(AlertBus([Broken()]), "实盘")

    assert notifier.on_run(record(orders=1)) is True   # 不抛
