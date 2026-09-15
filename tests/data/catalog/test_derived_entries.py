"""派生类条目。公式与上游必须写实，它们是 A2 独立重算的依据。"""

from qsys.data.catalog.derived import DATASETS, METRICS
from qsys.data.catalog.types import Computed, Kind, Validity

BY_ID = {m.id: m for m in METRICS}


def test_owns_two_derived_datasets():
    assert DATASETS == frozenset({"daily_flags", "valuation_snapshot"})


def test_registers_twenty_metrics():
    assert len(METRICS) == 20


def test_nothing_here_claims_to_be_direct():
    """两张表的生产路径都经过计算或透传，不能伪装成源字段直取。"""
    for metric in METRICS:
        assert metric.kind is not Kind.DIRECT, metric.id
        assert metric.validity is Validity.SESSION, metric.id
        assert isinstance(metric.lineage, Computed), metric.id


def test_limit_up_prefers_the_direct_price_over_a_band_derivation():
    """涨停价优先直取交易所公布值；自算只是未回填历史的兜底。

    依赖方向与 2026-09-15 之前相反：那时 limit_up 由 limit_pct 算出，
    现在 limit_pct 由 limit_up 解码而来。自算兜底仍按除权参考价，
    不是按普通的上一根收盘。
    """
    metric = BY_ID["daily_flags.limit_up"]
    assert "price_limits_daily.limit_up" in metric.lineage.upstream
    assert "daily_flags.adj_prev_close" in metric.lineage.upstream
    assert "daily_flags.limit_pct" not in metric.lineage.upstream
    assert "daily_flags.prev_close" not in metric.lineage.upstream


def test_limit_pct_is_decoded_from_the_price_not_from_st_status():
    """2026-07-06 起主板 ST 与普通股同为 10%，档位不能再由 ST 派生。"""
    metric = BY_ID["daily_flags.limit_pct"]
    assert "daily_flags.limit_up" in metric.lineage.upstream
    assert "D-13" in metric.known_issues


def test_is_first_board_needs_yesterday():
    metric = BY_ID["daily_flags.is_first_board"]
    assert "daily_flags.is_limit_up" in metric.lineage.upstream
    assert "昨日" in metric.lineage.formula


def test_first_observation_is_treated_as_not_limit_up_yesterday():
    """生产实现 shift(fill_value=False)，字典不能声称首根结果为 null。"""
    assert "False" in BY_ID["daily_flags.is_first_board"].lineage.formula


def test_is_st_traces_back_to_all_four_name_sources():
    up = set(BY_ID["daily_flags.is_st"].lineage.upstream)
    assert {
        "tushare_daily.name",
        "paid_daily.name",
        "name_snapshots.name",
        "name_history.name",
    } <= up


def test_st_source_records_which_level_won():
    assert "来源" in BY_ID["daily_flags.st_source"].meaning


def test_adj_prev_close_is_a_passthrough_not_a_recomputation():
    """仍是透传，只是多了一个优先级更高的直取候选。"""
    metric = BY_ID["daily_flags.adj_prev_close"]
    assert metric.lineage.upstream == (
        "price_limits_daily.pre_close",
        "tushare_daily.pre_close",
    )


def test_valuation_projects_daily_basic_instead_of_reverse_calculating():
    """2026-09-15 起市值与换手率直接投影，不再用收盘价乘 QMT 股本反算。

    反算在 A+H 股上会差出上亿股；实测 4,801,648 行逐行核对，投影与源列
    的最大差为 0。
    """
    market_cap = BY_ID["valuation_snapshot.market_cap"]
    turnover = BY_ID["valuation_snapshot.turnover_ratio"]
    assert market_cap.lineage.upstream == ("tushare_daily.market_cap",)
    assert turnover.lineage.upstream == ("tushare_daily.turnover_ratio",)
    assert "share_capital.total" not in market_cap.lineage.upstream
    assert "A3.market_cap_vs_shares" in market_cap.checks


def test_every_upstream_is_spelled_like_a_metric_id():
    for metric in METRICS:
        for upstream in metric.lineage.upstream:
            assert "." in upstream, (
                f"{metric.id} 的上游 {upstream!r} 不是 metric id"
            )
