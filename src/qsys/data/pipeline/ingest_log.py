"""采集日志：记录哪天、哪个源、何时采、成败，供数据问题回溯。"""

from __future__ import annotations

from datetime import date, datetime

import polars as pl

from qsys.data import schema
from qsys.data.storage import Warehouse

_KEY = ["dataset", "dt", "provider"]

_LOG_SCHEMA = {
    "dataset": pl.Utf8,
    "dt": pl.Date,
    "provider": pl.Utf8,
    "ingested_at": pl.Datetime("us"),
    "row_count": pl.Int64,
    "ok": pl.Boolean,
    "message": pl.Utf8,
}


class IngestLog:
    def __init__(self, warehouse: Warehouse) -> None:
        self._wh = warehouse

    def entries(self, year: int) -> pl.DataFrame:
        path = self._wh.path_for("ingest_log", year)
        if not path.exists():
            return pl.from_arrow(schema.INGEST_LOG.empty_table())
        return pl.read_parquet(path)

    def record(
        self,
        dataset: str,
        dt: date,
        provider: str,
        row_count: int,
        ok: bool,
        message: str = "",
    ) -> None:
        year = dt.year
        new = pl.DataFrame(
            {
                "dataset": [dataset],
                "dt": [dt],
                "provider": [provider],
                "ingested_at": [datetime.now()],
                "row_count": [row_count],
                "ok": [ok],
                "message": [message],
            },
            schema=_LOG_SCHEMA,
        )
        merged = (
            pl.concat([self.entries(year), new], how="vertical")
            # maintain_order 必须显式打开：否则"保留最后一条"在无序前提下无定义，
            # 幂等性会变成偶然行为
            .unique(subset=_KEY, keep="last", maintain_order=True)
            .sort(_KEY)
        )
        self._wh.write("ingest_log", merged.to_arrow().cast(schema.INGEST_LOG), year=year)
