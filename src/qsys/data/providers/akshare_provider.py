"""akshare 参考数据适配器：交易日历、标的主表（含退市）、分红送转。

akshare 的返回列名会随版本变化，全部集中在 _AkshareApi 门面与本文件的
列名常量中，变更时只需改这一处。列名依据 scripts/record_fixtures.py 的
实际录制结果确定。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import structlog

from qsys.data.providers.base import ReferenceProvider, normalize_symbol
from qsys.markets.ashare import board_of as _board_of

log = structlog.get_logger(__name__)

INSTRUMENT_COLUMNS = ["symbol", "name", "market", "list_date", "delist_date", "board"]
ACTION_COLUMNS = [
    "ex_date",
    "cash_per_share",
    "stock_per_share",
    "rights_per_share",
    "rights_price",
]

# 只有已实施的分配方案才产生复权因子；预案/预披露尚未发生。
IMPLEMENTED = "实施分配"

_INSTRUMENT_SCHEMA = {
    "symbol": pl.Utf8,
    "name": pl.Utf8,
    "market": pl.Utf8,
    "list_date": pl.Date,
    "delist_date": pl.Date,
    "board": pl.Utf8,
}

_ACTION_SCHEMA = {
    "ex_date": pl.Date,
    "cash_per_share": pl.Float64,
    "stock_per_share": pl.Float64,
    "rights_per_share": pl.Float64,
    "rights_price": pl.Float64,
}

# (取数方法, 代码列, 简称列, 上市日列, 退市日列)
_DELIST_SOURCES = (
    ("delist_sh", "公司代码", "公司简称", "上市日期", "暂停上市日期"),
    ("delist_sz", "证券代码", "证券简称", "上市日期", "终止上市日期"),
)


class _AkshareApi:
    """真实 akshare 调用的薄门面，便于测试注入。"""

    def trade_dates(self):
        import akshare as ak

        return ak.tool_trade_date_hist_sina()

    def code_name(self):
        import akshare as ak

        return ak.stock_info_a_code_name()

    def delist_sh(self):
        import akshare as ak

        return ak.stock_info_sh_delist()

    def delist_sz(self):
        import akshare as ak

        return ak.stock_info_sz_delist()

    def spot(self):
        import akshare as ak

        return ak.stock_zh_a_spot_em()

    def dividends(self, code: str):
        import akshare as ak

        return ak.stock_fhps_detail_em(symbol=code)


def _to_date_series(values: pl.Series) -> pl.Series:
    return (
        values.cast(pl.Utf8)
        .str.replace_all(r"[^0-9]", "")
        .str.strptime(pl.Date, format="%Y%m%d", strict=False)
    )


def _as_float(value) -> float:
    """空值一律归零。

    停牌股的市值字段常缺，而 akshare 给的是 NaN 不是 None——NaN 是真值，
    `value or 0.0` 会原样放行，float("nan") 再一路污染下游聚合。
    """
    if value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if number != number else number


def _zeroed(column: str) -> pl.Expr:
    """把 NaN 和 null 一并归零。

    pandas 的 NaN 进入 polars 后仍是 NaN，polars 严格区分 NaN 与 null，
    fill_null 填不掉 NaN——若泄漏会一路污染复权因子，且不会报错。
    """
    return pl.col(column).cast(pl.Float64).fill_nan(0.0).fill_null(0.0)


class AkshareProvider(ReferenceProvider):
    name = "akshare"

    def __init__(self, api=None) -> None:
        self._api = api if api is not None else _AkshareApi()

    def fetch_sessions(self, start: date, end: date) -> list[date]:
        raw = pl.from_pandas(self._api.trade_dates())
        days = _to_date_series(raw[raw.columns[0]])
        return [d for d in days.drop_nulls().to_list() if start <= d <= end]

    def _delisted_rows(self) -> list[dict]:
        rows: list[dict] = []
        for method, code_col, name_col, list_col, delist_col in _DELIST_SOURCES:
            df = pl.from_pandas(getattr(self._api, method)())
            if df.height == 0:
                continue
            codes = df[code_col].cast(pl.Utf8).to_list()
            names = df[name_col].cast(pl.Utf8).to_list()
            list_dates = _to_date_series(df[list_col]).to_list()
            delist_dates = _to_date_series(df[delist_col]).to_list()
            for raw_code, name, listed, delisted in zip(
                codes, names, list_dates, delist_dates, strict=True
            ):
                if delisted is None:
                    continue
                try:
                    symbol = normalize_symbol(raw_code)
                except ValueError:
                    # 交易所新开号段不应让整条采集管线崩溃
                    log.warning("instruments.unknown_code", source=method, code=raw_code)
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "name": name,
                        "market": symbol.split(".")[1],
                        "list_date": listed,
                        "delist_date": delisted,
                        "board": _board_of(symbol.split(".")[0]),
                    }
                )
        return rows

    def fetch_instruments(self) -> pl.DataFrame:
        listed = pl.from_pandas(self._api.code_name())
        code_col, name_col = listed.columns[0], listed.columns[1]

        rows: list[dict] = []
        for raw_code, name in zip(
            listed[code_col].cast(pl.Utf8).to_list(),
            listed[name_col].cast(pl.Utf8).to_list(),
            strict=True,
        ):
            try:
                symbol = normalize_symbol(raw_code)
            except ValueError:
                log.warning("instruments.unknown_code", source="code_name", code=raw_code)
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "market": symbol.split(".")[1],
                    # 在市股票的上市日免费源不提供，留空；alive_on 视 null 为无下界。
                    "list_date": None,
                    "delist_date": None,
                    "board": _board_of(symbol.split(".")[0]),
                }
            )
        rows.extend(self._delisted_rows())

        return pl.DataFrame(rows, schema=_INSTRUMENT_SCHEMA).select(INSTRUMENT_COLUMNS)

    def fetch_corporate_actions(self, symbol: str) -> pl.DataFrame:
        code = symbol.split(".")[0]
        raw = pl.from_pandas(self._api.dividends(code))
        if raw.height == 0:
            return pl.DataFrame(schema=_ACTION_SCHEMA)

        if "方案进度" in raw.columns:
            raw = raw.filter(pl.col("方案进度") == IMPLEMENTED)
        if raw.height == 0:
            return pl.DataFrame(schema=_ACTION_SCHEMA)

        return (
            raw.select(
                _to_date_series(raw["除权除息日"]).alias("ex_date"),
                # akshare 以"每 10 股"为单位，统一折算为每股
                (_zeroed("现金分红-现金分红比例") / 10.0).alias("cash_per_share"),
                (
                    (_zeroed("送转股份-送股比例") + _zeroed("送转股份-转股比例")) / 10.0
                ).alias("stock_per_share"),
                # 已知缺口：stock_fhps_detail_em 只含分红送转，不含配股。
                # 做过配股的标的复权因子会偏差——需二期补配股数据源，
                # 质量规则 5（复权自洽）可在除权日附近抓到这类跳变。
                pl.lit(0.0).alias("rights_per_share"),
                pl.lit(0.0).alias("rights_price"),
            )
            .drop_nulls("ex_date")
            .sort("ex_date")
            .select(ACTION_COLUMNS)
        )

    _VALUATION_SCHEMA = {
        "symbol": pl.Utf8,
        "close": pl.Float64,
        "market_cap": pl.Float64,
        "circulating_market_cap": pl.Float64,
        "turnover_ratio": pl.Float64,
    }

    def fetch_valuation_snapshot(self) -> pl.DataFrame:
        """全市场当日估值快照。一次调用拿全市场，不逐只查询。

        **已知缺口**：本接口只有当日数据，历史估值从首次采集之日起累积。
        更早的历史需另寻数据源。
        """
        empty = pl.DataFrame(schema=self._VALUATION_SCHEMA)
        raw = pl.from_pandas(self._api.spot())
        if raw.height == 0:
            return empty

        rows: list[dict] = []
        for row in raw.iter_rows(named=True):
            try:
                symbol = normalize_symbol(str(row["代码"]))
            except ValueError:
                log.warning("valuation.unknown_code", code=row["代码"])
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "close": _as_float(row.get("最新价")),
                    "market_cap": _as_float(row.get("总市值")),
                    "circulating_market_cap": _as_float(row.get("流通市值")),
                    "turnover_ratio": _as_float(row.get("换手率")),
                }
            )
        return pl.DataFrame(rows, schema=self._VALUATION_SCHEMA) if rows else empty
