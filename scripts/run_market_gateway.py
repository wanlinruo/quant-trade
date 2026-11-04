"""Windows-side read-only QMT market gateway for paper and live trading.

This process imports ``xtdata`` only.  It deliberately has no account id,
trader adapter, or order API, so the quote process cannot reach real funds.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as wall_time, timedelta
from typing import Callable, Sequence

import structlog

from qsys.live.config import load_deploy_config
from qsys.live.messages import MarketQuote
from qsys.live.redis_transport import RedisTransport
from qsys.ops.health import HeartbeatCadence, heartbeat_policy, trading_phase
from qsys.paper.quotes import MarketGateway, build_market_schedule

log = structlog.get_logger("market-gateway")


@dataclass
class TradingSessionCache:
    """Refresh QMT's current-day calendar until its delayed data is available.

    MiniQMT can return an empty trading-date result shortly after midnight and
    populate it later in the morning.  An empty first result must therefore
    not be treated as authoritative for the rest of the day.
    """

    refresh_interval: float = 300.0
    missing_today_retry_interval: float = 30.0
    sessions: list[date] = field(default_factory=list)
    day: date | None = None
    next_refresh: float = 0.0

    def refresh_if_due(
        self,
        wall: datetime,
        monotonic: float,
        provider: Callable[[date, date], list[date]],
    ) -> list[date]:
        today = wall.date()
        if self.day != today:
            self.day = today
            self.sessions = []
            self.next_refresh = 0.0
        if monotonic < self.next_refresh:
            return self.sessions

        # Set the retry deadline before calling QMT so a transient exception
        # cannot turn the 100 ms gateway loop into a calendar-query storm.
        self.next_refresh = monotonic + max(1.0, self.refresh_interval)
        # MiniQMT may not expose future sessions.  Starting the query at
        # ``today`` therefore yields an empty result on weekends/holidays and
        # incorrectly keeps health monitoring in conservative
        # ``calendar_unknown`` mode.  Recent past sessions prove that the
        # calendar source is available while today's absence still means the
        # market is closed.
        refreshed = provider(today - timedelta(days=40), today + timedelta(days=40))
        if refreshed:
            self.sessions = refreshed
            # A non-empty calendar is not necessarily current: after a long
            # holiday MiniQMT can expose historical sessions before adding
            # today.  Retry quickly around startup instead of preserving a
            # false off-hours cadence for the full five-minute cache period.
            clock = wall.time()
            opening_window = wall_time(8, 40) <= clock < wall_time(9, 30)
            if today.weekday() < 5 and today not in refreshed and opening_window:
                self.next_refresh = monotonic + max(
                    1.0, min(self.refresh_interval, self.missing_today_retry_interval)
                )
        return self.sessions


def qmt_provider(symbols):
    from xtquant import xtdata

    raw = xtdata.get_full_tick(list(symbols)) or {}
    out = {}
    for symbol in symbols:
        item = raw.get(symbol) or {}
        def direct(*names):
            for name in names:
                if name in item and item[name] is not None:
                    return item[name]
            raise KeyError("missing native quote field")
        try:
            stamp = float(direct("time", "timetag"))
            if stamp <= 0:
                continue
            at = datetime.fromtimestamp(stamp / 1000)
            price = float(direct("lastPrice", "last_price"))
            opening, high, low = (float(direct(name)) for name in ("open", "high", "low"))
            previous = float(direct("lastClose", "preClose"))
            volume, amount = direct("volume"), float(direct("amount"))
            if not all(math.isfinite(v) for v in (price, opening, high, low, previous, amount)) \
                    or isinstance(volume, bool) or not math.isfinite(float(volume)) \
                    or float(volume) != int(volume) or volume < 0 or amount < 0:
                continue
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            # 缺原生字段就留缺口；不以当前时间、其他价格或0伪造返回。
            continue
        out[symbol] = MarketQuote(
            symbol=symbol, at=at, price=price,
            open=opening, high=high, low=low, prev_close=previous,
            # QMT tick volume is lots (手); the cross-machine contract uses shares.
            volume=int(volume) * 100,
            amount=amount,
            upper_limit=_optional_float(item, "upStopPrice", "upperLimit"),
            lower_limit=_optional_float(item, "downStopPrice", "lowerLimit"),
            suspended=price <= 0,
            bid1=_level(item, "bidPrice", "bid", index=0),
            ask1=_level(item, "askPrice", "ask", index=0),
            bid1_volume=int(_level(item, "bidVol", "bidVolume", index=0)),
            ask1_volume=int(_level(item, "askVol", "askVolume", index=0)),
            tick_size=float(item.get("priceTick") or 0.01),
        )
    return out


def qmt_limit_detail(symbols):
    """QMT 合约详情里的当日涨跌停价，仅用于与 Tushare stk_limit 核对。"""
    from xtquant import xtdata

    out = {}
    for symbol in symbols:
        item = xtdata.get_instrument_detail(symbol) or {}
        up, down = item.get("UpStopPrice"), item.get("DownStopPrice")
        if up is not None and down is not None:
            out[symbol] = (float(up), float(down))
    return out


def qmt_market_overview_symbols(extra_symbols: Sequence[str] = ()):
    """原样取得本次请求集合，不用返回报价反推应有覆盖。"""
    from xtquant import xtdata

    # QMT 的全 A 股权威集合名是「沪深京A股」；「沪深A股」不含北交所，
    # 「北交所」不是有效集合名（2026-09-19 节点实测分别为 5218 / 0 只）。
    # 板块成分缓存可能滞后于新股上市（920298.BJ 上市三天后仍未入集合），
    # 还会混入 81/82/89/91 开头的指数或发行代码。因此当盘前 Tushare
    # stk_limit 已发布时，以它的全市场股票代码为准；价格仍全部读取 QMT。
    symbols = list(extra_symbols)
    if not symbols:
        symbols = list(xtdata.get_stock_list_in_sector("沪深京A股") or [])
    return sorted(set(symbols))


def qmt_market_overview(extra_symbols: Sequence[str] = ()):
    """Fetch the live A-share breadth universe plus the four headline indices."""
    symbols = qmt_market_overview_symbols(extra_symbols)
    return qmt_provider(sorted(set(symbols) | {"000001.SH", "399001.SZ", "399006.SZ", "000300.SH"}))


def published_limit_symbols(transport, streams, day: date) -> list[str]:
    """Return the current session's authoritative A-share symbol universe."""
    raw = transport.get_key(f"{streams.price_limits}:{day:%Y%m%d}")
    return sorted(json.loads(raw)) if raw else []


