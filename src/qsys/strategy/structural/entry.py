"""入场判定，逐行对照原文 L1201-L1346。

三个函数都接收**已取好的历史 DataFrame**，自己不访问数据——这样单元测试
可以用构造数据直接断言，不必搭起整个仓库。

**单位陷阱**：原策略 `vwap = money / volume`，聚宽的 volume 单位是**股**；
我们的日线 volume 是**手**。实测 600036.SH 2026-09-08：
`amount/(volume*100) = 41.00` 正落在当日区间 [40.81, 41.38] 内，
而 `amount/volume = 4100`。照抄会让 avg_inc 变成 +40 而不是 +0.07，
`check_high_open` 会放行所有股票——而且不会报错。
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from qsys.strategy.structural.params import (
    HIGH_AUCTION_RATIO_MAX,
    HIGH_AUCTION_RATIO_MIN,
    HIGH_AUCTION_VOL_RATIO_MIN,
    HIGH_AVG_INC_MIN,
    HIGH_MAX_CIRCULATING_CAP,
    HIGH_MAX_MONEY,
    HIGH_MIN_MARKET_CAP,
    HIGH_MIN_MONEY,
    LEFT_PRESSURE_DEFAULT_ZYTS,
    LEFT_PRESSURE_PADDING,
    LEFT_PRESSURE_VOLUME_FACTOR,
    LOW_AUCTION_RATIO_MAX,
    LOW_AUCTION_RATIO_MIN,
    LOW_HISTORY_DAYS,
    LOW_MIN_MONEY,
    LOW_RELATIVE_POSITION_MAX,
)

LOT = 100  # 日线 volume 单位是手，换算成股要乘 100


def diagnose_low_open(
    history: pl.DataFrame, auction_price: float | None,
) -> dict[str, object]:
    """返回 LOW 入档的原子判断；``eligible`` 与 ``check_low_open`` 同义。"""
    enough_history = history.height >= LOW_HISTORY_DAYS
    out: dict[str, object] = {
        "low_history_ok": enough_history,
        "low_auction_price_ok": auction_price is not None,
        "low_relative_position": None,
        "low_relative_position_ok": False,
        "low_prev_amount": None,
        "low_amount_ok": False,
        "low_open_ratio": None,
        "low_open_band_ok": False,
    }
    if auction_price is None or not enough_history:
        return {**out, "eligible": False}

    window = history.tail(LOW_HISTORY_DAYS)
    close = window["close"].to_list()[-1]
    high = window["high"].max()
    low = window["low"].min()
    money = window["amount"].to_list()[-1]
    out["low_prev_amount"] = money
    out["low_amount_ok"] = money >= LOW_MIN_MONEY
    if high > low:
        relative_position = (close - low) / (high - low)
        out["low_relative_position"] = relative_position
        out["low_relative_position_ok"] = relative_position <= LOW_RELATIVE_POSITION_MAX
    if close > 0:
        ratio = auction_price / close
        out["low_open_ratio"] = ratio
        out["low_open_band_ok"] = LOW_AUCTION_RATIO_MIN <= ratio <= LOW_AUCTION_RATIO_MAX
    out["eligible"] = all(
        bool(out[key])
        for key in (
            "low_history_ok", "low_auction_price_ok", "low_relative_position_ok",
            "low_amount_ok", "low_open_band_ok",
        )
    )
    return out


def check_low_open(history: pl.DataFrame, auction_price: float | None) -> bool:
    """低开入场（原文 L1201-L1221）。

    `history` 为截至昨日的日线，需满 60 根。**只取最近 60 根**——
    原策略是 `attribute_history(stock, 60, ...)`，调用方为了
    `check_left_pressure` 会传 101 根，多出来的不能参与相对位置计算。

    与聚宽对账时这个差别让 25 条本该选中的候选被漏掉：更早的低点会把
    相对位置整个抬上去，实测有一例 60 根算 0.276、101 根算 0.603。
    """
    return bool(diagnose_low_open(history, auction_price)["eligible"])


def check_left_pressure(history: pl.DataFrame) -> bool:
    """左侧压力与放量（原文 L1334-L1346）。

    原版有两处刻意的写法，照抄不改：
      · 从**倒数第 3 天**起找前高，不是倒数第 1 天
      · `volume[-zyts:]` 不做越界检查，zyts 超过长度时就是取全部
    """
    highs = history["high"].to_list()
    volumes = history["volume"].to_list()
    if not highs:
        return False

    prev_high = highs[-1]
    zyts_0 = LEFT_PRESSURE_DEFAULT_ZYTS
    for i in range(3, len(highs)):
        if highs[-i] >= prev_high:
            zyts_0 = i - 1
            break

    window = volumes[-(zyts_0 + LEFT_PRESSURE_PADDING) :]
    if len(window) < 2:
        return False
    return window[-1] > max(window[:-1]) * LEFT_PRESSURE_VOLUME_FACTOR


def check_high_open(
    history: pl.DataFrame,
    auction_price: float | None,
    auction_volume: float | None,
    valuation: Mapping[str, float] | None,
    *,
    require_auction_volume: bool = True,
) -> bool:
    """高开入场（原文 L1222-L1272）。

    `history` 为截至昨日的日线，至少 2 根；`check_left_pressure` 所需的
    101 根由调用方一并传入（本函数只用最后 2 根做前置判定）。

    `require_auction_volume=False` 时，竞价量缺失则**跳过**那一条过滤而不是
    整只剔除。分钟线只覆盖最近约一年，若不给这个口子，HIGH 分支就完全落不到
    早期区间——对账时会表现为「qsys 漏掉了全部 HIGH 票」，那是数据缺失的
    假象而非移植错误。差异在对账时按此归因。
    """
    return bool(diagnose_high_open(
        history, auction_price, auction_volume, valuation,
        require_auction_volume=require_auction_volume,
    )["eligible"])


def diagnose_high_open(
    history: pl.DataFrame,
    auction_price: float | None,
    auction_volume: float | None,
    valuation: Mapping[str, float] | None,
    *,
    require_auction_volume: bool = True,
) -> dict[str, object]:
    """返回 HIGH 入档原子判断；缺竞价量的近似口径显式标记。"""
    out: dict[str, object] = {
        "high_history_ok": history.height >= 2,
        "high_auction_price_ok": auction_price is not None,
        "high_auction_volume_present": auction_volume is not None,
        "high_auction_volume_required": require_auction_volume,
        "high_avg_inc": None,
        "high_avg_inc_ok": False,
        "high_prev_amount": None,
        "high_amount_ok": False,
        "high_market_cap": None,
        "high_market_cap_ok": False,
        "high_circulating_market_cap": None,
        "high_circulating_cap_ok": False,
        "high_auction_volume_ratio": None,
        "high_auction_volume_ok": None,
        "high_auction_volume_rule_ok": not require_auction_volume,
        "high_open_ratio": None,
        "high_open_band_ok": False,
        "high_left_pressure_ok": False,
    }
    if auction_price is None or history.height < 2:
        return {**out, "eligible": False}
    closes = history["close"].to_list()
    volumes = history["volume"].to_list()
    moneys = history["amount"].to_list()
    pre_prev_close, prev_close = closes[-2], closes[-1]
    prev_volume, prev_money = volumes[-1], moneys[-1]
    out["high_prev_amount"] = prev_money
    out["high_amount_ok"] = HIGH_MIN_MONEY <= prev_money <= HIGH_MAX_MONEY
    if pre_prev_close > 0 and prev_close > 0 and prev_volume > 0:
        vwap = prev_money / (prev_volume * LOT)
        avg_inc = vwap / pre_prev_close - 1
        out["high_avg_inc"] = avg_inc
        out["high_avg_inc_ok"] = avg_inc >= HIGH_AVG_INC_MIN
        ratio = auction_price / prev_close
        out["high_open_ratio"] = ratio
        out["high_open_band_ok"] = HIGH_AUCTION_RATIO_MIN < ratio < HIGH_AUCTION_RATIO_MAX
        if auction_volume is not None:
            volume_ratio = auction_volume / prev_volume
            out["high_auction_volume_ratio"] = volume_ratio
            out["high_auction_volume_ok"] = volume_ratio >= HIGH_AUCTION_VOL_RATIO_MIN
            out["high_auction_volume_rule_ok"] = out["high_auction_volume_ok"]
    if valuation is not None:
        market_cap = valuation["market_cap"]
        circulating = valuation["circulating_market_cap"]
        out["high_market_cap"] = market_cap
        out["high_market_cap_ok"] = market_cap >= HIGH_MIN_MARKET_CAP
        out["high_circulating_market_cap"] = circulating
        out["high_circulating_cap_ok"] = circulating <= HIGH_MAX_CIRCULATING_CAP
    out["high_left_pressure_ok"] = check_left_pressure(history)
    out["eligible"] = all(
        bool(out[key])
        for key in (
            "high_history_ok", "high_auction_price_ok", "high_avg_inc_ok",
            "high_amount_ok", "high_market_cap_ok", "high_circulating_cap_ok",
            "high_auction_volume_rule_ok", "high_open_band_ok", "high_left_pressure_ok",
        )
    )
    return out
