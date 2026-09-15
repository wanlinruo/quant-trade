"""参考类条目。validity 标错就会有人拿点观测去填历史。

R11/R13/R14（控制器裁决）：草案的 `_TABLE_SPEC` / `_MEANING` 未经核实，
逐列核对 pipeline 后有 8 列的 kind 需要改判、2 张表缺源（share_capital /
corporate_actions），corporate_actions 的真实源也从草案假定的 akshare
改判为 QMT。见 `catalog/reference.py` 模块文档字符串的完整清单与理由。
"""

from collections import Counter

from qsys.data.catalog.reference import DATASETS, METRICS
from qsys.data.catalog.types import (
    UNKNOWN,
    Computed,
    Direct,
    Kind,
    Uncovered,
    Validity,
)

BY_ID = {m.id: m for m in METRICS}


def test_owns_every_reference_dataset():
    assert DATASETS == frozenset({
        "instruments", "name_history", "name_snapshots", "price_limits_daily",
        "suspend_daily", "limit_list_daily", "corporate_actions",
        "share_capital", "adj_factors", "stock_st_daily",
    })


def test_registers_every_reference_column():
    """逐表核对列数。断总数只会说「54 != 55」，断分表能指出是哪张表短了。"""
    assert dict(Counter(m.dataset for m in METRICS)) == {
        "instruments": 7, "name_history": 6, "name_snapshots": 3,
        "price_limits_daily": 5, "suspend_daily": 4, "limit_list_daily": 9,
        "corporate_actions": 6, "share_capital": 5, "adj_factors": 4,
        "stock_st_daily": 6,
    }


def test_name_snapshot_is_a_point_observation():
    """stock_basic 返回的是**当前**名称。填取快照那一天是直接观测，
    填历史是结转。validity=POINT 就是这条规矩在字典里的形态。"""
    assert BY_ID["name_snapshots.name"].validity is Validity.POINT
    assert BY_ID["name_snapshots.dt"].meaning.startswith("观测日")


def test_instruments_are_point_observations_too():
    """instruments 是覆盖写的单张表只留最新一份，as_of 才让它可用。
    没有 as_of 就无法判断 board / list_date 有多陈旧。"""
    for m in METRICS:
        if m.dataset == "instruments":
            assert m.validity is Validity.POINT, m.id


def test_name_history_is_a_span_with_a_known_hole():
    """namechange 只记变更事件；2006-10-08 之后大量标的没有后继区间。"""
    m = BY_ID["name_history.name"]
    assert m.validity is Validity.SPAN
    assert "D-10" in m.known_issues
    assert "C3.span_gaps" in m.checks


def test_cum_factor_is_direct_but_carries_its_defect():
    """类型说的是它怎么来的，可信度说的是它对不对——两件事分开。
    R13 逐列核对后 cum_factor 仍是 DIRECT（无条件直取 tushare.adj_factor），
    只是源字段自身有 D-1/D-2 两条已知缺陷，这不影响它的类型判据。"""
    m = BY_ID["adj_factors.cum_factor"]
    assert m.kind is Kind.DIRECT
    assert m.known_issues == ("D-1", "D-2")
    assert "A3.missed_exdiv" in m.checks


def test_share_factor_is_distinct_from_price_factor():
    """价格因子含现金分红那一项，拿它调成交量会多调——
    股数只随送转与配股变化，与派现无关。"""
    assert "送转" in BY_ID["adj_factors.cum_share_factor"].meaning


def test_limit_list_columns_record_the_st_exclusion():
    """D-11：limit_list_d 不含 ST，接成首板源会静默丢掉全部 ST 首板。"""
    m = BY_ID["limit_list_daily.limit_type"]
    assert "ST" in m.meaning
    assert "D-11" in m.known_issues


def test_suspend_timing_null_means_all_day_not_missing():
    assert "全天停牌" in BY_ID["suspend_daily.suspend_timing"].meaning


def test_share_capital_uses_effective_date_not_announcement():
    """股本是公开可即时观察的事实，交易所每日据此发布总市值。"""
    assert "生效日" in BY_ID["share_capital.dt"].meaning
    assert "公告日" in BY_ID["share_capital.ann_date"].meaning


