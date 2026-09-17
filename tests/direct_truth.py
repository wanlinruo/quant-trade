"""给测试日线补上 Tushare 直取的 `stk_limit` 与 `stock_st` 行。

daily_flags 的涨跌停价与 ST 只认这两张直取表（D-11），生产代码不再自算。
夹具里按档位算出「交易所会公布的那个涨停价」是在**造测试数据**，
用的是回测撮合同一个 `PriceLimitConstraint.limit_prices`。

`tiers`: {symbol: 涨跌幅}，缺省按号段（300/301/688 为 0.20，其余 0.10）。
`st`: 标为 ST 的标的集合。`pre_close` 为 null 的行不写 stk_limit。
"""

from qsys.data import schema


def write_direct_truth(warehouse, bars, *, tiers=None, st=()):
    import polars as pl

    from qsys.core.constraints import PriceLimitConstraint

    tiers = tiers or {}

    def tier(symbol: str) -> float:
        if symbol in tiers:
            return tiers[symbol]
        return 0.20 if symbol.startswith(("300", "301", "688")) else 0.10

    rows = [r for r in bars.select("symbol", "dt", "pre_close").iter_rows(named=True)]
    limits = [
        (r["symbol"], r["dt"], r["pre_close"],
         *PriceLimitConstraint.limit_prices(r["pre_close"], tier(r["symbol"])))
        for r in rows if r["pre_close"] is not None
    ]
    years = sorted({r["dt"].year for r in rows})
    for year in years:
        chunk = [x for x in limits if x[1].year == year]
        warehouse.write("price_limits_daily", pl.DataFrame(
            chunk, schema=["symbol", "dt", "pre_close", "limit_up", "limit_down"],
            orient="row",
        ).with_columns(pl.col("dt").cast(pl.Date)).to_arrow().cast(
            schema.PRICE_LIMITS_DAILY), year=year)
        st_rows = [r for r in rows if r["dt"].year == year]
        warehouse.write("stock_st_daily", pl.DataFrame({
            "symbol": [r["symbol"] for r in st_rows],
            "dt": [r["dt"] for r in st_rows],
            "is_st": [r["symbol"] in set(st) for r in st_rows],
            "name": [None] * len(st_rows), "st_type": [None] * len(st_rows),
            "st_type_name": [None] * len(st_rows),
        }, schema_overrides={"name": pl.Utf8, "st_type": pl.Utf8,
                             "st_type_name": pl.Utf8}).to_arrow().cast(
            schema.STOCK_ST_DAILY), year=year)

