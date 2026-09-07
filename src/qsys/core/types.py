"""市场中立的核心值类型。

本模块不得包含任何 A 股专有规则（涨跌停、T+1 等）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Market(StrEnum):
    SH = "SH"
    SZ = "SZ"
    BJ = "BJ"


class Frequency(StrEnum):
    DAILY = "1d"
    MIN1 = "1m"


@dataclass(frozen=True, slots=True)
class Symbol:
    code: str
    market: Market

    def __post_init__(self) -> None:
        if not (len(self.code) == 6 and self.code.isdigit()):
            raise ValueError(f"invalid code: {self.code!r}")

    def __str__(self) -> str:
        return f"{self.code}.{self.market.value}"

    @classmethod
    def parse(cls, text: str) -> Symbol:
        code, sep, market = text.partition(".")
        if not sep:
            raise ValueError(f"missing market suffix: {text!r}")
        return cls(code=code, market=Market(market.upper()))


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: Symbol
    dt: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
