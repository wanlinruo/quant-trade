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
        self._daily_cache: dict[date, pl.DataFrame] = {}

    def daily(self, trade_date: date) -> pl.DataFrame:
        cached = self._daily_cache.get(trade_date)
        if cached is not None:
            return cached.clone()
        rows = self._client.call("daily", {"trade_date": trade_date.strftime("%Y%m%d")})
        frame = _rows_to_frame(rows, _DAILY_FLOATS)
        self._daily_cache[trade_date] = frame
        return frame.clone()

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

    def stock_st(self, trade_date: date) -> pl.DataFrame:
        rows = self._client.call(
            "stock_st", {"trade_date": trade_date.strftime("%Y%m%d")},
            "ts_code,name,trade_date,type,type_name",
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

    def trade_cal(
        self, start: date, end: date, *, exchange: str = "SSE"
    ) -> pl.DataFrame:
        """交易日历原始事实，包含开市和休市日；不按工作日推算。"""
        rows = self._client.call(
            "trade_cal",
            {
                "exchange": exchange,
                "start_date": start.strftime("%Y%m%d"),
                "end_date": end.strftime("%Y%m%d"),
            },
            "exchange,cal_date,is_open,pretrade_date",
        )
        return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()

    def limit_list_d(self, trade_date: date, limit_type: str = "U") -> pl.DataFrame:
        rows = self._client.call(
            "limit_list_d",
            {"trade_date": trade_date.strftime("%Y%m%d"), "limit_type": limit_type},
        )
        return _rows_to_frame(rows, ("fd_amount", "limit_amount", "float_mv"))

    def moneyflow(self, trade_date: date) -> pl.DataFrame:
        fields = (
            "ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,"
            "buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,"
            "buy_lg_amount,sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,"
            "sell_elg_vol,sell_elg_amount,net_mf_vol,net_mf_amount"
        )
        rows = self._client.call(
            "moneyflow", {"trade_date": trade_date.strftime("%Y%m%d")}, fields,
        )
        return _rows_to_frame(rows, tuple(fields.split(",")[2:]))

    def industry_membership(self) -> pl.DataFrame:
        fields = (
            "l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,ts_code,name,"
            "in_date,out_date,is_new"
        )
        rows: list[dict] = []
        page_size = 2_000
        for offset in range(0, 1_000_000, page_size):
            page = self._client.call(
                "index_member_all", {"offset": offset, "limit": page_size}, fields,
            )
            rows.extend(page)
            if len(page) < page_size:
                break
        else:  # pragma: no cover
            raise RuntimeError("index_member_all exceeded 1,000,000 rows")
        return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()

    def auction_open(self, trade_date: date) -> pl.DataFrame:
        fields = "ts_code,trade_date,open,close,high,low,vol,amount,vwap"
        rows = self._client.call(
            "stk_auction_o", {"trade_date": trade_date.strftime("%Y%m%d")}, fields,
        )
        return _rows_to_frame(
            rows, ("open", "close", "high", "low", "vol", "amount", "vwap")
        )
