"""仓库四张地基表 + 采集日志的 pyarrow schema。

bars_daily 存不复权价格，只追加永不重写；复权因子单独存于 adj_factors。
"""

from __future__ import annotations

import pyarrow as pa

INSTRUMENTS = pa.schema(
    [
        ("symbol", pa.string()),
        ("name", pa.string()),
        ("market", pa.string()),
        ("list_date", pa.date32()),
        ("delist_date", pa.date32()),  # 未退市为 null
        ("board", pa.string()),  # main / gem / star / bse
    ]
)

BARS_DAILY = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.int64()),
        ("amount", pa.float64()),
    ]
)

CORPORATE_ACTIONS = pa.schema(
    [
        ("symbol", pa.string()),
        ("ex_date", pa.date32()),  # 除权除息日
        ("cash_per_share", pa.float64()),  # 每股现金红利（税前）
        ("stock_per_share", pa.float64()),  # 每股送转股数
        ("rights_per_share", pa.float64()),  # 每股配股数
        ("rights_price", pa.float64()),  # 配股价
    ]
)

ADJ_FACTORS = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("cum_factor", pa.float64()),  # 后复权累计因子
    ]
)

INGEST_LOG = pa.schema(
    [
        ("dataset", pa.string()),
        ("dt", pa.date32()),
        ("provider", pa.string()),
        ("ingested_at", pa.timestamp("us")),
        ("row_count", pa.int64()),
        ("ok", pa.bool_()),
        ("message", pa.string()),
    ]
)

PARTITIONED: dict[str, pa.Schema] = {
    "bars_daily": BARS_DAILY,
    "ingest_log": INGEST_LOG,
}

UNPARTITIONED: dict[str, pa.Schema] = {
    "instruments": INSTRUMENTS,
    "corporate_actions": CORPORATE_ACTIONS,
    "adj_factors": ADJ_FACTORS,
}

ALL: dict[str, pa.Schema] = {**PARTITIONED, **UNPARTITIONED}
