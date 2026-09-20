from collections import Counter

from qsys.data.catalog.canonical import DATASETS, METRICS
from qsys.data.catalog.types import Computed, Validity


BY_ID = {item.id: item for item in METRICS}


def test_owns_and_covers_stage4_and_stage5_canonical_tables():
    assert DATASETS == {
        "bar_daily", "valuation_daily", "share_capital_history",
        "adjustment_factor_daily", "price_limit_daily", "security_status_daily",
        "market_flags_daily", "market_regime_daily",
        "bar_minute_1m",
    }
    assert dict(Counter(item.dataset for item in METRICS)) == {
        "bar_daily": 9,
        "valuation_daily": 14,
        "share_capital_history": 5,
        "adjustment_factor_daily": 4,
        "price_limit_daily": 5,
        "security_status_daily": 8,
        "market_flags_daily": 14,
        "market_regime_daily": 15,
        "bar_minute_1m": 10,
    }


def test_capital_effective_date_is_not_derived_from_daily_snapshots():
    metric = BY_ID["share_capital_history.effective_date"]
    assert metric.validity is Validity.SPAN
    assert metric.lineage == Computed(
        ("share_capital.dt",),
        "阶段 4 并行迁移逐值投影 share_capital.dt",
    )


def test_daily_topics_have_distinct_upstreams():
    assert BY_ID["bar_daily.close"].lineage.upstream == ("tushare_daily.close",)
    assert BY_ID["valuation_daily.market_cap"].lineage.upstream == (
        "tushare_daily.market_cap",
    )
    assert BY_ID["adjustment_factor_daily.share_factor"].lineage.upstream == (
        "adj_factors.cum_share_factor",
    )


def test_stage5_flags_and_regime_do_not_depend_on_legacy_derived_tables():
    stage5 = [
        metric for metric in METRICS
        if metric.dataset in {"market_flags_daily", "market_regime_daily"}
    ]
    upstream = {
        item
        for metric in stage5
        for item in metric.lineage.upstream
    }
    assert not any(item.startswith("daily_flags.") for item in upstream)
    assert not any(item.startswith("market_regime.") for item in upstream)
    assert {"bar_daily.close", "security_status_daily.is_st"} <= upstream
    assert "adjustment_factor_daily.price_factor" in upstream


def test_status_contract_exposes_provenance_and_null_preserving_inputs():
    assert BY_ID["security_status_daily.is_st"].lineage == Computed(
        ("stock_st_daily.is_st", "delisting_pool.symbol"),
        "阶段 5 统一状态构建；未知值不填充",
    )
    assert "suspend_daily.suspend_type" in (
        BY_ID["security_status_daily.is_suspended"].lineage.upstream
    )
