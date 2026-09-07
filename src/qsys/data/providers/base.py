"""数据源适配器抽象。

上层只依赖这些接口，免费源接口变更时可整体换源。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import date

import polars as pl

_SH_PREFIXES = ("60", "68", "51", "58")
_SZ_PREFIXES = ("00", "30", "15", "16")
_BJ_PREFIXES = ("43", "83", "87", "92")

_CODE_RE = re.compile(r"(\d{6})")


def normalize_symbol(raw_code: str) -> str:
    """六位裸码或带前后缀的代码 → 规范形式 '600036.SH'。"""
    match = _CODE_RE.search(raw_code)
    if match is None:
        raise ValueError(f"cannot infer market: {raw_code!r}")
    code = match.group(1)
    prefix = code[:2]
    if prefix in _SH_PREFIXES:
        return f"{code}.SH"
    if prefix in _SZ_PREFIXES:
        return f"{code}.SZ"
    if prefix in _BJ_PREFIXES:
        return f"{code}.BJ"
    raise ValueError(f"cannot infer market: {raw_code!r}")


class BarsProvider(ABC):
    name: str

    @abstractmethod
    def fetch_daily_bars(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        """返回列：dt, open, high, low, close, volume, amount（不复权）。"""


class ReferenceProvider(ABC):
    name: str

    @abstractmethod
    def fetch_instruments(self) -> pl.DataFrame:
        """返回列：symbol, name, market, list_date, delist_date, board。"""

    @abstractmethod
    def fetch_sessions(self, start: date, end: date) -> list[date]:
        """返回区间内的交易日列表。"""

    @abstractmethod
    def fetch_corporate_actions(self, symbol: str) -> pl.DataFrame:
        """返回列：ex_date, cash_per_share, stock_per_share, rights_per_share, rights_price。"""
