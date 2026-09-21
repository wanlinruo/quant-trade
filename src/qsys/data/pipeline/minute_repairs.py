"""分钟线异常的来源保真修复。

QMT 对已经退市的证券回溯时可能返回“成交量非零、价格与成交额为零”。付费源
只能作为显式覆盖供检查点报价读取，不能把值写进 QMT 表冒充原始 QMT 行。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import polars as pl


def expected_minute_times(day: date) -> list[datetime]:
    values: list[datetime] = []
    moment = datetime.combine(day, time(9, 30))
    while moment.time() <= time(11, 30):
        values.append(moment)
        moment += timedelta(minutes=1)
    moment = datetime.combine(day, time(13, 1))
    while moment.time() <= time(15, 0):
        values.append(moment)
        moment += timedelta(minutes=1)
    return values


def expected_joinquant_times(day: date) -> list[datetime]:
    """聚宽原生 240 根收盘时刻；09:31 根包含集合竞价，不能伪拆 09:30。"""
    values: list[datetime] = []
    moment = datetime.combine(day, time(9, 31))
    while moment.time() <= time(11, 30):
        values.append(moment)
        moment += timedelta(minutes=1)
    moment = datetime.combine(day, time(13, 1))
    while moment.time() <= time(15, 0):
        values.append(moment)
        moment += timedelta(minutes=1)
    return values


def invalid_qmt_price_symbols(frame: pl.DataFrame) -> list[str]:
    """返回有实际成交但价格/成交额不可用的 QMT 标的。"""
    prices = ["open", "high", "low", "close"]
    invalid = frame.filter(
        (pl.col("volume") > 0)
        & (
            pl.any_horizontal([
                pl.col(name).is_null()
                | ~pl.col(name).is_finite()
                | (pl.col(name) <= 0)
                for name in prices
            ])
            | pl.col("amount").is_null()
            | ~pl.col("amount").is_finite()
            | (pl.col("amount") <= 0)
        )
    )
    return sorted(invalid["symbol"].unique().to_list())


def valid_paid_overrides(
    frame: pl.DataFrame, symbols: list[str], day: date
) -> tuple[pl.DataFrame, dict[str, str]]:
    """只接纳完整、精确落在 241 个标准时刻且数值自洽的付费标的日。"""
    candidates = frame.filter(pl.col("symbol").is_in(symbols))
    expected = expected_minute_times(day)
    accepted: list[str] = []
    rejected: dict[str, str] = {}
    available = set(candidates["symbol"].unique().to_list())
    for symbol in symbols:
        if symbol not in available:
            rejected[symbol] = "missing"
            continue
        rows = candidates.filter(pl.col("symbol") == symbol)
        if rows.height != len(expected) or rows["dt"].n_unique() != len(expected):
            rejected[symbol] = f"bars={rows.height},unique={rows['dt'].n_unique()}"
            continue
        if set(rows["dt"].to_list()) != set(expected):
            rejected[symbol] = "wrong_time_grid"
            continue
        invalid = rows.filter(
            (pl.col("volume") > 0)
            & (
                pl.any_horizontal([
                    ~pl.col(name).is_finite() | (pl.col(name) <= 0)
                    for name in ["open", "high", "low", "close"]
                ])
                | ~pl.col("amount").is_finite()
                | (pl.col("amount") <= 0)
            )
        )
        envelope = rows.filter(
            (pl.col("high") < pl.col("low"))
            | (pl.col("high") < pl.col("open"))
            | (pl.col("high") < pl.col("close"))
            | (pl.col("low") > pl.col("open"))
            | (pl.col("low") > pl.col("close"))
            | (pl.col("volume") < 0)
            | (pl.col("amount") < 0)
        )
        if invalid.height or envelope.height:
            rejected[symbol] = f"invalid={invalid.height},envelope={envelope.height}"
            continue
        accepted.append(symbol)
    return candidates.filter(pl.col("symbol").is_in(accepted)).sort(["symbol", "dt"]), rejected


def valid_joinquant_overrides(
    frame: pl.DataFrame, symbols: list[str], day: date
) -> tuple[pl.DataFrame, dict[str, str]]:
    """只接纳完整、精确落在聚宽 240 个原生收盘时刻的标的日。"""
    candidates = frame.filter(pl.col("symbol").is_in(symbols))
    expected = expected_joinquant_times(day)
    accepted: list[str] = []
    rejected: dict[str, str] = {}
    available = set(candidates["symbol"].unique().to_list()) if candidates.height else set()
    for symbol in symbols:
        if symbol not in available:
            rejected[symbol] = "missing"
            continue
        rows = candidates.filter(pl.col("symbol") == symbol)
        if rows.height != len(expected) or rows["dt"].n_unique() != len(expected):
            rejected[symbol] = f"bars={rows.height},unique={rows['dt'].n_unique()}"
            continue
        if set(rows["dt"].to_list()) != set(expected):
            rejected[symbol] = "wrong_time_grid"
            continue
        invalid = rows.filter(
            (pl.col("volume") > 0)
            & (
                pl.any_horizontal([
                    pl.col(name).is_null()
                    | ~pl.col(name).is_finite()
                    | (pl.col(name) <= 0)
                    for name in ["open", "high", "low", "close"]
                ])
                | pl.col("amount").is_null()
                | ~pl.col("amount").is_finite()
                # JQ money 取整到元：不足 1 元的碎股成交会合法地显示为 0。
                | ((pl.col("amount") <= 0) & (pl.col("volume") * pl.col("high") >= 1))
            )
        )
        envelope = rows.filter(
            (pl.col("high") < pl.col("low"))
            | (pl.col("high") < pl.col("open"))
            | (pl.col("high") < pl.col("close"))
            | (pl.col("low") > pl.col("open"))
            | (pl.col("low") > pl.col("close"))
            | (pl.col("volume") < 0)
            | (pl.col("amount") < 0)
        )
        if invalid.height or envelope.height:
            rejected[symbol] = f"invalid={invalid.height},envelope={envelope.height}"
            continue
        accepted.append(symbol)
    return candidates.filter(pl.col("symbol").is_in(accepted)).sort(["symbol", "dt"]), rejected


# 粗大错误的相对偏差门槛。取整、首末分钟边界、北交所盘后固定价格成交造成的
# 差异都在 2% 以内；2026 付费分钟出现过成交量放大 77 倍而成交额不变的源头
# 缺陷，这类错误必须拒绝入库。
DAILY_GROSS_TOLERANCE = 0.05
_PRICES = ("open", "high", "low", "close")


def daily_from_minutes(frame: pl.DataFrame) -> pl.DataFrame:
    """按标的日汇总分钟线（成交量为股）。

    OHLC 只取有成交的 bar：零成交 bar 的价格是源头沿用昨收的占位值，退市整理
    首日这类大幅跳空时会让开盘价错成昨收。
    """
    day = pl.col("dt").dt.date().alias("dt")
    totals = frame.group_by("symbol", day).agg(
        pl.col("volume").sum().cast(pl.Float64), pl.col("amount").sum()
    )
    prices = frame.filter(pl.col("volume") > 0).sort("dt").group_by("symbol", day).agg(
        pl.col("open").first(), pl.col("high").max(),
        pl.col("low").min(), pl.col("close").last(),
    )
    return totals.join(prices, on=["symbol", "dt"], how="left").sort(["symbol", "dt"])


def compare_with_daily(minutes: pl.DataFrame, daily: pl.DataFrame) -> pl.DataFrame:
    """逐标的日对比分钟汇总与日线（成交量为手、成交额为元）。

    返回 ``symbol, dt, gross, minor``：``gross`` 是粗大错误原因（无则为 null），
    ``minor`` 表示超出年度验收容差（OHLC 0.005 元、成交量 0.5 股、成交额 100 元）
    但未达粗大错误。
    """
    reference = daily.select(
        "symbol", "dt", *(pl.col(name).alias(f"{name}_d") for name in _PRICES),
        (pl.col("volume") * 100).alias("volume_d"), pl.col("amount").alias("amount_d"),
    )
    joined = daily_from_minutes(minutes).join(reference, on=["symbol", "dt"], how="left")

    def relative(name: str, floor: float) -> pl.Expr:
        base = pl.max_horizontal(pl.col(f"{name}_d").abs(), pl.lit(floor))
        return (pl.col(name) - pl.col(f"{name}_d")).abs() / base

    gross = (
        pl.when(pl.col("volume_d").is_null()).then(pl.lit("daily_missing"))
        .when(relative("volume", 100) > DAILY_GROSS_TOLERANCE).then(pl.lit("volume"))
        .when(relative("amount", 100) > DAILY_GROSS_TOLERANCE).then(pl.lit("amount"))
        .when(pl.col("open").is_null() & (pl.col("volume_d") > 0)).then(pl.lit("no_traded_bars"))
        .when(pl.any_horizontal([relative(name, 0.01) > DAILY_GROSS_TOLERANCE
                                 for name in _PRICES])).then(pl.lit("price"))
        .otherwise(None)
    )
    # 缺日线或全天无成交 bar 时比较结果为 null，eq_missing 把它判为「未超容差」。
    minor = pl.any_horizontal(
        [(pl.col(name) - pl.col(f"{name}_d")).abs() > 0.005 for name in _PRICES]
        + [(pl.col("volume") - pl.col("volume_d")).abs() > 0.5,
           (pl.col("amount") - pl.col("amount_d")).abs() > 100]
    ).eq_missing(True)
    return joined.select(
        "symbol", "dt", gross.alias("gross"),
        (gross.is_null() & minor).alias("minor"),
    )
