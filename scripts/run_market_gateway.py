"""Windows-side read-only QMT market gateway for paper trading.

This process imports ``xtdata`` only.  It deliberately has no account id,
trader adapter, or order API, so paper trading cannot reach real funds.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import structlog

from qsys.live.config import load_deploy_config
from qsys.live.messages import MarketQuote
from qsys.live.redis_transport import RedisTransport
from qsys.paper.quotes import MarketGateway

log = structlog.get_logger("market-gateway")


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
        )
    return out


def qmt_market_overview():
    """Fetch the live A-share breadth universe plus the four headline indices."""
    from xtquant import xtdata

    symbols = list(xtdata.get_stock_list_in_sector("沪深A股") or [])
    symbols.extend(("000001.SH", "399001.SZ", "399006.SZ", "000300.SH"))
    return qmt_provider(sorted(set(symbols)))


def _optional_float(item, *names):
    for name in names:
        value = item.get(name)
        if value not in (None, "", 0):
            return float(value)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="QMT 只读实时行情网关")
    parser.add_argument("--config", default=None)
    parser.add_argument("--poll", type=float, default=0.1)
    parser.add_argument("--overview-interval", type=float, default=15.0)
    args = parser.parse_args()
    cfg = load_deploy_config(args.config)
    gateway = MarketGateway(RedisTransport.from_config(cfg), cfg.streams, qmt_provider)
    log.info("market_gateway.started")
    next_overview = 0.0
    while True:
        try:
            gateway.run_once()
            gateway.beat()
            now = time.monotonic()
            if now >= next_overview:
                next_overview = now + max(5.0, args.overview_interval)
                gateway.publish_overview(qmt_market_overview())
        except Exception as exc:
            log.error("market_gateway.loop_error", error=str(exc))
        time.sleep(max(0.02, args.poll))


if __name__ == "__main__":
    sys.exit(main())
