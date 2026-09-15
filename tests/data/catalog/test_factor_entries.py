"""因子类条目。因子的正确性还包括股票池、窗口和阈值口径。"""

from collections import Counter

from qsys.data.catalog.factors import DATASETS, METRICS
from qsys.data.catalog.types import Kind

BY_ID = {m.id: m for m in METRICS}

_STRATEGY_IDS = {
    "strategy.stock_return_10d",
    "strategy.distance_to_60d_high",
    "strategy.low_relative_position_60d",
    "strategy.low_auction_price_ratio",
    "strategy.high_avg_inc",
    "strategy.high_auction_volume_ratio",
    "strategy.high_auction_price_ratio",
    "strategy.low_main_auction_volume_ratio",
    "strategy.left_pressure_days",
    "strategy.left_pressure_volume_ratio",
}


def test_owns_only_market_regime():
    assert DATASETS == frozenset({"market_regime"})


def test_registers_all_fifteen_regime_columns():
    assert Counter(m.dataset for m in METRICS)["market_regime"] == 15


def test_strategy_inventory_is_exact_and_unlanded():
    actual = {m.id for m in METRICS if m.dataset == "strategy"}
    assert actual == _STRATEGY_IDS
    assert all(BY_ID[mid].landed is False for mid in actual)


def test_breadth_factors_record_their_universe_choice():
    metric = BY_ID["market_regime.market_advance_ratio"]
    assert metric.kind is Kind.FACTOR
    assert "universe" in dict(metric.lineage.params)
    assert "D-3" in metric.known_issues


def test_index_slope_records_both_windows():
    params = dict(BY_ID["market_regime.index_ma20_slope_5d"].lineage.params)
    assert params["ma_window"] == 20
    assert params["lookback"] == 5


def test_index_slope_is_null_without_index_data_not_zero():
    metric = BY_ID["market_regime.index_ma20_slope_5d"]
    assert "C1.no_fill" in metric.checks
    assert "不填 0" in metric.meaning or "留空" in metric.meaning


def test_gate_inputs_declare_the_strategy_as_an_external_consumer():
    for metric_id in (
        "market_regime.index_ma20_slope_5d",
        "market_regime.market_advance_ratio",
        "market_regime.touched_limit_up_count",
        "market_regime.market_median_return_1d",
        "market_regime.prior_limitup_nextday_return_mean",
    ):
        assert any(
            "S6" in consumer or "S7" in consumer
            for consumer in BY_ID[metric_id].ext_consumers
        ), metric_id


def test_thresholds_pending_recalibration_are_flagged():
    assert "D-4" in BY_ID["market_regime.market_advance_ratio"].known_issues


def test_stock_return_10d_records_its_window():
    params = dict(BY_ID["strategy.stock_return_10d"].lineage.params)
    assert params["window"] == 10


def test_advance_ratio_denominator_excludes_flat_stocks():
    """生产公式是上涨/(上涨+下跌)，不是上涨/valid_stock_count。"""
    formula = BY_ID["market_regime.market_advance_ratio"].lineage.formula
    assert "上涨+下跌" in formula
    assert "平盘" in formula


def test_high_avg_inc_records_the_lot_conversion():
    """日线 volume 是手，少乘 100 会把均价放大百倍。"""
    formula = BY_ID["strategy.high_avg_inc"].lineage.formula
    assert "100" in formula
    assert "手" in BY_ID["strategy.high_avg_inc"].meaning


def test_auction_inputs_match_the_actual_market_feed_paths():
    """开盘价来自日线单列放行，竞价量来自 09:30 分钟 bar。"""
    price_upstream = BY_ID["strategy.high_auction_price_ratio"].lineage.upstream
    volume_upstream = BY_ID["strategy.high_auction_volume_ratio"].lineage.upstream
    assert "tushare_daily.open" in price_upstream
    assert "minute_bars.volume" in volume_upstream
    assert all(not item.startswith("auction_ticks.") for item in price_upstream)
    assert all(not item.startswith("auction_ticks.") for item in volume_upstream)


def test_every_factor_has_explicit_parameters_or_universe():
    for metric in METRICS:
        if metric.kind is Kind.FACTOR:
            assert metric.lineage.params, (
                f"{metric.id} 标为 FACTOR 却没有参数或口径"
            )
