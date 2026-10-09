"""Read one fresh QMT quote for the fixed commissioning instrument."""

from __future__ import annotations

import argparse
import json
import uuid

from qsys.live.commissioning import COMMISSIONING_SYMBOL
from qsys.live.config import load_deploy_config
from qsys.live.redis_transport import RedisTransport
from qsys.paper.quotes import QuoteClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/deploy.local.toml")
    args = parser.parse_args()
    cfg = load_deploy_config(args.config)
    quote = QuoteClient(
        RedisTransport.from_config(cfg), cfg.streams,
        consumer=f"commissioning-quote-{uuid.uuid4().hex[:8]}", timeout=5.0,
    ).request([COMMISSIONING_SYMBOL]).get(COMMISSIONING_SYMBOL)
    if quote is None:
        print(json.dumps({"symbol": COMMISSIONING_SYMBOL, "available": False}))
        return 1
    print(json.dumps({
        "symbol": quote.symbol, "at": quote.at.isoformat(), "price": quote.price,
        "bid1": quote.bid1, "ask1": quote.ask1,
        "bid1_volume": quote.bid1_volume, "ask1_volume": quote.ask1_volume,
        "volume": quote.volume, "suspended": quote.suspended,
        "tick_size": quote.tick_size,
    }, ensure_ascii=False))
    return 0 if not quote.suspended and quote.price > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
