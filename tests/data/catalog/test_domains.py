from dataclasses import replace

import pytest

from qsys.data.catalog import DATASETS
from qsys.data.catalog.domains import (
    SourceEntryKind,
    classify_entry,
    dataset_root_sources,
    domain_summary,
    unclassified_summary,
)
from qsys.data.catalog.types import UNCLASSIFIED, DataDomain


def _sources(domain: DataDomain) -> set[str]:
    row = next(r for r in domain_summary() if r.domain is domain)
    return {s.id for s in row.sources}


def test_every_domain_appears_including_the_empty_ones():
    """宏观与另类必须出现在总览里——0 张表本身是信息。"""
    rows = {row.domain for row in domain_summary()}
    assert rows == set(DataDomain)


def test_market_domain_names_the_frozen_paid_source():
    """行情域必须显示付费源，且它是 FROZEN——用户曾因此以为它不再使用。"""
    market = next(r for r in domain_summary() if r.domain is DataDomain.MARKET)
    assert "baidupan.minute" in {s.id for s in market.sources}


def test_empty_domains_have_no_sources():
    for row in domain_summary():
        if row.domain in (DataDomain.MACRO, DataDomain.ALTERNATIVE):
            assert row.datasets == () and row.sources == ()


# ---------------------------------------------------------------- 递归血缘


def test_minute_canonical_resolves_through_two_upstream_tables():
    """`bar_minute_1m.sources` 一个源字符串都没有：两个表 id + 一句备注。

    只走一跳它会显示「无源」——而它正是 2026-10-05 那次「以为付费源已停用」
    的那张表。递归必须解出付费源与 QMT 两个接口。
    """
    assert dataset_root_sources("bar_minute_1m") == (
        "baidupan.minute", "qmt.minute",
    )


def test_canonical_daily_resolves_to_the_three_tushare_interfaces():
    """`bar_daily` 上游只写了 `tushare_daily` 一张表。"""
    assert dataset_root_sources("bar_daily") == (
        "tushare.adj_factor", "tushare.daily", "tushare.daily_basic",
    )


def test_fundamental_domain_names_tushare_as_spec_5_1_expects():
    """spec §5.1 自己写的预期是「基本面 Fundamental ← Tushare Pro」。

    一跳只能看到 `qmt.capital`——估值两张表的上游是 `tushare_daily` 这张表，
    不是源 id。页面照一跳显示等于说「估值数据来自 QMT」，是错的。
    """
    fundamental = _sources(DataDomain.FUNDAMENTAL)
    assert {"tushare.daily", "tushare.daily_basic", "tushare.adj_factor"} <= fundamental
    assert "qmt.capital" in fundamental  # share_capital 确实来自 QMT


def test_factor_domain_reaches_every_leaf_interface():
    """衍生域一跳只有 1 个源（`tushare.adj_factor`），递归后 10 个。"""
    factor = _sources(DataDomain.FACTOR)
    assert factor == {
        "qmt.dividends",
        "tushare.adj_factor",
        "tushare.daily",
        "tushare.daily_basic",
        "tushare.index_daily",
        "tushare.limit_list_d",
        "tushare.namechange",
        "tushare.stk_limit",
        "tushare.stock_st",
        "tushare.suspend_d",
    }


def test_only_free_text_upstreams_stay_unresolved():
    """解不出就是解不出：自由文本不许反推成某个源（见 MEMORY 的禁反推规则）。

    47 张表里只有这三张的上游**全部**是自由文本，它们必须解出空集，
    而不是被猜成最像的那个源。
    """
    unresolved = {d.id for d in DATASETS.values() if not dataset_root_sources(d.id)}
    assert unresolved == {"daily_flags", "metric_evidence", "strategy"}


def test_every_resolved_root_is_a_real_registered_source():
    for dataset_id in DATASETS:
        for root in dataset_root_sources(dataset_id):
            assert classify_entry(root) is SourceEntryKind.SOURCE


def test_recursion_terminates_even_when_the_lineage_has_a_cycle(monkeypatch):
    """当前 47 张表无环，但「无环」是事实不是约束。加一张表就可能不成立，
    而没有 `seen` 的递归遇到环就是无限递归，不是错结果。"""
    monkeypatch.setitem(
        DATASETS, "bar_daily",
        replace(DATASETS["bar_daily"], sources=("tushare_daily", "valuation_daily")),
    )
    monkeypatch.setitem(
        DATASETS, "valuation_daily",
        replace(DATASETS["valuation_daily"], sources=("bar_daily",)),
    )
    assert "tushare.daily" in dataset_root_sources("bar_daily")


def test_classify_entry_tells_sources_tables_and_notes_apart():
    assert classify_entry("baidupan.minute") is SourceEntryKind.SOURCE
    assert classify_entry("minute_bars") is SourceEntryKind.TABLE
    assert classify_entry("显式异常覆盖") is SourceEntryKind.NOTE


# ------------------------------------------------------------------ 未归类


def test_classified_and_unclassified_together_cover_every_table():
    """合计必须等于 `DATASETS`。差一张就说明有表从页面上消失了。"""
    counted = sum(len(row.datasets) for row in domain_summary())
    counted += len(unclassified_summary().datasets)
    assert counted == len(DATASETS)


def test_an_unclassified_table_is_surfaced_with_its_reason(monkeypatch):
    """spec §3.2 换来的承诺是「页面上永远不会有归属不明的表」。

    只按 `for domain in DataDomain` 遍历会把它实现成「归属不明的表不会在
    页面上」——字面满足、意图相反。
    """
    monkeypatch.setitem(
        DATASETS, "strategy",
        replace(DATASETS["strategy"], domain=UNCLASSIFIED("策略决策量，待判")),
    )
    row = unclassified_summary()
    assert [d.id for d in row.datasets] == ["strategy"]
    assert row.reasons == (("strategy", "策略决策量，待判"),)
    # 并且它不再被算进 runtime 域，合计仍然是 47
    runtime = next(r for r in domain_summary() if r.domain is DataDomain.RUNTIME)
    assert "strategy" not in {d.id for d in runtime.datasets}
    assert sum(len(r.datasets) for r in domain_summary()) + 1 == len(DATASETS)


def test_unclassified_rows_still_resolve_their_root_sources(monkeypatch):
    monkeypatch.setitem(
        DATASETS, "bar_minute_1m",
        replace(DATASETS["bar_minute_1m"], domain=UNCLASSIFIED("跨域，待判")),
    )
    assert {s.id for s in unclassified_summary().sources} == {
        "baidupan.minute", "qmt.minute",
    }


def test_a_bare_string_domain_cannot_be_constructed():
    """裸字符串最危险：`isinstance(..., DataDomain)` 过滤会让它从所有域
    页面静默消失，既不报错也不出现。必须在构造期拦住（spec §3.2）。"""
    with pytest.raises(ValueError):
        replace(DATASETS["bar_daily"], domain="market")


def test_unclassified_without_a_reason_cannot_be_constructed():
    with pytest.raises(ValueError):
        replace(DATASETS["bar_daily"], domain=UNCLASSIFIED(""))
    with pytest.raises(ValueError):
        replace(DATASETS["bar_daily"], domain=UNCLASSIFIED("   "))
