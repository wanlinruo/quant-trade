"""行情类条目。重点核的是血缘指对了源、以及单位换算没被改掉。

R1：paid_daily 的血缘不复用 tushare 的乘数表，两边的 amount 单位不同
（付费源万元 ×10000，Tushare 千元 ×1000），必须各自从自己的 pipeline
映射表生成。
"""

from qsys.data.catalog.market import DATASETS, METRICS
from qsys.data.catalog.types import Direct, Kind, MetricStatus, UNKNOWN, Validity

BY_ID = {m.id: m for m in METRICS}


def test_owns_the_seven_market_datasets():
    assert DATASETS == frozenset(
        {"tushare_daily", "bars_daily", "paid_daily", "paid_minute_bars",
         "index_bars", "minute_bars", "auction_ticks"}
    )


def test_registers_every_market_column():
    """七张表的列数逐表核对，缺列时直接指出数据集。"""
    from collections import Counter
    got = Counter(m.dataset for m in METRICS)
    assert dict(got) == {
        "tushare_daily": 28, "paid_daily": 28, "index_bars": 8,
        "minute_bars": 8, "paid_minute_bars": 9,
        "auction_ticks": 10, "bars_daily": 8,
    }


def test_amount_carries_the_thousand_multiplier():
    """Tushare daily.amount 单位是千元，入库 ×1000 转元。
    这个乘数被改掉在数值上「看起来正常」，只有字典对账能发现。"""
    lineage = BY_ID["tushare_daily.amount"].lineage
    assert lineage == Direct("tushare.daily", "amount", 1000.0)


def test_paid_daily_amount_uses_the_wan_yuan_multiplier():
    """付费源成交额单位是**万元**（×10000），Tushare 是千元（×1000）。
    两者复用同一张乘数表就会把其中一边记错。"""
    assert BY_ID["paid_daily.amount"].lineage == Direct("baidupan.daily", "amount", 10000.0)
    assert BY_ID["tushare_daily.amount"].lineage == Direct("tushare.daily", "amount", 1000.0)


def test_paid_daily_symbol_records_the_exchange_suffix_rule():
    """R9：paid_daily.symbol 判 DIRECT，但代码里给它拼了交易所后缀，
    note 必须写明，否则读者会以为源表头本来就带后缀。"""
    note = BY_ID["paid_daily.symbol"].lineage.note
    assert note and ("SH" in note or "交易所" in note)


def test_frozen_datasets_only_claim_the_frozen_check():
    """R2：paid_daily/bars_daily 冻结不变，A1/A3 等恒等式规则的实现只读
    tushare_daily，字典若替它们挂上这些规则就是宣称一份不存在的覆盖。"""
    for m in METRICS:
        if m.dataset in ("paid_daily", "bars_daily"):
            assert m.checks == ("C4.frozen_unchanged",), m.id


def test_share_columns_carry_the_ten_thousand_multiplier():
    """daily_basic 的股本列单位万股、市值列单位万元。"""
    for col, src in [
        ("total_shares", "total_share"), ("circulating_shares", "float_share"),
        ("free_shares", "free_share"), ("market_cap", "total_mv"),
        ("circulating_market_cap", "circ_mv"),
    ]:
        assert BY_ID[f"tushare_daily.{col}"].lineage == Direct(
            "tushare.daily_basic", src, 10000.0
        ), col


def test_volume_is_not_rescaled():
    """vol 单位是手，不换算——小数是真实精度不是脏数据。"""
    assert BY_ID["tushare_daily.volume"].lineage.multiplier == 1.0


def test_name_column_is_null_in_production_and_says_so():
    """tushare_daily.name 恒为 null，真值由 namechange / paid_daily /
    name_snapshots 三个源在 flags.resolve_names 里解析。"""
    m = BY_ID["tushare_daily.name"]
    assert "null" in m.meaning or "恒空" in m.meaning


def test_paid_daily_name_is_the_real_observed_value():
    """区别于 tushare_daily.name（恒 null），paid_daily.name 是逐日观测到的
    真实名称——flags.resolve_names 就是靠它覆盖 1990–2026-09-11。"""
    m = BY_ID["paid_daily.name"]
    assert "null" not in m.meaning
    assert m.lineage == Direct("baidupan.daily", "name", 1.0)


def test_paid_daily_is_frozen_and_points_at_baidupan():
    for m in METRICS:
        if m.dataset == "paid_daily":
            assert m.status is MetricStatus.FROZEN
            assert m.lineage.source_id == "baidupan.daily"


def test_paid_minutes_are_frozen_and_keep_source_units():
    metrics = [m for m in METRICS if m.dataset == "paid_minute_bars"]
    assert len(metrics) == 9
    assert all(m.status is MetricStatus.FROZEN for m in metrics)
    assert all(m.lineage.source_id == "baidupan.minute" for m in metrics)
    assert "股" in BY_ID["paid_minute_bars.volume"].meaning
    assert "D-20" in BY_ID["paid_minute_bars.close"].known_issues


def test_bars_daily_is_frozen_on_a_dead_source():
    for m in METRICS:
        if m.dataset == "bars_daily":
            assert m.status is MetricStatus.FROZEN
            assert m.lineage.source_id == "mootdx.kline"


def test_minute_dt_means_the_closing_minute():
    """09:30–09:31 那根记 09:31，所以 PIT 可见性就是 dt <= as_of，
    没有差一分钟的歧义。这个约定写错会让整条分钟链路差一格。"""
    assert "收盘" in BY_ID["minute_bars.dt"].meaning


def test_auction_quote_columns_record_the_identity():
    """竞价期间 bid1 == ask1 == 虚拟开盘价，bid_vol1 == ask_vol1 == 匹配量。
    09:25 撮合完成后这条恒等式才失效。"""
    assert "虚拟开盘价" in BY_ID["auction_ticks.bid1"].meaning


def test_every_entry_is_direct_except_the_one_column_with_no_source():
    """行情类原则上全是直取。唯一的例外是 tushare_daily.name——
    三个接口都不返回它，compose_daily 写 null 字面量，所以它指不到任何源字段。
    把它记成 DIRECT 会让 root_sources 报出一条不存在的名称链路。"""
    for m in METRICS:
        if m.id == "tushare_daily.name":
            assert m.kind is Kind.DERIVED
            assert m.lineage.upstream == ()
            continue
        assert m.kind is Kind.DIRECT, m.id
        assert m.validity in (Validity.SESSION, Validity.POINT), m.id


def test_the_null_name_column_contributes_no_root_source():
    """回归守护：这一列若记成 DIRECT，root_sources 会凭空多出一条
    tushare.namechange 的名称链路，而生产里那条链路根本不存在。"""
    from qsys.data.catalog import root_sources
    assert root_sources("tushare_daily.name") == ()


def test_unknown_meanings_are_listed_not_hidden():
    """允许有 UNKNOWN，但要能被数出来——写 UNKNOWN 合规，编一句才违规。"""
    unknown = [m.id for m in METRICS if m.meaning is UNKNOWN]
    assert len(unknown) <= 10, f"待查过多，先补依据：{unknown}"


def test_pre_close_carries_the_factor_known_issue():
    """A3.missed_exdiv 报的是源端因子与价格缺口不符（D-1）；价格是仲裁者，
    前收价本身对得上公司行动，不应因此判阻断。"""
    assert BY_ID["tushare_daily.pre_close"].known_issues == ("D-1",)
