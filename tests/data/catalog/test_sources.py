"""源清单。字典里每一条直取血缘都要指到这里的某个 id。"""

from collections.abc import Iterable

import pytest

from qsys.data.catalog.sources import SOURCES
from qsys.data.catalog.types import Access, Source, SourceStatus


def test_ids_match_their_provider_and_api():
    """id 必须是 provider 前缀 + api，否则页面上两处显示会对不上。"""
    for sid, s in SOURCES.items():
        assert sid == s.id
        assert sid.endswith("." + s.api), sid


def test_covers_every_tushare_api_the_pipeline_uses():
    """实清过：pipeline 里引用了正好这些 Tushare 接口。"""
    assert {s.api for s in SOURCES.values() if s.provider == "Tushare Pro"} == {
        "daily", "daily_basic", "adj_factor", "stk_limit", "suspend_d",
        "limit_list_d", "index_daily", "stock_basic", "namechange", "trade_cal",
        "stock_st",
        "minute",
    }


def test_registers_every_known_source():
    """源清单是穷举的。新增源必须同时更新这里，否则字典会指向一个没登记的源。

    R12/R14 补登 qmt.capital（share_capital 的真实源）与
    qmt.dividends（corporate_actions 的真实源，草案原先误记成
    akshare.stock_fhps_detail_em），Task 8 再登记系统自身 qsys.runtime，
    2026-09-15 直取换源补登 tushare.stock_st。2026-10-05 Task 3 补登
    joinquant.minute：bar_minute_1m.source 的 distinct 值里一直有
    joinquant，但源清单从未登记过它（scripts/publish_joinquant_minute_overrides.py
    按需补 QMT 退市证券零价格分钟记录），对账质检靠它才能把这个值对上号。
    """
    assert set(SOURCES) == {
        "tushare.daily", "tushare.daily_basic", "tushare.adj_factor",
        "tushare.stk_limit", "tushare.suspend_d", "tushare.limit_list_d",
        "tushare.index_daily", "tushare.stock_basic", "tushare.namechange",
        "tushare.trade_cal", "tushare.stock_st",
        "tushare.minute",
        "akshare.tool_trade_date_hist_sina", "akshare.stock_info_a_code_name",
        "akshare.stock_info_sh_delist", "akshare.stock_info_sz_delist",
        "akshare.stock_zh_a_spot_em", "akshare.stock_fhps_detail_em",
        "qmt.minute", "qmt.tick", "qmt.capital", "qmt.dividends",
        "joinquant.minute",
        "baidupan.daily", "baidupan.minute",
        "mootdx.kline",
        "qsys.runtime",
    }


def test_mootdx_is_dead():
    """2026-09-10 起对全部服务器返回 None，不是「暂时不稳」。"""
    assert SOURCES["mootdx.kline"].status is SourceStatus.DEAD


def test_qmt_sources_are_realtime_and_say_so():
    """实时自采漏一天永久缺一天，caveat 必须写明，否则没人知道它不能补。"""
    for sid in ("qmt.minute", "qmt.tick"):
        assert SOURCES[sid].access is Access.REALTIME
        assert SOURCES[sid].caveat


def test_qmt_capital_is_a_frozen_historical_source():
    source = SOURCES["qmt.capital"]
    assert source.status is SourceStatus.FROZEN
    assert "退出每日归档" in source.caveat


def test_every_dead_or_frozen_source_explains_itself():
    for s in SOURCES.values():
        if s.status is not SourceStatus.ACTIVE:
            assert s.caveat, f"{s.id} 非 ACTIVE 却没写 caveat"


def test_tushare_expiry_is_recorded_for_the_credential_check():
    """C5 要靠这个日期提前告警——积分一年有效、到期回收，
    到期未续费则日线源直接中断。"""
    daily = SOURCES["tushare.daily"]
    assert daily.expires is not None
    assert daily.expires.year == 2027


def test_limit_list_records_both_of_its_known_traps():
    """D-11（不含 ST）与 D-12（发布晚于 17:00）都必须在 caveat 里。"""
    caveat = SOURCES["tushare.limit_list_d"].caveat
    assert "ST" in caveat
    assert "19:00" in caveat


def test_paid_daily_is_frozen_but_still_the_only_historical_name_source():
    s = SOURCES["baidupan.daily"]
    assert s.status is SourceStatus.FROZEN
    assert s.access is Access.FILE
    assert "名称" in s.caveat


def test_paid_minute_source_is_findable_by_the_name_the_data_uses():
    """bar_minute_1m.source 列写的是 paid，目录 id 是 baidupan.minute。
    2026-10-05 的教训：按 paid 查不到任何东西。"""
    assert "paid" in SOURCES["baidupan.minute"].aliases


def test_reserved_tushare_minute_source_cannot_be_mistaken_for_landed_data():
    source = SOURCES["tushare.minute"]
    assert source.tier == "未购买分钟权限"
    assert source.cadence == "无生产采集任务"
    assert "没有 tushare_minute_bars" in source.caveat
    assert "tushare" in source.aliases


def _assert_no_alias_collisions(sources: Iterable[Source]) -> None:
    """别名既不能互相重复，也不能撞上任何源的 id——与定义顺序无关。

    之前的写法只检查「自己的 id 是否已经出现在 seen（别名）集合里」，
    被撞的 id 定义在被撞别名之前时测试会静默通过（评审发现 1）：
    ``seen`` 只在遍历途中累积，先定义的 id 永远不会被后面的别名拿去比对。
    这里改成先收集全部 id 的静态集合，每个别名直接对这个集合做全量比对，
    不再依赖「走到这一条时 seen 里已经有什么」。
    """
    sources = list(sources)
    ids = {source.id for source in sources}
    seen: dict[str, str] = {}
    for source in sources:
        for alias in source.aliases:
            assert alias not in ids, f"别名 {alias}（属于 {source.id}）与某个源 id 相同"
            assert alias not in seen, f"别名 {alias} 同时属于 {seen[alias]} 与 {source.id}"
            seen[alias] = source.id


def test_aliases_do_not_collide():
    _assert_no_alias_collisions(SOURCES.values())


def test_alias_colliding_with_an_earlier_defined_id_is_caught():
    """定义顺序不能决定抓不抓得到：被撞的 id 排在带冲突别名的源之前也必须报。

    用构造的 Source 列表复现评审给的最小反例，不碰 sources.py 的真实数据。
    """
    earlier = Source(id="x.probe", provider="probe", api="probe",
                      access=Access.API, status=SourceStatus.ACTIVE)
    later = Source(id="y.probe", provider="probe", api="probe",
                   access=Access.API, status=SourceStatus.ACTIVE,
                   aliases=("x.probe",))
    with pytest.raises(AssertionError, match="x.probe"):
        _assert_no_alias_collisions([earlier, later])
