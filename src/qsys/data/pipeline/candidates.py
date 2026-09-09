"""候选股-日选择器：决定要下载哪些股在哪些日的分钟线。

这是唯一决定分钟线数据量的地方。实测（894 个交易日，2023-01 ~ 2026-09）：
  window=1   41,592 组 ≈ 1000 万根
  window=3  122,936 组 ≈ 2950 万根
  window=6  232,162 组 ≈ 5570 万根
对照：全市场全历史约 10.2 亿根。

**交易日推进，不是日历日推进**：首板在周五，候选日是下周一。
交易日序列直接取自 daily_flags 里实际出现过的日期——那本就是从真实日线
派生的，比另配一份日历更不容易走样。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import structlog

from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

MANIFEST_COLUMNS = ["symbol", "dt"]

_SCHEMA = {"symbol": pl.Utf8, "dt": pl.Date}


def candidate_stock_days(
    warehouse: Warehouse,
    start: date,
    end: date,
    *,
    window: int = 1,
) -> pl.DataFrame:
    """首板之后 `window` 个交易日的（标的, 日期）组合。"""
    if window < 1:
        raise ValueError(f"window must be >= 1: {window}")

    flags = WarehouseQuery(warehouse).daily_flags(start, end)
    empty = pl.DataFrame(schema=_SCHEMA)
    if flags.height == 0:
        return empty

    sessions = sorted(flags["dt"].unique().to_list())
    position = {d: i for i, d in enumerate(sessions)}

    boards = flags.filter(pl.col("is_first_board")).select(["symbol", "dt"])
    if boards.height == 0:
        return empty

    pairs: set[tuple[str, date]] = set()
    for symbol, day in zip(boards["symbol"].to_list(), boards["dt"].to_list()):
        origin = position[day]
        for step in range(1, window + 1):
            following = origin + step
            if following < len(sessions):
                pairs.add((symbol, sessions[following]))

    if not pairs:
        return empty

    ordered = sorted(pairs, key=lambda p: (p[1], p[0]))
    return pl.DataFrame(
        {"symbol": [p[0] for p in ordered], "dt": [p[1] for p in ordered]},
        schema=_SCHEMA,
    )


def write_manifest(frame: pl.DataFrame, path: Path) -> int:
    """写出 Windows 下载脚本的输入清单。返回行数。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.select(MANIFEST_COLUMNS).write_csv(path)
    log.info("manifest.written", path=str(path), rows=frame.height)
    return frame.height
