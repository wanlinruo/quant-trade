"""字典类型的构造期约束。

这些约束是第一道闸门：类型错配在构造时就炸，而不是等到某条规则跑出
奇怪结果才发现。
"""

import pytest

from qsys.data.catalog.types import (
    UNCOVERED,
    UNKNOWN,
    Access,
    Computed,
    Direct,
    Kind,
    Metric,
    MetricStatus,
    Source,
    SourceStatus,
    Validity,
)


def _direct_metric(**over) -> Metric:
    base = dict(
        id="tushare_daily.close",
        kind=Kind.DIRECT,
        validity=Validity.SESSION,
        status=MetricStatus.ACTIVE,
        meaning="不复权收盘价",
        lineage=Direct(source_id="tushare.daily", field="close"),
        checks=("A1.dimension",),
    )
    base.update(over)
    return Metric(**base)


def test_splits_id_into_dataset_and_column():
    m = _direct_metric()
    assert m.dataset == "tushare_daily"
    assert m.column == "close"


def test_direct_metric_must_carry_direct_lineage():
    with pytest.raises(ValueError, match="DIRECT"):
        _direct_metric(lineage=Computed(upstream=("a.b",), formula="x"))


def test_derived_metric_must_not_claim_a_source_field():
    """DIRECT 不得有上游、DERIVED 不得直接指源——这是分类判据的构造期形态。"""
    with pytest.raises(ValueError, match="DERIVED"):
        _direct_metric(kind=Kind.DERIVED)


def test_empty_checks_tuple_is_rejected():
    """空元组分不清「忘了挂」和「确实不需要」，必须写 UNCOVERED。"""
    with pytest.raises(ValueError, match="UNCOVERED"):
        _direct_metric(checks=())


def test_uncovered_records_a_reason():
    m = _direct_metric(checks=UNCOVERED("运行时日志，无外部真值可比"))
    assert m.checks.reason == "运行时日志，无外部真值可比"


def test_unknown_meaning_is_a_shared_sentinel():
    """UNKNOWN 必须是可被页面精确识别的哨兵，不是随手写的中文。"""
    assert _direct_metric(meaning=UNKNOWN).meaning is UNKNOWN


def test_metric_status_and_source_status_are_distinct_enums():
    """bars_daily 是 Metric.FROZEN 而其源 mootdx 是 Source.DEAD——
    指标冻结是我们的决定，源死亡是上游的事实，不可混为一谈。"""
    assert type(MetricStatus.FROZEN) is not type(SourceStatus.FROZEN)
    assert not isinstance(SourceStatus.DEAD, MetricStatus)


def test_source_carries_access_and_expiry():
    s = Source(
        id="tushare.daily", provider="Tushare Pro", api="daily",
        access=Access.API, status=SourceStatus.ACTIVE,
        credential="config/deploy.local.toml [tushare]", tier="2000 积分",
        acquired="2026-09-13 购入 ¥500/年", cadence="工作日 17:00 + 19:00",
    )
    assert s.access is Access.API
    assert s.expires is None