# --- R13：源字段名 ≠ 仓库列名，且不是每一列都是 DIRECT --------------------

def test_stk_limit_fields_are_renamed_from_up_down_limit():
    """stk_limit 接口实际给的是 up_limit / down_limit，仓库改名成
    limit_up / limit_down；Direct.field 必须记源的名字，不是仓库列名。"""
    assert BY_ID["price_limits_daily.limit_up"].lineage == Direct(
        "tushare.stk_limit", "up_limit"
    )
    assert BY_ID["price_limits_daily.limit_down"].lineage == Direct(
        "tushare.stk_limit", "down_limit"
    )


def test_pre_close_is_derived_because_it_has_two_candidate_sources():
    """stk_limit 响应缺 pre_close 时会回退连 tushare.daily 取——两个候选
    源字段不满足 DIRECT 要求的「唯一」，归 DERIVED。"""
    m = BY_ID["price_limits_daily.pre_close"]
    assert m.kind is Kind.DERIVED
    assert isinstance(m.lineage, Computed)


def test_instruments_market_is_a_mechanical_split_not_a_fetched_field():
    """market 是 ts_code.split('.').last()，机械字符串操作，无判断口径，
    归 DERIVED；board 含号段分类口径，归 FACTOR。"""
    market = BY_ID["instruments.market"]
    assert market.kind is Kind.DERIVED
    assert market.lineage == Computed(
        upstream=("instruments.symbol",), formula="symbol.split('.').last()"
    )
    assert BY_ID["instruments.board"].kind is Kind.FACTOR


def test_instruments_as_of_has_no_upstream():
    """as_of 是调用方传入的挂钟日期字面量，不来自任何 API 字段——
    与 tushare_daily.name 的 R10 判例同构：Computed(upstream=())。"""
    m = BY_ID["instruments.as_of"]
    assert m.kind is Kind.DERIVED
    assert m.lineage.upstream == ()


def test_name_snapshots_dt_equals_the_same_batch_as_of():
    """name_snapshots.dt 就是同一次同步产出的 instruments.as_of，
    不是 stock_basic 的字段，归 DERIVED。"""
    m = BY_ID["name_snapshots.dt"]
    assert m.kind is Kind.DERIVED
    assert m.lineage.upstream == ("instruments.as_of",)


def test_limit_type_is_the_callers_query_parameter_not_a_response_field():
    """ingest_limit_list 按 limit_type 参数分别请求接口，再把参数本身写成
    字面量；响应体不带这一列，指不到任何源字段，归 DERIVED。"""
    m = BY_ID["limit_list_daily.limit_type"]
    assert m.kind is Kind.DERIVED
    assert m.lineage.upstream == ()


def test_limit_list_meanings_are_supported_by_official_field_definitions():
    """原待查四列已按 Tushare limit_list_d 官方文档 doc_id=298 补证。"""
    for col in ("first_time", "fd_amount", "limit_amount", "float_mv"):
        meaning = BY_ID[f"limit_list_daily.{col}"].meaning
        assert meaning is not UNKNOWN
        assert meaning.strip()


# --- R14：corporate_actions 的真实源是 QMT，不是草案假定的 akshare -------

def test_corporate_actions_points_at_qmt_dividends_not_akshare():
    """pipeline/dividends.py 明说数据来自 QMT get_divid_factors——
    正是因为 akshare 的 stock_fhps_detail_em 不含配股。"""
    for m in METRICS:
        if m.dataset == "corporate_actions":
            if isinstance(m.lineage, Direct):
                assert m.lineage.source_id == "qmt.dividends", m.id
            else:
                assert (
                    "qmt.dividends" in m.lineage.formula
                    or "get_divid_factors" in m.lineage.formula
                ), m.id


