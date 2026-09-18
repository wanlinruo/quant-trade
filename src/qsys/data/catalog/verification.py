"""每日指标验证：一次跑完当日增量与当年至今的全量规则并落盘。

由 20:00 的每日服务质检执行（`scripts/service_checks.py`）。收盘同步后面那次
只跑增量（`scripts/tushare_daily_sync.py`），两者都写 `metric_checks`，页面与
质检读同一张表。

**为什么全量只查当年至今**：全量规则要扫整段历史，而 2020~2024 年的存量问题
已分别挂在 D-1、D-10 等条目下，天天重报只会淹没当年的新问题。跑一年约 3 秒，
跑全历史则会把每天的质检变成一张常红的旧账。需要整段历史时手工执行
`scripts/verify_metrics.py run --scope full --start ... --end ...`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import polars as pl
import structlog

from qsys.data.catalog.checks import CheckContext, Scope, run_checks, write_results
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class VerificationSummary:
    day: date
    run_id: str
    rows: int
    blocking: int
    warnings: int
    failed_checks: tuple[str, ...]


def verify_session(
    warehouse: Warehouse, day: date, *, artifacts: Path | None = None
) -> VerificationSummary:
    """当日增量 + 当年至今全量，写成同一次运行的结果。"""
    now = datetime.now()
    run_id = now.strftime("%Y%m%dT%H%M%S%f")
    query = WarehouseQuery(warehouse)
    frames = [
        run_checks(CheckContext(query, warehouse, start, day), run_id, now,
                   scope=scope, artifacts=artifacts)
        for scope, start in ((Scope.INCREMENTAL, day), (Scope.FULL, date(day.year, 1, 1)))
    ]
    frame = pl.concat(frames, how="vertical")
    written = write_results(warehouse, frame)
    failed = frame.filter(~pl.col("passed"))
    summary = VerificationSummary(
        day, run_id, written,
        failed.filter(pl.col("severity") == "blocking").height,
        failed.filter(pl.col("severity") == "warning").height,
        tuple(sorted(set(failed["check_id"].to_list()))),
    )
    log.info("verification.session_done", day=str(day), rows=summary.rows,
             blocking=summary.blocking, warnings=summary.warnings)
    return summary
