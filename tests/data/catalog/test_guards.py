"""registry 的守护测试。

这些测试是字典不腐烂的全部依靠。每一条都对应一种真实事故，
删任何一条都等于把那种事故放回来。
"""

from qsys.data import schema
from qsys.data.catalog import (
    OWNED_DATASETS,
    REGISTRY,
    SOURCES,
    consumers,
    expected_metric_ids,
    landed,
    root_sources,
)
from qsys.data.catalog.types import Computed, Direct, Uncovered
from qsys.data.pipeline.tushare_daily import _FROM_BASIC, _FROM_DAILY


def test_expected_ids_cover_schema_and_read_only_sources():
    """402 = schema.ALL 的 365 列 + 两张只读外部事实表的 37 列。

    paid_daily 故意不在 schema.ALL 里（那会让 Warehouse.write 成为可能），
    但它是 2026-09-11 前名称的唯一真值源，必须进字典。
    """
    ids = expected_metric_ids()
    assert len(ids) == 402, (
        f"期望 402 条（schema.ALL 的 365 列 + 只读事实 37 列），实得 {len(ids)}。"
        "schema 若有增删，先确认变动是否预期，再改这个数字。"
    )


def test_registry_covers_exactly_the_datasets_it_claims_to_own():
    """条目模块声明负责哪些表，就必须把那些表的列登记完整。

    这条在建设期就能跑绿——它管的是「已认领的有没有做完」。
    """
    for dataset in OWNED_DATASETS:
        cols = set((schema.ALL | schema.READ_ONLY)[dataset].names)
        got = {m.column for m in REGISTRY.values() if m.dataset == dataset}
        assert got == cols, f"{dataset} 登记不全：缺 {cols - got}，多 {got - cols}"


def test_all_datasets_are_owned_by_some_module():
    """**这条在 Task 4-8 全部完成前是红的，那是它的用途。**

    它红着就说明还有表没人认领。不设允许清单——允许清单会被忘掉，
    红色测试不会。
    """
    every = set(schema.ALL) | set(schema.READ_ONLY)
    assert OWNED_DATASETS == every, f"还没认领：{every - OWNED_DATASETS}"


def test_registry_ids_are_exactly_the_expected_landed_ids():
    """T1 完备性：缺一个失败，多一个也失败。

    拦的是「往 schema 加列忘登记」——页面上会少一个指标而无人知晓。
    """
    assert set(landed()) == expected_metric_ids()


def test_frozen_datasets_are_not_writable():
    """T6：把 schema.py 注释里那句「不要注册进 PARTITIONED」升级成测试。

    注册进去就让 Warehouse.write("paid_daily", ...) 成为可能，
    而 paid_daily 是永久只读的历史证据。
    """
    assert "paid_daily" not in schema.PARTITIONED
    assert "paid_daily" not in schema.UNPARTITIONED
    assert "paid_minute_bars" not in schema.PARTITIONED
    assert "paid_minute_bars" not in schema.UNPARTITIONED


def test_every_upstream_reference_resolves():
    """T2 上半：血缘指向 registry 中真实存在的指标。"""
    for metric in REGISTRY.values():
        if isinstance(metric.lineage, Computed):
            for upstream in metric.lineage.upstream:
                assert upstream in REGISTRY, (
                    f"{metric.id} 的上游 {upstream} 不存在"
                )


def test_lineage_graph_is_acyclic_except_self_lookback():
    """T2 下半：允许跨日自引用，不允许 A→B→A 这种真环。"""

    def walk(metric_id: str, path: tuple[str, ...]) -> None:
        metric = REGISTRY[metric_id]
        if isinstance(metric.lineage, Direct):
            return
        for upstream in metric.lineage.upstream:
            if upstream == metric_id:
                continue
            assert upstream not in path, (
                f"血缘成环：{' -> '.join(path + (upstream,))}"
            )
            walk(upstream, path + (upstream,))

    for metric_id in REGISTRY:
        walk(metric_id, (metric_id,))


