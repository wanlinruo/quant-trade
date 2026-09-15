"""源清单。字典里每一条直取血缘都要指到这里的某个 id。"""

from qsys.data.catalog.sources import SOURCES
from qsys.data.catalog.types import Access, SourceStatus


def test_ids_match_their_provider_and_api():
    """id 必须是 provider 前缀 + api，否则页面上两处显示会对不上。"""
    for sid, s in SOURCES.items():
        assert sid == s.id
        assert sid.endswith("." + s.api), sid


def test_covers_all_ten_tushare_apis():
    """实清过：pipeline 里引用了正好这 10 个 Tushare 接口。"""
    assert {s.api for s in SOURCES.values() if s.provider == "Tushare Pro"} == {
        "daily", "daily_basic", "adj_factor", "stk_limit", "suspend_d",
        "limit_list_d", "index_daily", "stock_basic", "namechange", "trade_cal",
    }


def test_registers_every_known_source():
    """源清单是穷举的。新增源必须同时更新这里，否则字典会指向一个没登记的源。"""
    assert set(SOURCES) == {
        "tushare.daily", "tushare.daily_basic", "tushare.adj_factor",
        "tushare.stk_limit", "tushare.suspend_d", "tushare.limit_list_d",
        "tushare.index_daily", "tushare.stock_basic", "tushare.namechange",
        "tushare.trade_cal",
        "akshare.tool_trade_date_hist_sina", "akshare.stock_info_a_code_name",
        "akshare.stock_info_sh_delist", "akshare.stock_info_sz_delist",
        "akshare.stock_zh_a_spot_em", "akshare.stock_fhps_detail_em",
        "qmt.minute", "qmt.tick",
        "baidupan.daily", "baidupan.minute",
        "mootdx.kline",
    }


def test_mootdx_is_dead():
    """2026-09-10 起对全部服务器返回 None，不是「暂时不稳」。"""
    assert SOURCES["mootdx.kline"].status is SourceStatus.DEAD


def test_qmt_sources_are_realtime_and_say_so():
    """实时自采漏一天永久缺一天，caveat 必须写明，否则没人知道它不能补。"""
    for sid in ("qmt.minute", "qmt.tick"):
        assert SOURCES[sid].access is Access.REALTIME
        assert SOURCES[sid].caveat


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
