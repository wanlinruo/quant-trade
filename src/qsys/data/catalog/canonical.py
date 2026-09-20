"""阶段 4–5 规范窄表、状态与派生表的字段契约。

当前仅并行投影/重命名，不切换消费者。每个字段都指向唯一旧契约，后续来源
表冻结时再把根血缘提升到外部接口，迁移期间仍可做逐键逐字段零差异对账。
"""

from __future__ import annotations

from qsys.data import schema
from qsys.data.catalog.types import (
    UNCOVERED,
    Computed,
    Kind,
    Metric,
    MetricStatus,
    Validity,
)

DATASETS = frozenset({
    "bar_daily", "valuation_daily", "share_capital_history",
    "adjustment_factor_daily", "price_limit_daily", "security_status_daily",
    "market_flags_daily", "market_regime_daily", "bar_minute_1m",
})

_CHECKS = UNCOVERED("阶段 4 由逐键逐字段对账、表契约与关系检查验证")


def _passthrough(
    dataset: str,
    column: str,
    upstream: str,
    meaning: str,
    validity: Validity = Validity.SESSION,
) -> Metric:
    return Metric(
        id=f"{dataset}.{column}", kind=Kind.DERIVED, validity=validity,
        status=MetricStatus.ACTIVE, meaning=meaning,
        lineage=Computed(
            (upstream,), f"阶段 4 并行迁移逐值投影 {upstream}"
        ),
        checks=_CHECKS,
    )


_BAR_MEANING = {
    "symbol": "规范证券代码", "dt": "交易日", "pre_close": "交易所口径前收盘价（元）",
    "open": "不复权开盘价（元）", "high": "不复权最高价（元）",
    "low": "不复权最低价（元）", "close": "不复权收盘价（元）",
    "volume": "成交量（手）", "amount": "成交额（元）",
}

_VALUATION_MEANING = {
    "symbol": "规范证券代码", "dt": "交易日",
    "turnover_ratio": "换手率（%）", "free_turnover_ratio": "自由流通换手率（%）",
    "volume_ratio": "量比", "pe": "市盈率", "pe_ttm": "滚动市盈率",
    "pb": "市净率", "ps": "市销率", "ps_ttm": "滚动市销率",
    "dividend_yield": "股息率（%）", "dividend_yield_ttm": "滚动股息率（%）",
    "market_cap": "总市值（元）", "circulating_market_cap": "流通市值（元）",
}