def test_corporate_actions_symbol_is_derived_from_the_request_not_the_response():
    """review fix round 1：get_divid_factors(symbol) 逐个标的调用，
    响应体（以除权日为索引的 DataFrame）里没有任何字段携带标的身份——
    这一列是脚本按调用参数回填的，不是从响应读出来的。与 R10 的
    instruments.as_of / tushare_daily.name 同构：Computed(upstream=())，
    且没有独立源字段可供比对，checks 是 Uncovered 不是普通规则元组。"""
    m = BY_ID["corporate_actions.symbol"]
    assert m.kind is Kind.DERIVED
    assert isinstance(m.lineage, Computed)
    assert m.lineage.upstream == ()
    assert isinstance(m.checks, Uncovered)


def test_share_capital_symbol_stays_direct_because_the_response_is_keyed_by_it():
    """review fix round 1：与 corporate_actions.symbol 的区别在于
    get_financial_data 是**批量**调用，返回值本身是按标的建索引的字典
    （scripts/win_download_capital.py:111-116 批量请求，:122-123
    `data.get(symbol)` 按键取值）——标的身份来自响应体的结构（字典键），
    不是脚本凭空回填的入参，与 paid_daily.symbol 的 R9 判例同构，
    保留 DIRECT。"""
    m = BY_ID["share_capital.symbol"]
    assert m.kind is Kind.DIRECT
    assert isinstance(m.lineage, Direct)
    assert m.lineage.source_id == "qmt.capital"
    assert m.lineage.note is not None and len(m.lineage.note) > 10


def test_stock_per_share_is_a_sum_of_two_source_fields():
    """stockBonus（送股）+ stockGift（转增）之和不是单一 (source_id, field)，
    不满足 DIRECT，归 DERIVED，formula 里要能看到两个字段的名字。"""
    m = BY_ID["corporate_actions.stock_per_share"]
    assert m.kind is Kind.DERIVED
    assert "stockBonus" in m.lineage.formula
    assert "stockGift" in m.lineage.formula


def test_rights_columns_document_the_akshare_bootstrap_zero_fill_but_no_fake_d_number():
    """akshare 冷启动旁路把 rights_per_share / rights_price 硬编码成 0.0，
    这是填充不是观测——要在 meaning 里说清楚，但不能编一个不存在的 D 编号。"""
    for col in ("rights_per_share", "rights_price"):
        m = BY_ID[f"corporate_actions.{col}"]
        assert "0.0" in m.meaning
        assert m.known_issues == ()


def test_share_capital_points_at_qmt_capital():
    """R12：share_capital 不来自 qmt.minute，是 QMT download_financial_data。"""
    for m in METRICS:
        if m.dataset == "share_capital" and isinstance(m.lineage, Direct):
            assert m.lineage.source_id == "qmt.capital", m.id


def test_share_capital_field_names_are_the_qmt_names_not_warehouse_names():
    """QMT 的 m_timetag / m_anntime / total_capital / circulating_capital
    才是源字段名，不是仓库的 dt / ann_date / total / circulating。"""
    assert BY_ID["share_capital.dt"].lineage == Direct("qmt.capital", "m_timetag")
    assert BY_ID["share_capital.ann_date"].lineage == Direct(
        "qmt.capital", "m_anntime"
    )
    assert BY_ID["share_capital.total"].lineage == Direct(
        "qmt.capital", "total_capital"
    )
    assert BY_ID["share_capital.circulating"].lineage == Direct(
        "qmt.capital", "circulating_capital"
    )


def test_every_uncovered_reason_is_substantive():
    """review fix round 1：`test_every_entry_has_nonempty_checks` 是重言式——
    `Metric.__post_init__` 已经在构造期挡住了 `checks=()`，模块导入不了就
    根本跑不到这条断言，它测不出任何真实缺陷。换成一条真正约束行为的
    断言：每个 UNCOVERED 的 reason 必须言之有物，不能是「暂无」这种
    没有信息量的占位符——这是 __post_init__ 不管、只有这里管的性质。"""
    trivial = {"", "暂无", "无", "待定", "TODO"}
    for m in METRICS:
        if isinstance(m.checks, Uncovered):
            assert m.checks.reason not in trivial, m.id
            assert len(m.checks.reason) >= 10, m.id