def test_every_direct_metric_points_at_a_registered_source():
    """T2 补充：直取必须指向 sources.py 中登记过的源。"""
    for metric in REGISTRY.values():
        if isinstance(metric.lineage, Direct):
            assert metric.lineage.source_id in SOURCES, (
                f"{metric.id} 指向未登记的源 {metric.lineage.source_id}"
            )


def test_tushare_daily_lineage_is_generated_from_the_pipeline_mapping():
    """T3：字典与生产映射同源，字段名和乘数不能各写一份后漂移。"""
    for ours, theirs, multiplier in _FROM_DAILY:
        lineage = REGISTRY[f"tushare_daily.{ours}"].lineage
        assert lineage == Direct("tushare.daily", theirs, multiplier), ours
    for ours, theirs, multiplier in _FROM_BASIC:
        lineage = REGISTRY[f"tushare_daily.{ours}"].lineage
        assert lineage == Direct("tushare.daily_basic", theirs, multiplier), ours


def test_the_pipeline_multipliers_are_pinned_to_measured_values():
    """T3 的另一半：把已经逐键实测过的单位换算钉死。"""
    daily = {ours: multiplier for ours, _, multiplier in _FROM_DAILY}
    basic = {ours: multiplier for ours, _, multiplier in _FROM_BASIC}
    assert daily["amount"] == 1000.0
    assert daily["volume"] == 1.0
    for column in ("total_shares", "circulating_shares", "free_shares"):
        assert basic[column] == 10000.0
    for column in ("market_cap", "circulating_market_cap"):
        assert basic[column] == 10000.0
    for column in ("pe", "pe_ttm", "pb", "turnover_ratio"):
        assert basic[column] == 1.0


def test_consumers_are_derived_not_declared():
    """T4：反向关系由血缘推导，不维护第二份手写清单。"""
    assert "daily_flags.limit_pct" in consumers("daily_flags.limit_up")
    assert consumers("daily_flags.is_first_board") == () or all(
        consumer in REGISTRY
        for consumer in consumers("daily_flags.is_first_board")
    )


def test_root_sources_reach_interface_level():
    roots = root_sources("daily_flags.is_first_board")
    assert "tushare.daily.close" in roots
    assert all("." in root for root in roots)


def test_every_metric_is_checked_or_explicitly_uncovered():
    """T5：没有规则的条目必须明确写出未覆盖理由。"""
    for metric in REGISTRY.values():
        assert isinstance(metric.checks, (tuple, Uncovered)), metric.id
        if isinstance(metric.checks, tuple):
            assert metric.checks, metric.id


def test_every_referenced_check_id_will_be_implemented():
    """规则引用完备性；Task 11–15 建完规则前故意保持红灯。"""
    from qsys.data.catalog.checks import CHECKS

    referenced = {
        check_id
        for metric in REGISTRY.values()
        if isinstance(metric.checks, tuple)
        for check_id in metric.checks
    }
    missing = referenced - set(CHECKS)
    assert not missing, f"字典引用了尚未实现的规则：{sorted(missing)}"


def test_no_metric_remains_explicitly_uncovered():
    """基础数据契约必须覆盖无法做外部交叉真值的字段。"""
    uncovered = [
        metric.id for metric in REGISTRY.values()
        if isinstance(metric.checks, Uncovered)
    ]
    assert not uncovered, f"仍未覆盖：{uncovered}"


def test_contract_checks_and_metrics_are_bidirectionally_registered():
    """防止“指标挂了规则、规则却不为指标写结果行”的未验证假象。"""
    from qsys.data.catalog.checks import CHECKS

    for metric in REGISTRY.values():
        contract = (
            f"C6.storage_contract.{metric.dataset}"
            if metric.landed else "C7.catalog_contract.strategy"
        )
        assert contract in metric.checks, metric.id
        assert metric.id in CHECKS[contract].metrics, metric.id
