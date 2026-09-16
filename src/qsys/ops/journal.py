"""实盘会话日志。

实盘会话写、监控台读——Web 层不持有业务逻辑，也不写任何数据。

写入必须幂等：盘中对账每 N 分钟跑一次，成交回报可能重复投递，
重复写不得产生重复行。净值按交易日去重（保留最后一次），
成交按 client_order_id 去重，对账按 (layer, 时刻) 保留每一次运行留痕。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import polars as pl
import structlog

from qsys.core.events import Reject
from qsys.core.trading import Fill, to_cents
from qsys.data import schema
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_EPOCH = date(1990, 1, 1)
_HORIZON = date(2100, 1, 1)

_EQUITY_SCHEMA = {
    "dt": pl.Date, "cash": pl.Float64,
    "market_value": pl.Float64, "total_value": pl.Float64,
}
_FILLS_SCHEMA = {
    "dt": pl.Date, "client_order_id": pl.Utf8,
    "broker_order_id": pl.Utf8, "broker_trade_id": pl.Utf8,
    "symbol": pl.Utf8, "side": pl.Utf8,
    "qty": pl.Int64, "price": pl.Float64, "fee": pl.Float64,
    "filled_at": pl.Datetime("us"),
}
_REJECTS_SCHEMA = {
    "dt": pl.Date, "client_order_id": pl.Utf8, "symbol": pl.Utf8,
    "qty": pl.Int64, "reason": pl.Utf8, "at": pl.Datetime("us"),
}
_RECON_SCHEMA = {
    "dt": pl.Date, "at": pl.Datetime("us"), "layer": pl.Utf8,
    "is_consistent": pl.Boolean, "cash_diff": pl.Float64,
    "n_mismatches": pl.Int64, "detail": pl.Utf8,
}


class SessionJournal:
    def __init__(self, warehouse: Warehouse) -> None:
        self._wh = warehouse

    def _read(self, dataset: str, year: int, pl_schema: dict) -> pl.DataFrame:
        path = self._wh.path_for(dataset, year)
        if not path.exists():
            return pl.DataFrame(schema=pl_schema)
        frame = pl.read_parquet(path)
        missing = [
            pl.lit(None, dtype=dtype).alias(name)
            for name, dtype in pl_schema.items() if name not in frame.columns
        ]
        return frame.with_columns(missing).select(list(pl_schema)) if missing else frame

    def _read_all(self, dataset: str, pl_schema: dict) -> pl.DataFrame:
        base = self._wh.root / dataset
        parts = sorted(base.glob("year=*/part.parquet"))
        if not parts:
            return pl.DataFrame(schema=pl_schema)
        frames = []
        for path in parts:
            frame = pl.read_parquet(path)
            missing = [
                pl.lit(None, dtype=dtype).alias(name)
                for name, dtype in pl_schema.items() if name not in frame.columns
            ]
            frames.append(frame.with_columns(missing).select(list(pl_schema)))
        return pl.concat(frames, how="vertical")

    def _merge(
        self, dataset: str, dt: date, fresh: pl.DataFrame, pl_schema: dict,
        arrow_schema, key: list[str], columns: list[str],
    ) -> None:
        existing = self._read(dataset, dt.year, pl_schema)
        merged = (
            pl.concat([existing, fresh], how="vertical")
            .unique(subset=key, keep="last", maintain_order=True)
            .sort(key)
            .select(columns)
        )
        self._wh.write(dataset, merged.to_arrow().cast(arrow_schema), year=dt.year)

    # --- 净值 ---

    def record_equity(self, dt: date, *, cash: float, market_value: float) -> None:
        fresh = pl.DataFrame(
            {
                "dt": [dt], "cash": [to_cents(cash)],
                "market_value": [to_cents(market_value)],
                "total_value": [to_cents(cash + market_value)],
            },
            schema=_EQUITY_SCHEMA,
        )
        self._merge("live_equity", dt, fresh, _EQUITY_SCHEMA, schema.LIVE_EQUITY,
                    ["dt"], list(_EQUITY_SCHEMA))

    def equity(self, start: date | None = None, end: date | None = None) -> pl.DataFrame:
        df = self._read_all("live_equity", _EQUITY_SCHEMA)
        if df.height == 0:
            return df
        return df.filter(pl.col("dt").is_between(start or _EPOCH, end or _HORIZON)).sort("dt")

    def sessions(self) -> list[date]:
        return self.equity()["dt"].to_list()

    # --- 成交 ---

    def record_fills(self, dt: date, fills: Sequence[Fill]) -> None:
        if not fills:
            return
        fresh = pl.DataFrame(
            {
                "dt": [dt] * len(fills),
                "client_order_id": [f.order_id for f in fills],
                "broker_order_id": [f.broker_order_id for f in fills],
                "broker_trade_id": [f.broker_trade_id for f in fills],
                "symbol": [str(f.symbol) for f in fills],
                "side": [f.side.value for f in fills],
                "qty": [f.qty for f in fills],
                "price": [f.price for f in fills],
                "fee": [f.fee for f in fills],
                "filled_at": [f.filled_at for f in fills],
            },
            schema=_FILLS_SCHEMA,
        )
        self._merge("live_fills", dt, fresh, _FILLS_SCHEMA, schema.LIVE_FILLS,
                    ["client_order_id", "broker_trade_id", "filled_at", "qty", "price"],
                    list(_FILLS_SCHEMA))

    def fills(self, dt: date) -> pl.DataFrame:
        return self._read("live_fills", dt.year, _FILLS_SCHEMA).filter(
            pl.col("dt") == dt
        ).sort("filled_at")

    # --- 拒单 ---

    def record_rejects(self, dt: date, rejects: Sequence[Reject]) -> None:
        if not rejects:
            return
        fresh = pl.DataFrame(
            {
                "dt": [dt] * len(rejects),
                "client_order_id": [r.order_id for r in rejects],
                "symbol": [str(r.symbol) for r in rejects],
                "qty": [r.qty for r in rejects],
                "reason": [r.reason for r in rejects],
                "at": [r.at for r in rejects],
            },
            schema=_REJECTS_SCHEMA,
        )
        self._merge("live_rejects", dt, fresh, _REJECTS_SCHEMA, schema.LIVE_REJECTS,
                    ["client_order_id", "at", "reason"], list(_REJECTS_SCHEMA))

    def rejects(self, dt: date) -> pl.DataFrame:
        return self._read("live_rejects", dt.year, _REJECTS_SCHEMA).filter(
            pl.col("dt") == dt
        ).sort("at")

    # --- 对账 ---

    def record_reconciliation(
        self, dt: date, layer: str, *, is_consistent: bool,
        cash_diff: float = 0.0, n_mismatches: int = 0, detail: str = "",
        at: datetime | None = None,
    ) -> None:
        fresh = pl.DataFrame(
            {
                "dt": [dt], "at": [at or datetime.now()], "layer": [layer],
                "is_consistent": [is_consistent], "cash_diff": [cash_diff],
                "n_mismatches": [n_mismatches], "detail": [detail],
            },
            schema=_RECON_SCHEMA,
        )
        # 每次运行都要留痕，按 (layer, 时刻) 区分
        self._merge("live_recon", dt, fresh, _RECON_SCHEMA, schema.LIVE_RECON,
                    ["layer", "at"], list(_RECON_SCHEMA))

    def reconciliations(self, dt: date) -> pl.DataFrame:
        return self._read("live_recon", dt.year, _RECON_SCHEMA).filter(
            pl.col("dt") == dt
        ).sort("at")