def _metrics() -> list[Metric]:
    out = [
        _passthrough("bar_daily", column, f"tushare_daily.{column}", meaning)
        for column, meaning in _BAR_MEANING.items()
    ]
    out.extend(
        _passthrough("valuation_daily", column, f"tushare_daily.{column}", meaning)
        for column, meaning in _VALUATION_MEANING.items()
    )
    capital = {
        "symbol": ("share_capital.symbol", "证券代码"),
        "effective_date": ("share_capital.dt", "股本生效日，不是每日快照观测日"),
        "announcement_date": ("share_capital.ann_date", "公告日"),
        "total_shares": ("share_capital.total", "总股本（股）"),
        "circulating_shares": ("share_capital.circulating", "流通股本（股）"),
    }
    out.extend(
        _passthrough(
            "share_capital_history", column, upstream, meaning, Validity.SPAN
        )
        for column, (upstream, meaning) in capital.items()
    )
    factors = {
        "symbol": ("adj_factors.symbol", "证券代码"),
        "dt": ("adj_factors.dt", "交易日"),
        "price_factor": ("adj_factors.cum_factor", "价格累计后复权因子"),
        "share_factor": ("adj_factors.cum_share_factor", "股本累计因子"),
    }
    out.extend(
        _passthrough("adjustment_factor_daily", column, upstream, meaning)
        for column, (upstream, meaning) in factors.items()
    )
    price_limits = {
        name: (f"price_limits_daily.{name}", meaning)
        for name, meaning in {
            "symbol": "证券代码", "dt": "交易日",
            "pre_close": "交易所口径前收盘价", "limit_up": "涨停价",
            "limit_down": "跌停价",
        }.items()
    }
    out.extend(
        _passthrough("price_limit_daily", column, upstream, meaning)
        for column, (upstream, meaning) in price_limits.items()
    )
    status = {
        "symbol": (("bar_daily.symbol", "stock_st_daily.symbol", "suspend_daily.symbol",
                    "delisting_pool.symbol"), "证券代码"),
        "dt": (("bar_daily.dt", "stock_st_daily.dt", "suspend_daily.dt",
                "delisting_pool.dt"), "状态观测日"),
        "is_st": (("stock_st_daily.is_st", "delisting_pool.symbol"),
                  "ST 真值；退市整理状态显式覆盖为 false"),
        "is_suspended": (("suspend_daily.suspend_type", "bar_daily.symbol"),
                         "当日存在停牌事件为 true；同日兼有复牌事件时仍保留曾停牌事实；"
                         "仅复牌或存在当日行情为 false，其余未知"),
        "is_delisting": (("delisting_pool.symbol", "bar_daily.symbol"),
                         "处于已公告退市整理区间"),
        "st_source": (("stock_st_daily.is_st", "delisting_pool.symbol"), "ST 状态来源"),
        "suspension_source": (("suspend_daily.suspend_type", "bar_daily.symbol"),
                              "停复牌状态来源"),
        "delisting_source": (("delisting_pool.symbol",), "退市整理状态来源"),
    }
    out.extend(
        Metric(
            id=f"security_status_daily.{column}", kind=Kind.DERIVED,
            validity=Validity.SESSION, status=MetricStatus.ACTIVE, meaning=meaning,
            lineage=Computed(upstream, "阶段 5 统一状态构建；未知值不填充"),
            checks=_CHECKS,
        )
        for column, (upstream, meaning) in status.items()
    )
    flag_upstream = {
        "dt": ("bar_daily.dt",), "symbol": ("bar_daily.symbol",),
        "prev_close": ("bar_daily.close",),
        "adj_prev_close": ("price_limit_daily.pre_close", "bar_daily.pre_close"),
        "limit_up": ("price_limit_daily.limit_up",),
        "limit_down": ("price_limit_daily.limit_down",),
        "is_limit_up": ("bar_daily.close", "price_limit_daily.limit_up",
                        "limit_list_daily.limit_type", "security_status_daily.is_st"),
        "is_limit_down": ("bar_daily.close", "price_limit_daily.limit_down"),
        "touched_limit_up": ("bar_daily.high", "price_limit_daily.limit_up"),
        "is_broken_board": ("bar_daily.high", "bar_daily.close",
                            "price_limit_daily.limit_up", "limit_list_daily.limit_type"),
        "is_first_board": ("price_limit_daily.limit_up", "bar_daily.close"),
        "limit_pct": ("price_limit_daily.pre_close", "price_limit_daily.limit_up"),
        "is_st": ("security_status_daily.is_st",),
        "st_source": ("security_status_daily.st_source",),
    }
    out.extend(
        Metric(
            id=f"market_flags_daily.{column}", kind=Kind.DERIVED,
            validity=Validity.SESSION, status=MetricStatus.ACTIVE,
            meaning=f"规范市场标志：{column}",
            lineage=Computed(flag_upstream[column], "阶段 5 从规范行情与状态事实独立重算"),
            checks=_CHECKS,
        )
        for column in schema.MARKET_FLAGS_DAILY.names
    )
    regime_common = (
        "market_flags_daily.is_limit_up", "market_flags_daily.is_limit_down",
        "market_flags_daily.touched_limit_up", "market_flags_daily.is_broken_board",
        "market_flags_daily.is_first_board", "bar_daily.close", "bar_daily.amount",
        "adjustment_factor_daily.price_factor",
    )
    out.extend(
        Metric(
            id=f"market_regime_daily.{column}", kind=Kind.DERIVED,
            validity=Validity.SESSION, status=MetricStatus.ACTIVE,
            meaning=f"规范市场生态：{column}",
            lineage=Computed(
                ("bar_daily.dt",) if column == "dt" else (
                    ("index_bars.close",) if column == "index_ma20_slope_5d"
                    else regime_common
                ),
                "阶段 5 从规范行情、状态、因子和指数独立聚合",
            ),
            checks=_CHECKS,
        )
        for column in schema.MARKET_REGIME_DAILY.names
    )
    minute_upstream = {
        "symbol": ("paid_minute_bars.symbol", "minute_bars.symbol"),
        "ts": ("paid_minute_bars.dt", "minute_bars.dt"),
        "open": ("paid_minute_bars.open", "minute_bars.open"),
        "high": ("paid_minute_bars.high", "minute_bars.high"),
        "low": ("paid_minute_bars.low", "minute_bars.low"),
        "close": ("paid_minute_bars.close", "minute_bars.close"),
        "volume": ("paid_minute_bars.volume", "minute_bars.volume"),
        "amount": ("paid_minute_bars.amount", "minute_bars.amount"),
        "source": ("paid_minute_bars.symbol", "minute_bars.symbol"),
        "visible_at": ("paid_minute_bars.dt", "minute_bars.dt"),
    }
    out.extend(
        Metric(
            id=f"bar_minute_1m.{column}", kind=Kind.DERIVED,
            validity=Validity.POINT, status=MetricStatus.ACTIVE,
            meaning=f"规范一分钟定稿行情：{column}",
            lineage=Computed(
                minute_upstream[column],
                "阶段 6 按标的日唯一选源；成交量统一为手并显式保留来源与可见时刻",
            ),
            checks=_CHECKS,
        )
        for column in schema.BAR_MINUTE_1M.names
    )
    return out


METRICS: tuple[Metric, ...] = tuple(_metrics())
