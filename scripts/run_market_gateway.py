"""Windows-side read-only QMT market gateway for paper and live trading.

This process imports ``xtdata`` only.  It deliberately has no account id,
trader adapter, or order API, so the quote process cannot reach real funds.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Sequence

import structlog

from qsys.live.config import load_deploy_config
from qsys.live.messages import MarketQuote
from qsys.live.redis_transport import RedisTransport
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
        refreshed = provider(today, today + timedelta(days=40))
        if refreshed:
            self.sessions = refreshed
        return self.sessions


def qmt_provider(symbols):
    from xtquant import xtdata

    raw = xtdata.get_full_tick(list(symbols)) or {}
    out = {}
    for symbol in symbols:
        item = raw.get(symbol) or {}
        stamp = item.get("time") or item.get("timetag")
        at = datetime.fromtimestamp(float(stamp) / 1000) if stamp else datetime.now()
        price = float(item.get("lastPrice") or item.get("last_price") or 0)
        out[symbol] = MarketQuote(
            symbol=symbol, at=at, price=price,
            open=float(item.get("open") or 0), high=float(item.get("high") or price),
            low=float(item.get("low") or price),
            prev_close=float(item.get("lastClose") or item.get("preClose") or 0),
            # QMT tick volume is lots (手); the cross-machine contract uses shares.
            volume=int(item.get("volume") or 0) * 100,
            amount=float(item.get("amount") or 0),
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


def qmt_market_overview(extra_symbols: Sequence[str] = ()):
    """Fetch the live A-share breadth universe plus the four headline indices."""
    from xtquant import xtdata

    # QMT 的全 A 股权威集合名是「沪深京A股」；「沪深A股」不含北交所，
    # 「北交所」不是有效集合名（2026-09-19 节点实测分别为 5218 / 0 只）。
    # 板块成分缓存可能滞后于新股上市（920298.BJ 上市三天后仍未入集合），
    # 因此与盘前 Tushare stk_limit 的全市场代码取并集；价格仍全部读取 QMT。
    symbols = list(xtdata.get_stock_list_in_sector("沪深京A股") or [])
    symbols.extend(extra_symbols)
    symbols.extend(("000001.SH", "399001.SZ", "399006.SZ", "000300.SH"))
    return qmt_provider(sorted(set(symbols)))


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
    limit_check_done = False
    calendar = TradingSessionCache(args.calendar_refresh_interval)
    while True:
        try:
            gateway.run_once()
            gateway.beat()
            if not limit_check_done:
                limit_check_done = gateway.check_limits_step(qmt_limit_detail, datetime.now().date())
                if limit_check_done:
                    log.info("market_gateway.limit_check_done")
            wall = datetime.now()
            if calendar.day != wall.date():
                limit_check_done = False
            monotonic = time.monotonic()
            sessions = calendar.refresh_if_due(wall, monotonic, qmt_trading_sessions)
            schedule = build_market_schedule(wall, sessions)
            if monotonic >= next_schedule:
                next_schedule = monotonic + 30.0
                gateway.publish_schedule(schedule)
            if schedule.is_open and monotonic >= next_overview:
                next_overview = monotonic + max(5.0, args.overview_interval)
                gateway.publish_overview(qmt_market_overview(
                    published_limit_symbols(transport, cfg.streams, wall.date())
                ))
        except Exception as exc:
            log.error("market_gateway.loop_error", error=str(exc))
        time.sleep(max(0.02, args.poll))


if __name__ == "__main__":
    sys.exit(main())
