"""分红送配导入。

数据来自 Windows 侧 QMT 的 `get_divid_factors`——**它含配股**，而 akshare 的
`stock_fhps_detail_em` 只有分红送转。做过配股的标的复权因子会偏差，
这是 spec §12 风险表里挂着的一条，由此关闭。

导入落进 `corporate_actions`（回测在除权日据此调整持仓与现金）。
`factor` 列是 QMT 给的单次复权因子，只用于交叉校验，不入库——
价格复权因子从日线真值源直取；本表仍供 `adjust.build_adj_factors` 派生
成交量所需的股本累计比例，保持可重算。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import polars as pl
import pyarrow as pa
import structlog

from qsys.data import schema
from qsys.data.pipeline.minutes import ImportResult, _free_name
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

# Windows 侧导出的列（比仓库多一个 factor，仅用于校验）
DIVIDEND_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("ex_date", pa.date32()),
        ("cash_per_share", pa.float64()),
        ("stock_per_share", pa.float64()),
        ("rights_per_share", pa.float64()),
        ("rights_price", pa.float64()),
        ("factor", pa.float64()),
    ]
)

_COLUMNS = list(schema.CORPORATE_ACTIONS.names)


def import_dividends(
    warehouse: Warehouse,
    incoming_dir: Path,
    *,
    archive_dir: Path | None = None,
) -> ImportResult:
    """导入目录下所有分红 parquet。返回统计。"""
    incoming = Path(incoming_dir)
    if not incoming.is_dir():
        return ImportResult(0, 0, 0)

    frames: list[pl.DataFrame] = []
    good: list[Path] = []
    errors = 0

    for path in sorted(incoming.glob("*.parquet")):
        try:
            frame = pl.read_parquet(path)
            missing = set(_COLUMNS) - set(frame.columns)
            if missing:
                raise ValueError(f"missing columns: {sorted(missing)}")
            frames.append(frame.select(_COLUMNS))
            good.append(path)
        except Exception as exc:  # 单个坏文件不中断整批
            errors += 1
            log.warning("dividends.bad_file", path=str(path), error=str(exc))

    rows = 0
    if frames:
        fresh = pl.concat(frames, how="vertical")
        merged = (
            pl.concat(
                [WarehouseQuery(warehouse).corporate_actions(), fresh], how="vertical"
            )
            .unique(subset=["symbol", "ex_date"], keep="last", maintain_order=True)
            .sort(["symbol", "ex_date"])
            .select(_COLUMNS)
        )
        warehouse.write(
            "corporate_actions", merged.to_arrow().cast(schema.CORPORATE_ACTIONS)
        )
        rows = fresh.height

    if archive_dir is not None:
        archive = Path(archive_dir)
        archive.mkdir(parents=True, exist_ok=True)
        for path in good:
            shutil.move(str(path), str(_free_name(archive, path.name)))

    log.info("dividends.imported", files=len(good), rows=rows, errors=errors)
    return ImportResult(len(good), rows, errors)
