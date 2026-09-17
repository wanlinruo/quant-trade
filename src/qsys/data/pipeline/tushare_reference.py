"""Tushare 参考事实落库。

外部值只做列名、日期和明确单位换算；不反推、不填充。分区表按年合并，
无分区表原子覆盖。每日同步的接线适配器位于 ``tushare_sync``。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.storage import Warehouse
from qsys.markets.ashare import board_of

log = structlog.get_logger(__name__)


def _merge_write(warehouse: Warehouse, dataset: str, frame: pl.DataFrame,
                 arrow_schema, keys: list[str], reader) -> int:
    if frame.height == 0:
        return 0
    total = 0
    for year in sorted({day.year for day in frame["dt"].to_list()}):
        lo, hi = date(year, 1, 1), date(year, 12, 31)
        chunk = frame.filter(pl.col("dt").is_between(lo, hi))
        merged = (
            pl.concat([reader(lo, hi), chunk], how="vertical_relaxed")
            .unique(subset=keys, keep="last", maintain_order=True)
            .sort(keys)
            .select(arrow_schema.names)
        )
        warehouse.write(dataset, merged.to_arrow().cast(arrow_schema), year=year)
        total += chunk.height
    return total


def _replace_days(warehouse: Warehouse, dataset: str, frame: pl.DataFrame,
                  arrow_schema, days: Sequence[date], reader) -> int:
    """以本次响应完整替换指定日期；上游撤销的行也会从本地消失。"""
    target = set(days)
    for year in sorted({day.year for day in target}):
        year_days = {day for day in target if day.year == year}
        lo, hi = date(year, 1, 1), date(year, 12, 31)
        outside = reader(lo, hi).filter(~pl.col("dt").is_in(list(year_days)))
        chunk = (frame.filter(pl.col("dt").is_in(list(year_days)))
                 if frame.height else pl.from_arrow(arrow_schema.empty_table()))
        merged = pl.concat([outside, chunk], how="vertical_relaxed").select(
            arrow_schema.names
        )
        warehouse.write(dataset, merged.sort(["dt", "symbol"]).to_arrow().cast(
            arrow_schema
        ), year=year)
    return frame.height


def _date_expr(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False)


def ingest_price_limits(provider, warehouse: Warehouse, days: Sequence[date]) -> int:
    from qsys.data.query import WarehouseQuery

    frames = []
    for day in days:
        raw = provider.stk_limit(day)
        if raw.height:
            # stk_limit 实际只给 up_limit/down_limit；pre_close 同样是 Tushare
            # daily 的直接字段。真实 provider 会缓存 sync 已取得的当日日线，
            # 因而这里不会多耗一次接口调用。
            if "pre_close" not in raw.columns:
                daily = provider.daily(day)
                raw = raw.join(
                    daily.select("ts_code", "trade_date", "pre_close"),
                    on=["ts_code", "trade_date"], how="left",
                )
            frames.append(raw.select(
                pl.col("ts_code").alias("symbol"),
                _date_expr("trade_date").alias("dt"),
                pl.col("pre_close"), pl.col("up_limit").alias("limit_up"),
                pl.col("down_limit").alias("limit_down"),
            ))
    if not frames:
        return 0
    query = WarehouseQuery(warehouse)
    written = _merge_write(
        warehouse, "price_limits_daily", pl.concat(frames), schema.PRICE_LIMITS_DAILY,
        ["symbol", "dt"], lambda lo, hi: query.price_limits(None, lo, hi),
    )
    log.info("price_limits.ingested", rows=written, days=len(days))
    return written


def ingest_suspensions(provider, warehouse: Warehouse, days: Sequence[date]) -> int:
    from qsys.data.query import WarehouseQuery

    frames = []
    for day in days:
        raw = provider.suspend_d(day)
        if not raw.height:
            continue
        timing = (pl.col("suspend_timing").cast(pl.Utf8)
                  if "suspend_timing" in raw.columns else pl.lit(None, dtype=pl.Utf8))
        frames.append(raw.select(
            pl.col("ts_code").alias("symbol"),
            _date_expr("trade_date").alias("dt"),
            pl.col("suspend_type").cast(pl.Utf8), timing.alias("suspend_timing"),
        ))
    query = WarehouseQuery(warehouse)
    incoming = (pl.concat(frames) if frames
                else pl.from_arrow(schema.SUSPEND_DAILY.empty_table()))
    written = _replace_days(
        warehouse, "suspend_daily", incoming, schema.SUSPEND_DAILY, days,
        lambda lo, hi: query.suspensions(None, lo, hi),
    )
    log.info("suspensions.ingested", rows=written, days=len(days))
    return written


def ingest_stock_st(provider, warehouse: Warehouse, days: Sequence[date]) -> int:
    """落库完整股票日布尔真值，而不是只保存 ST 正例。"""
    from qsys.data.query import WarehouseQuery

    query = WarehouseQuery(warehouse)
    frames = []
    for day in days:
        universe = query.tushare_daily(
            None, day, day, columns=["symbol", "dt"]
        ).unique(subset=["symbol", "dt"])
        if universe.height == 0:
            raise RuntimeError(f"{day:%Y%m%d} 没有 tushare_daily，不能判定 ST 完整性")
        raw = provider.stock_st(day)
        if raw.height == 0:
            # 正例名单为空不是「全市场没有 ST」——A 股每天都有上百只风险警示股。
            # 按空名单展开会把全市场静默写成 False（D-11），所以整批拒写。
            raise RuntimeError(
                f"Tushare stock_st 在 {day:%Y%m%d} 返回 0 行；不能据此判定全市场非 ST，"
                "不写入 stock_st_daily"
            )
        positives = raw.select(
            pl.col("ts_code").alias("symbol"),
            pl.col("name").cast(pl.Utf8),
            pl.col("type").cast(pl.Utf8).alias("st_type"),
            pl.col("type_name").cast(pl.Utf8).alias("st_type_name"),
            pl.lit(True).alias("_matched"),
        ).unique(subset=["symbol"], keep="last")
        frames.append(universe.join(positives, on="symbol", how="left").with_columns(
            pl.col("_matched").fill_null(False).alias("is_st")
        ).select(schema.STOCK_ST_DAILY.names))

    incoming = pl.concat(frames, how="vertical_relaxed")
    written = _replace_days(
        warehouse, "stock_st_daily", incoming, schema.STOCK_ST_DAILY, days,
        lambda lo, hi: query.stock_st(None, lo, hi),
    )
    log.info("stock_st.ingested", rows=written, days=len(days),
             st_rows=incoming.filter(pl.col("is_st")).height)
    return written


def ingest_index_daily(provider, warehouse: Warehouse, ts_codes: Sequence[str],
                       start: date, end: date) -> int:
    from qsys.data.query import WarehouseQuery

    frames = []
    for code in ts_codes:
        raw = provider.index_daily(code, start, end)
        if not raw.height:
            continue
        frames.append(raw.select(
            pl.col("ts_code").alias("symbol"),
            _date_expr("trade_date").alias("dt"),
            "open", "high", "low", "close",
            pl.col("vol").cast(pl.Float64).alias("volume"),
            (pl.col("amount") * 1000.0).alias("amount"),
        ))
    if not frames:
        return 0
    query = WarehouseQuery(warehouse)
    written = _merge_write(
        warehouse, "index_bars", pl.concat(frames), schema.INDEX_BARS,
        ["symbol", "dt"], lambda lo, hi: query.index_bars(None, lo, hi),
    )
    log.info("index_bars.ingested", rows=written, codes=list(ts_codes))
    return written


def ingest_instruments(provider, warehouse: Warehouse, as_of: date) -> int:
    """落库标的主表（覆盖写，只留最新一份）。

    `as_of` 是**取这份主表的那一天**，不是正在同步的交易日：`stock_basic`
    返回的是当前名称，只对取数当天成立。

    2026-09-17 起不再追加 `name_snapshots`（用户决定）：标的一律按代码认，
    名称用处不大；退市整理期由 `namechange` 派生的退市整理期池承担。
    已有的快照原地保留，不删。
    """
    parts = [frame for status in ("L", "D")
             if (frame := provider.stock_basic(status)).height]
    if not parts:
        return 0
    frame = pl.concat(parts, how="diagonal_relaxed")
    out = frame.select(
        pl.col("ts_code").alias("symbol"), pl.col("name").cast(pl.Utf8),
        pl.col("ts_code").str.split(".").list.last().alias("market"),
        _date_expr("list_date").alias("list_date"),
        _date_expr("delist_date").alias("delist_date"),
        pl.col("ts_code").map_elements(
            lambda symbol: board_of(symbol.split(".")[0]), return_dtype=pl.Utf8
        ).alias("board"),
        pl.lit(as_of, dtype=pl.Date).alias("as_of"),
    ).unique(subset=["symbol"], keep="last").sort("symbol")
    warehouse.write("instruments", out.to_arrow().cast(schema.INSTRUMENTS))

    log.info("instruments.ingested", rows=out.height, as_of=str(as_of))
    return out.height


def ingest_name_history(provider, warehouse: Warehouse) -> int:
    raw = provider.namechange()
    if not raw.height:
        return 0
    out = raw.select(
        pl.col("ts_code").alias("symbol"), pl.col("name").cast(pl.Utf8),
        _date_expr("start_date").alias("start_date"),
        _date_expr("end_date").alias("end_date"),
        _date_expr("ann_date").alias("ann_date"),
        pl.col("change_reason").cast(pl.Utf8),
    ).unique(subset=["symbol", "start_date"], keep="last").sort(["symbol", "start_date"])
    warehouse.write("name_history", out.to_arrow().cast(schema.NAME_HISTORY))
    log.info("name_history.ingested", rows=out.height)
    return out.height


def ingest_limit_list(provider, warehouse: Warehouse, days: Sequence[date],
                      limit_types: Sequence[str] = ("U", "Z")) -> int:
    """直取涨停与炸板列表；接口自 2020 年起且不含 ST 股票统计。"""
    from qsys.data.query import WarehouseQuery

    frames = []
    for day in days:
        for limit_type in limit_types:
            raw = provider.limit_list_d(day, limit_type)
            if raw.height:
                frames.append(raw.select(
                    pl.col("ts_code").alias("symbol"),
                    _date_expr("trade_date").alias("dt"),
                    pl.lit(limit_type, dtype=pl.Utf8).alias("limit_type"),
                    pl.col("limit_times").cast(pl.Int64),
                    pl.col("open_times").cast(pl.Int64),
                    pl.col("first_time").cast(pl.Utf8),
                    pl.col("fd_amount"), pl.col("limit_amount"), pl.col("float_mv"),
                ))
    query = WarehouseQuery(warehouse)
    incoming = (pl.concat(frames) if frames
                else pl.from_arrow(schema.LIMIT_LIST_DAILY.empty_table()))
    # D-12：整天空响应只说明「这次没取到」（发布前或接口抖动），不说明当天
    # 没有涨停。已入库的名单不得被它抹掉；非空响应照常整天替换。
    answered = set(incoming["dt"].to_list())
    silent = [day for day in days if day not in answered]
    stored = (set(query.limit_list(None, min(silent), max(silent))["dt"].to_list())
              if silent else set())
    kept = sorted(day for day in silent if day in stored)
    if kept:
        log.warning("limit_list.empty_response_kept_stored",
                    days=[str(day) for day in kept])
    written = _replace_days(
        warehouse, "limit_list_daily", incoming, schema.LIMIT_LIST_DAILY,
        [day for day in days if day not in kept],
        lambda lo, hi: query.limit_list(None, lo, hi),
    )
    log.info("limit_list.ingested", rows=written, days=len(days),
             limit_types=list(limit_types))
    return written
