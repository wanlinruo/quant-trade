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


# ---- 交易者视角：消息要能直接拿来做决定（2026-10-01 重做）----

from qsys.ops.notify import BUY, SELL, Position, Trade, TradeContext  # noqa: E402

BOUGHT = Trade("600036.SH", BUY, 300, 41.07)
SOLD = Trade("000001.SZ", SELL, 500, 12.30)
HELD = Position("600036.SH", 300, 41.07, 42.10)


def test_an_order_message_says_what_was_actually_bought():
    """「下单 2 笔」在手机上没法判断要不要干预——得说清标的、数量、价格、金额。"""
    _, body = compose("实盘", record(orders=1, phase="auction"),
                      TradeContext(fills=(BOUGHT,)))

    assert "600036.SH" in body and "300 股" in body
    assert "41.07" in body and "12,321" in body


def test_buying_and_selling_are_told_apart():
    """T+1 同一天可能既卖昨天的又买今天的；离场才是钱回来的时刻。"""
    buy, _ = compose("实盘", record(orders=1), TradeContext(fills=(BOUGHT,)))
    sell, _ = compose("实盘", record(orders=1), TradeContext(fills=(SOLD,)))
    both, _ = compose("实盘", record(orders=2), TradeContext(fills=(BOUGHT, SOLD)))

    assert "买入 1 只" in buy and "卖出" not in buy
    assert "卖出 1 只" in sell and "买入" not in sell
    assert "买入 1 只，卖出 1 只" in both


def test_without_detail_the_title_falls_back_to_a_count():
    title, _ = compose("实盘", record(orders=2))

    assert "下单 2 笔" in title


def test_fills_are_preferred_over_orders():
    """报成交优先于报委托：委托只是意图，成交才是事实。"""
    _, body = compose("实盘", record(orders=1),
                      TradeContext(orders=(SOLD,), fills=(BOUGHT,)))

    assert "600036.SH" in body and "000001.SZ" not in body


def test_a_reject_says_which_one_and_why():
    _, body = compose("实盘", record(rejects=1),
                      TradeContext(rejects=(("600036.SH", "可用资金不足"),)))

    assert "拒单 600036.SH：可用资金不足" in body


def test_the_close_reports_the_day_profit_and_loss():
    """收盘最该回答的是「今天赚了多少」。"""
    _, body = compose("模拟", record(phase="session_end", total_value=101_500.0),
                      TradeContext(prev_total_value=100_000.0))

    assert "当日盈亏 +1,500.00（+1.50%）" in body


def test_the_day_pnl_is_skipped_when_there_is_no_yesterday():
    _, body = compose("模拟", record(phase="session_end"), TradeContext())

    assert "当日盈亏" not in body


def test_the_close_lists_what_is_still_held():
    """T+1 策略晚上要知道手里是什么——明天开盘就要卖。"""
    _, body = compose("模拟", record(phase="session_end", position_count=1),
                      TradeContext(positions=(HELD,)))

    assert "600036.SH 300 股" in body
    assert "成本 41.07" in body and "现价 42.10" in body
    assert "+309" in body and "+2.51%" in body


# ---- 盘前就位：只在今天有事可做时打扰 ----

def test_the_premarket_message_is_sent_when_something_must_be_sold_today():
    bus, sink = _bus()
    sent = StrategyNotifier(bus, "模拟").on_run(
        record(phase="session_start"), TradeContext(positions=(HELD,)))

    assert sent is True
    assert "盘前就位" in sink.alerts[0].title
    assert "600036.SH" in sink.alerts[0].detail


def test_an_empty_morning_is_not_worth_waking_you_for():
    """空仓的早晨没有可反应的事，十分钟后的竞价决策已经说明策略活着。"""
    bus, sink = _bus()
    sent = StrategyNotifier(bus, "模拟").on_run(record(phase="session_start"))

    assert sent is False and sink.alerts == []


def test_a_premarket_failure_is_still_sent():
    bus, sink = _bus()
    sent = StrategyNotifier(bus, "模拟").on_run(
        record(phase="session_start", error="行情未就绪"))

    assert sent is True and sink.alerts[0].severity is Severity.CRITICAL


def test_the_old_two_fixed_messages_are_unchanged():
    """竞价与收盘仍然每天各一条，空仓日的消息量没有增加。"""
    assert should_notify(record(phase="auction")) is True
    assert should_notify(record(phase="session_end")) is True
    assert should_notify(record(phase="checkpoint")) is False
    assert should_notify(record(phase="bar")) is False


# ---- 内部对象 → 消息里的字段 ----

from qsys.core.portfolio import Position as PortfolioPosition  # noqa: E402
from qsys.core.trading import Side  # noqa: E402
from qsys.ops.notify import positions_of, trade_of  # noqa: E402


def test_a_fill_becomes_a_readable_line():
    trade = trade_of(Side.BUY, "600036.SH", 300, 41.07)

    assert trade.side == BUY
    assert "买入 600036.SH 300 股 @ 41.07（12,321 元）" == trade.as_line()


def test_a_market_order_without_a_price_records_zero_rather_than_inventing_one():
    assert trade_of(Side.SELL, "600036.SH", 100, None).price == 0.0


def test_positions_use_the_last_price_and_fall_back_to_cost():
    held = {"600036.SH": PortfolioPosition("600036.SH", 300, 300, 12_321.0),
            "000001.SZ": PortfolioPosition("000001.SZ", 500, 500, 6_150.0)}
    lines = positions_of(held, {"600036.SH": 42.10})

    assert [p.symbol for p in lines] == ["000001.SZ", "600036.SH"]
    assert lines[1].price == 42.10
    assert lines[0].price == lines[0].cost and lines[0].pnl == 0.0


def test_an_emptied_position_is_left_out():
    held = {"600036.SH": PortfolioPosition("600036.SH", 0, 0, 0.0)}

    assert positions_of(held, {}) == ()


def test_the_close_summarises_the_whole_day_not_the_last_event():
    """收盘事件自己没下单，但当天成交了——照抄事件摘要会写出自相矛盾的「无下单」。"""
    _, body = compose("实盘", record(phase="session_end", summary="持仓 0 只，无下单"),
                      TradeContext(fills=(BOUGHT, SOLD)))

    assert "今日成交 2 笔" in body
    assert "无下单" not in body


def test_a_day_without_trades_keeps_the_plain_summary():
    _, body = compose("实盘", record(phase="session_end", summary="持仓 0 只，无下单"),
                      TradeContext())

    assert "持仓 0 只，无下单" in body
