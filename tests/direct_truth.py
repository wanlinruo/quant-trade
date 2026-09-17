"""给测试日线补上 Tushare 直取的 `stk_limit` 与 `stock_st` 行。

daily_flags 的涨跌停价与 ST 只认这两张直取表（D-11），生产代码不再自算。
夹具里按档位算出「交易所会公布的那个涨停价」是在**造测试数据**，
生产代码里已经没有这个换算（D-19），它只在测试里造数据用。

`tiers`: {symbol: 涨跌幅}，缺省按号段（300/301/688 为 0.20，其余 0.10）。
`st`: 标为 ST 的标的集合。`pre_close` 为 null 的行不写 stk_limit。
"""

from decimal import ROUND_HALF_UP, Decimal

from qsys.data import schema


def exchange_limit_prices(prev_close: float, tier: float) -> tuple[float, float]:
    """交易所按前收与档位公布的涨跌停价（四舍五入到分，Decimal 计算）。

    乘法必须在 Decimal 里做：前收 1.65 的跌停价真值 1.485 应入为 1.49，
    float 的 1.65*0.9 = 1.4849999999999999 会取成 1.48。
    """
    cent = Decimal("0.01")
    base, ratio = Decimal(str(prev_close)), Decimal(str(tier))
    up = (base * (1 + ratio)).quantize(cent, rounding=ROUND_HALF_UP)
    down = (base * (1 - ratio)).quantize(cent, rounding=ROUND_HALF_UP)
    return float(up), float(down)


def write_direct_truth(warehouse, bars, *, tiers=None, st=()):
    import polars as pl

    tiers = tiers or {}

    def tier(symbol: str) -> float:
        if symbol in tiers:
            return tiers[symbol]
        return 0.20 if symbol.startswith(("300", "301", "688")) else 0.10

    rows = [r for r in bars.select("symbol", "dt", "pre_close").iter_rows(named=True)]
    limits = [
        (r["symbol"], r["dt"], r["pre_close"],
         *exchange_limit_prices(r["pre_close"], tier(r["symbol"])))
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



def limits_from_closes(closes_by_session, symbols, tier=0.10):
    """测试数据：按上一交易日收盘（首日用当日开盘）造交易所会公布的涨跌停价。

    返回可直接传给 `BacktestEngine(price_limits=...)` 的加载函数。
    `closes_by_session`: {session: (open, close)}，同一价格序列用于所有 `symbols`。
    """
    ordered = sorted(closes_by_session)
    table = {}
    for i, session in enumerate(ordered):
        base = closes_by_session[ordered[i - 1]][1] if i else closes_by_session[session][0]
        up, down = exchange_limit_prices(base, tier)
        table[session] = {symbol: (up, down) for symbol in symbols}

    def load(start, end):
        return {d: v for d, v in table.items() if start <= d <= end}

    return load
