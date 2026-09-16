"""券商兼容的短委托编号。"""

from __future__ import annotations

import hashlib
from datetime import date


def live_order_id(strategy_id: str, trading_day: date, event: str, sequence: int) -> str:
    """生成不超过 24 字节、确定且便于按交易日识别的 client order id。"""
    source = f"{strategy_id}|{trading_day.isoformat()}|{event}|{sequence}"
    digest = hashlib.blake2s(source.encode(), digest_size=6).hexdigest()
    return f"q{trading_day:%y%m%d}{sequence:03d}{digest}"[:24]
