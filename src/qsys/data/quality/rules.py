"""数据质量校验规则 1-3。

规则只做判定，不修数据。规则 4-6（交叉源比对、复权自洽、财务勾稽）属二期范围。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import polars as pl

from qsys.core.calendar import TradingCalendar

# 各板块日涨跌幅上限。ST 股的 5% 限制在一期不可得，故越界规则仅告警不阻断。
# bshare 沿用主板 10%。
LIMIT_BY_BOARD = {
    "main": 0.10,
    "gem": 0.20,
    "star": 0.20,
    "bse": 0.30,
    "bshare": 0.10,
}
_TOLERANCE = 1e-6


class Severity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Violation:
    rule: str
    severity: Severity
    symbol: str
    dt: date
    detail: str


def check_completeness(
    bars: pl.DataFrame,
    calendar: TradingCalendar,
    expected_symbols: Sequence[str],
    start: date,
    end: date,
) -> list[Violation]:
    """规则 1：区间内每个交易日、每只预期标的都应有 bar。"""
    sessions = calendar.sessions_in_range(start, end)
    violations: list[Violation] = []
    for symbol in expected_symbols:
        present = set(bars.filter(pl.col("symbol") == symbol)["dt"].to_list())
        for session in sessions:
            if session not in present:
                violations.append(
                    Violation(
                        rule="completeness",
                        severity=Severity.BLOCKING,
                        symbol=symbol,
                        dt=session,
                        detail="missing bar (needs a suspension record to explain)",
                    )
                )
    return violations


def check_price_limits(bars: pl.DataFrame, boards: dict[str, str]) -> list[Violation]:
    """规则 2：日涨跌幅不得超过所属板块上限。"""
    if bars.height == 0:
        return []
    frame = bars.sort(["symbol", "dt"]).with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev_close")
    )
    violations: list[Violation] = []
    for row in frame.iter_rows(named=True):
        prev = row["prev_close"]
        if prev is None or prev <= 0:
            continue
        change = abs(row["close"] / prev - 1.0)
        limit = LIMIT_BY_BOARD.get(boards.get(row["symbol"], "main"), 0.10)
        if change > limit + _TOLERANCE:
            violations.append(
                Violation(
                    rule="price_limit",
                    severity=Severity.WARNING,
                    symbol=row["symbol"],
                    dt=row["dt"],
                    detail=f"change {change:.4f} exceeds board limit {limit:.2f}",
                )
            )
    return violations


def check_internal_consistency(bars: pl.DataFrame) -> list[Violation]:
    """规则 3：OHLC 大小关系、价格为正、成交量非负。"""
    violations: list[Violation] = []
    for row in bars.iter_rows(named=True):
        symbol, dt = row["symbol"], row["dt"]
        o, h, l, c = row["open"], row["high"], row["low"], row["close"]

        if min(o, h, l, c) <= 0:
            violations.append(
                Violation("non_positive_price", Severity.BLOCKING, symbol, dt,
                          f"ohlc=({o}, {h}, {l}, {c})")
            )
        elif not (l <= min(o, c) and max(o, c) <= h and l <= h):
            violations.append(
                Violation("ohlc_order", Severity.BLOCKING, symbol, dt,
                          f"ohlc=({o}, {h}, {l}, {c})")
            )

        if row["volume"] < 0:
            violations.append(
                Violation("negative_volume", Severity.BLOCKING, symbol, dt,
                          f"volume={row['volume']}")
            )
    return violations