def qmt_trading_sessions(start: date, end: date) -> list[date]:
    from xtquant import xtdata

    stamps = xtdata.get_trading_dates(
        "SH", start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    ) or []
    return [datetime.fromtimestamp(float(stamp) / 1000).date() for stamp in stamps]


def _optional_float(item, *names):
    for name in names:
        value = item.get(name)
        if value not in (None, "", 0):
            return float(value)
    return None


def _level(item, *names, index=0):
    for name in names:
        value = item.get(name)
        if isinstance(value, (list, tuple)) and len(value) > index:
            return float(value[index] or 0)
        if value not in (None, "") and index == 0:
            return float(value or 0)
    return 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="QMT 只读实时行情网关")
    parser.add_argument("--config", default=None)
    parser.add_argument("--poll", type=float, default=0.1)
    parser.add_argument("--overview-interval", type=float, default=15.0)
    parser.add_argument("--calendar-refresh-interval", type=float, default=300.0)
    args = parser.parse_args()
    cfg = load_deploy_config(args.config)
    transport = RedisTransport.from_config(cfg)
    gateway = MarketGateway(transport, cfg.streams, qmt_provider)
    log.info("market_gateway.started")
    next_overview = 0.0
    next_schedule = 0.0
    active_until = 0.0
    heartbeat = HeartbeatCadence()
    limit_check_done = False
    calendar = TradingSessionCache(args.calendar_refresh_interval)
    while True:
        wall = datetime.now()
        monotonic = time.monotonic()
        sessions = calendar.sessions
        try:
            if calendar.day != wall.date():
                limit_check_done = False
            sessions = calendar.refresh_if_due(wall, monotonic, qmt_trading_sessions)
        except Exception as exc:
            log.error("market_gateway.calendar_error", error=str(exc))

        phase = trading_phase(wall, set(sessions) if sessions else None)
        policy = heartbeat_policy("market", phase, active=monotonic < active_until)
        try:
            if heartbeat.due(monotonic, policy.expected_interval_s):
                gateway.beat(expected_interval_s=policy.expected_interval_s, phase=phase)
        except Exception as exc:
            log.error("market_gateway.heartbeat_error", error=str(exc))

        try:
            handled = gateway.run_once()
            if handled:
                active_until = monotonic + 30.0
                # 盘外收到请求后立即宣告活跃，不等低频心跳的下一个截止点。
                heartbeat.next_due = 0.0
                gateway.beat(expected_interval_s=1, phase=phase)
            if not limit_check_done:
                limit_check_done = gateway.check_limits_step(qmt_limit_detail, wall.date())
                if limit_check_done:
                    log.info("market_gateway.limit_check_done")
                # 核对占用了一轮时间片，先把期间到达的报价请求服务掉再做概览：
                # 决策事件等的就是这些请求（2026-09-21 影子核心因此超时）。
                gateway.run_once()
            schedule = build_market_schedule(wall, sessions)
            if monotonic >= next_schedule:
                next_schedule = monotonic + 30.0
                gateway.publish_schedule(schedule)
            if schedule.is_open and monotonic >= next_overview:
                next_overview = monotonic + max(5.0, args.overview_interval)
                published = published_limit_symbols(transport, cfg.streams, wall.date())
                expected = qmt_market_overview_symbols(published)
                quotes = qmt_provider(sorted(set(expected) | {
                    "000001.SH", "399001.SZ", "399006.SZ", "000300.SH",
                }))
                gateway.publish_latest_quotes(quotes, now=wall)
                gateway.publish_overview(
                    quotes, now=wall, expected_symbols=expected,
                    universe_source="tushare.stk_limit" if published else "qmt.沪深京A股",
                    universe_session=wall.date() if published else None,
                )
                gateway.run_once()
        except Exception as exc:
            log.error("market_gateway.loop_error", error=str(exc))
        time.sleep(max(0.02, args.poll))


if __name__ == "__main__":
    sys.exit(main())
