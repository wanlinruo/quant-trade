"""Tushare Pro provider：一个接口一个方法，只做取数与列名/类型规范化。

单位换算不在这里做——换算是业务语义，属 pipeline 层。本层只保证
「拿到的就是接口原样返回的值，类型正确」。
"""

from __future__ import annotations

from datetime import date

import polars as pl

from qsys.data.providers.tushare_client import TushareClient


def _rows_to_frame(rows: list[dict], floats: tuple[str, ...]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    # 早期历史里同一数值列会先出现整数、百行后才出现小数。默认只采样前
    # 100 行会把列误判为整数：部分 Polars 版本报错，部分版本会静默截断。
    frame = pl.DataFrame(rows, infer_schema_length=None)
    return frame.with_columns(
        [pl.col(c).cast(pl.Float64, strict=False) for c in floats if c in frame.columns]
    )


_DAILY_FLOATS = ("open", "high", "low", "close", "pre_close", "change",
                 "pct_chg", "vol", "amount")
_BASIC_FLOATS = ("close", "turnover_rate", "turnover_rate_f", "volume_ratio",
                 "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm",
                 "total_share", "float_share", "free_share", "total_mv", "circ_mv")


class TushareProvider:
    def __init__(self, client: TushareClient) -> None:
        self._client = client

    def daily(self, trade_date: date) -> pl.DataFrame:
        rows = self._client.call("daily", {"trade_date": trade_date.strftime("%Y%m%d")})
        return _rows_to_frame(rows, _DAILY_FLOATS)

    def daily_basic(self, trade_date: date) -> pl.DataFrame:
        rows = self._client.call("daily_basic",
                                 {"trade_date": trade_date.strftime("%Y%m%d")})
        return _rows_to_frame(rows, _BASIC_FLOATS)

    def adj_factor(self, trade_date: date) -> pl.DataFrame:
        rows = self._client.call("adj_factor",
                                 {"trade_date": trade_date.strftime("%Y%m%d")})
        return _rows_to_frame(rows, ("adj_factor",))

    def stk_limit(self, trade_date: date) -> pl.DataFrame:
        rows = self._client.call(
            "stk_limit", {"trade_date": trade_date.strftime("%Y%m%d")}
        )
        return _rows_to_frame(rows, ("pre_close", "up_limit", "down_limit"))

    def suspend_d(self, trade_date: date) -> pl.DataFrame:
        rows = self._client.call(
            "suspend_d", {"trade_date": trade_date.strftime("%Y%m%d")}
        )
        return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()

    def index_daily(self, ts_code: str, start: date, end: date) -> pl.DataFrame:
        rows = self._client.call(
            "index_daily",
            {
                "ts_code": ts_code,
                "start_date": start.strftime("%Y%m%d"),
                "end_date": end.strftime("%Y%m%d"),
            },
        )
        return _rows_to_frame(
            rows, ("open", "high", "low", "close", "pre_close", "vol", "amount")
        )

    def stock_basic(self, list_status: str) -> pl.DataFrame:
        rows = self._client.call(
            "stock_basic",
            {"exchange": "", "list_status": list_status},
            "ts_code,symbol,name,area,industry,list_date,delist_date",
        )
        return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()

    def namechange(self) -> pl.DataFrame:
        fields = "ts_code,name,start_date,end_date,ann_date,change_reason"
        rows: list[dict] = []
        page_size = 10_000
        for offset in range(0, 1_000_000, page_size):
            page = self._client.call(
                "namechange", {"offset": offset, "limit": page_size}, fields
            )
            rows.extend(page)
            if len(page) < page_size:
                break
        else:  # pragma: no cover - 上游异常膨胀时宁可失败，不返回截断表
            raise RuntimeError("namechange 分页超过 1,000,000 行，拒绝静默截断")
        return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()

    def limit_list_d(self, trade_date: date, limit_type: str = "U") -> pl.DataFrame:
        rows = self._client.call(
            "limit_list_d",
            {"trade_date": trade_date.strftime("%Y%m%d"), "limit_type": limit_type},
        )
        return _rows_to_frame(rows, ("fd_amount", "limit_amount", "float_mv"))
