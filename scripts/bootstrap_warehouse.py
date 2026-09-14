"""用真实免费数据源初始化仓库并跑质量闸门。

用法：uv run python scripts/bootstrap_warehouse.py
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import structlog

from qsys.core.calendar import AShareCalendar
from qsys.data.pipeline.adjust import build_adj_factors, build_corporate_actions
from qsys.data.pipeline.bars import ingest_daily_bars
from qsys.data.pipeline.instruments import build_instruments
from qsys.data.providers.akshare_provider import AkshareProvider
from qsys.data.providers.mootdx_provider import MootdxProvider
from qsys.data.quality.report import QualityReport, run_quality_checks
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

SYMBOLS = ["600036.SH", "000001.SZ", "300750.SZ"]


def bootstrap_warehouse(
    root: Path, symbols: Sequence[str], start: date, end: date
) -> QualityReport:
    warehouse = Warehouse(root)
    reference = AkshareProvider()
    bars_provider = MootdxProvider()

    calendar = AShareCalendar(reference.fetch_sessions(start, end))
    sessions = calendar.sessions_in_range(start, end)
    print(f"交易日: {len(sessions)}  ({sessions[0]} ~ {sessions[-1]})")

    n_inst = build_instruments(reference, warehouse, date.today())
    print(f"标的主表: {n_inst}")

    n_bars = ingest_daily_bars(bars_provider, warehouse, symbols, start, end)
    print(f"日线: {n_bars}")
    if bars_provider.truncated:
        print(f"!! 历史被截断: {bars_provider.truncated}")

    n_actions = build_corporate_actions(reference, warehouse, symbols)
    print(f"除权事件: {n_actions}")

    n_factors = build_adj_factors(warehouse)
    print(f"复权因子: {n_factors}")

    query = WarehouseQuery(warehouse)
    report = run_quality_checks(query, calendar, symbols, start, end)
    print()
    print(report.summary())
    return report


def main() -> int:
    report = bootstrap_warehouse(
        Path("warehouse"), SYMBOLS, date(2024, 1, 1), date(2024, 6, 30)
    )
    return 1 if report.is_blocked else 0


if __name__ == "__main__":
    sys.exit(main())
