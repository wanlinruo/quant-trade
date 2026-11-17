"""盘中观测与盘后直取的对账（#648）：只报差异，不改写盘中观测或直取值。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time

import polars as pl


@dataclass(frozen=True)
class Observed:
    symbol: str
    height: int | None
    first_seal: datetime | None
    open_times: int


@dataclass(frozen=True)
class Mismatch:
    symbol: str
    field: str
    observed: object
    direct: object


def _clock(raw: str, on: datetime) -> datetime | None:
    """解析直取 ``limit_list_d.first_time``；``None`` 表示上游未给时刻。

    实测形态（#669，2022–2026 全量，见 limit-up-board-collector.md）：9 点为 5 位 ``HMMSS``
    （无前导零，如 ``92500``），10 点起为 6 位 ``HHMMSS``；另有 3 行上游给 ``"0"``，不是时刻，
    不比较。其他形态或越界时刻显式报错，不猜。
    """
    if raw == "0":
        return None
    if not (raw.isascii() and raw.isdigit() and len(raw) in (5, 6)):
        raise ValueError(f"unrecognised limit_list_d first_time {raw!r}")
    digits = raw.rjust(6, "0")
    try:
        clock = time(int(digits[:2]), int(digits[2:4]), int(digits[4:6]))
    except ValueError as exc:
        raise ValueError(f"out-of-range limit_list_d first_time {raw!r}") from exc
    return datetime.combine(on.date(), clock)


def reconcile(observed: Sequence[Observed], limit_list_day: pl.DataFrame,
              interval_s: float = 15.0) -> list[Mismatch]:
    direct = {r["symbol"]: r for r in limit_list_day.filter(pl.col("limit_type") == "U")
              .iter_rows(named=True)}
    out: list[Mismatch] = []
    for item in observed:
        row = direct.get(item.symbol)
        if row is None:
            continue
        if row["limit_times"] is not None and item.height != row["limit_times"]:
            out.append(Mismatch(item.symbol, "height", item.height, row["limit_times"]))
        if row["first_time"] and item.first_seal is not None:
            truth = _clock(row["first_time"], item.first_seal)
            if truth is not None and abs((item.first_seal - truth).total_seconds()) > interval_s:
                out.append(Mismatch(item.symbol, "first_seal", item.first_seal, truth))
        if row["open_times"] is not None and item.open_times != row["open_times"]:
            out.append(Mismatch(item.symbol, "open_times", item.open_times, row["open_times"]))
    return out
