"""全库数据源清单。

「来源＝Tushare」这种粒度会把真问题盖掉：tushare_daily 那 28 列里 9 列来自
`daily`、15 列来自 `daily_basic`、1 列来自 `adj_factor`，到货时点也不同
（D-12 实测 limit_list_d 17:02 为空、19:00 才有，而 daily 17:02 已齐）。
所以源登记到**接口**级。
"""

from __future__ import annotations

from datetime import date

from qsys.data.catalog.types import Access, Source, SourceStatus

# Tushare Pro 积分一年有效、到期回收。2026-09-13 购入 5000 积分 ¥500/年。
_TUSHARE_EXPIRES = date(2027, 9, 13)
_TUSHARE_CRED = "config/deploy.local.toml [tushare]"
_TUSHARE_ACQUIRED = "2026-09-13 购入 5000 积分 ¥500/年，积分到期回收"
_WORKDAY_TWICE = "工作日 17:00 + 19:00（Asia/Shanghai）"


def _tushare(api: str, tier: str, cadence: str, caveat: str | None = None) -> Source:
    return Source(
        id=f"tushare.{api}", provider="Tushare Pro", api=api,
        access=Access.API, status=SourceStatus.ACTIVE,
        credential=_TUSHARE_CRED, tier=tier, acquired=_TUSHARE_ACQUIRED,
        cadence=cadence, caveat=caveat, expires=_TUSHARE_EXPIRES,
    )


def _akshare(api: str, caveat: str | None = None) -> Source:
    return Source(
        id=f"akshare.{api}", provider="akshare", api=api,
        access=Access.API, status=SourceStatus.ACTIVE,
        credential=None, tier="免费", acquired="开源库，无凭据",
        cadence="按需调用", caveat=caveat,
    )


_ALL = [
    # ---- Tushare Pro（minute/auction permissions are capability-gated） ----
    _tushare("dividend", "当前账户已实测可用（#558）", "显式现金选源时逐事件查询",
             "cash_div_tax按证券/除权日/实施状态唯一匹配，原值不取整、不补0。"
             "缺失或冲突保留审计并质量拒绝；候选可显式采用本字段，当前默认生产仍为QMT，旧批次保留。"
             "#558公告核验与当前7事件窗口实测已留证，历史例外不扩展为全历史重写。"),
    _tushare("daily", "2000 积分", _WORKDAY_TWICE,
             "vol 单位是手且带小数（582305.98），取整会丢真实精度；"
             "amount 单位千元，入库 ×1000 转元"),
    _tushare("daily_basic", "2000 积分", _WORKDAY_TWICE,
             "股本列单位万股、市值列单位万元，入库均 ×10000；"
             "total_share 必须直取，反算会让 A+H 股差上亿股"),
    _tushare("adj_factor", "2000 积分", _WORKDAY_TWICE,
             "源字段自身不自洽：非股改期有 688 条价格反推对不上直取值，"
             "另有 12 只「价格有除权缺口而因子不跳」的漏除权，见 D-1"),
    _tushare("stk_limit", "2000 积分", _WORKDAY_TWICE,
             "涨跌停价真值，是档位的**唯一**可靠来源——档位随监管变化，"
             "任何写死的「板块/ST → 档位」映射都会过期：2026-07-06 起主板 ST "
             "由 5% 改为 10%（实测 07-03 尚为 5%、07-06 起全部 10%，无过渡）。"
             "新股上市初期无涨跌幅限制时返回 up_limit=1,000,000、"
             "down_limit=0.01，那是「不设限」不是价格"),
    _tushare("stock_st", "3000 积分", _WORKDAY_TWICE,
             "只返回当日 ST **正例**，落库时须与当日 tushare_daily 全集左连"
             "才能得到显式 False，否则「接口失败」与「当日不是 ST」无法区分；"
             "历史自 2000-01-01；对退市整理期标的有单日误报（D-15）"),
    _tushare("suspend_d", "2000 积分", _WORKDAY_TWICE,
             "suspend_timing 在全天停牌时为 null，不是缺失"),
    _tushare("limit_list_d", "5000 积分", _WORKDAY_TWICE,
             "**不含 ST 股票**（D-11：与自算涨停的差集精确等于当日 ST 涨停集）；"
             "发布时点晚于 17:00，实测 17:02 取到 0 行而 19:00 取到 55 行（D-12）；"
             "实测数据自 2020 年起"),
    _tushare("moneyflow", "2000 积分", _WORKDAY_TWICE,
             "成交量源单位手、成交额源单位万元（入库乘 10000 转元）；净流入量额"
             "基于 L2 主动买卖单，不能由大小单合计简单相减；只在完整单日响应后发布"),
    _tushare("index_member_all", "2000 积分", "按需全量刷新",
             "上游 is_new 默认为 Y，采集必须显式合并 Y/N 并分别分页；"
             "查询必须使用 in_date/out_date 做历史 as-of，不能只读 is_new"),
    _tushare("stk_auction_o", "单独权限", "研究回补按交易日",
             "接口盘后更新；只作历史事实。与 QMT 09:25 最终快照完成价格量额"
             "同口径验证前保持隔离，不得用于晋级交易模型"),
    _tushare("index_daily", "2000 积分", _WORKDAY_TWICE,
             "vol 含小数（全历史 5,270 行中实测 186 行），schema 已用 float64"),
    _tushare("stock_basic", "2000 积分", _WORKDAY_TWICE,
             "返回的是**当前**名称，只对取数当天成立；"
             "拿它填历史是结转，填取快照那一天才是直接观测"),
    _tushare("namechange", "2000 积分", _WORKDAY_TWICE,
             "只记录变更事件不是逐日快照；2006-10-08 之后大量标的没有后继区间"
             "（D-10 的 2006 断层），单日实测 96 只无名称"),
    _tushare("trade_cal", "120 积分", "按需调用", None),
    Source(
        id="tushare.minute", provider="Tushare Pro", api="minute",
        access=Access.API, status=SourceStatus.ACTIVE,
        credential=_TUSHARE_CRED, tier="未购买分钟权限",
        acquired="当前未购买；仅预留物理契约与来源标识",
        cadence="无生产采集任务",
        caveat="生产 warehouse 当前没有 tushare_minute_bars 分区；只有在采购权限、"
               "实现采集并通过分钟质量门禁后才允许发布，不能把预留表误报成已有数据",
        expires=_TUSHARE_EXPIRES,
        aliases=("tushare",),
    ),

    # ---- akshare，6 个端点，免费 ----
    _akshare("tool_trade_date_hist_sina"),
    _akshare("stock_info_a_code_name"),
    _akshare("stock_info_sh_delist"),
    _akshare("stock_info_sz_delist"),
    _akshare("stock_zh_a_spot_em"),
    _akshare("stock_fhps_detail_em",
             "分红送转明细。第三方未经验证的源，只作旁证，"
             "不得用它裁决 Tushare 的因子对错"),

    # ---- QMT 实时自采，2 个 ----
    Source(
        id="qmt.minute", provider="QMT", api="minute",
        access=Access.REALTIME, status=SourceStatus.ACTIVE,
        credential="Windows 节点 xtquant", tier="券商账户",
        acquired="实时行情通道", cadence="每交易日收盘后导出",
        caveat="分钟线滚动保留约一年，漏一天丢一天",
        aliases=("qmt",),
    ),
    Source(
        id="qmt.tick", provider="QMT", api="tick",
        access=Access.REALTIME, status=SourceStatus.ACTIVE,
        credential="Windows 节点 xtquant", tier="券商账户",
        acquired="实时行情通道", cadence="每交易日收盘后导出",
        caveat="**买不到的东西**：tick 只回溯约 20 个交易日（实测边界 2026-08-10，"
               "锚点 2026-09-08），漏三周就全丢。Tushare 的 stk_auction 是 9:25 "
               "撮合完成后的结果，口径对不上，替代会引入前视偏差（D-7）",
    ),

    # ---- QMT 财务事实，1 个冻结历史源 + 1 个按需全量源（R12/R14
    # 的真实源，草案原先分别错记成 qmt.minute 与 akshare.stock_fhps_detail_em）----
    Source(
        id="qmt.capital", provider="QMT", api="capital",
        access=Access.REALTIME, status=SourceStatus.FROZEN,
        credential="Windows 节点 xtquant", tier="券商账户",
        acquired="download_financial_data", cadence="2026-09-16 起停止采集",
        caveat="2026-09-16 起冻结为历史证据，已退出每日归档与规范层血缘。"
               "scripts/win_download_capital.py 实测：download_financial_data "
               "0.11 秒/只，全市场约 10 分钟，磁盘约 4 MB；抽样 15 只全部有"
               "股本变动，且粒度是事件级（增发/解禁/回购注销都有独立记录），"
               "必须按记录逐条落盘，不能只取季末快照。字段对照：m_timetag"
               "（生效日）→ dt，m_anntime（公告日）→ ann_date，total_capital "
               "→ total，circulating_capital → circulating；脚本对 "
               "total_capital / circulating_capital 用 `... or 0.0` 兜底，"
               "None 会被折算成 0.0（见 catalog/reference.py 的填充问题记录）",
    ),
    Source(
        id="qmt.dividends", provider="QMT", api="dividends",
        access=Access.REALTIME, status=SourceStatus.ACTIVE,
        credential="Windows 节点 xtquant", tier="券商账户",
        acquired="scripts/win_download_dividends.py 全市场逐标的下载"
                 "（xtdata.get_divid_factors）",
        cadence="按需全量重跑",
        caveat="它**含配股**（allotNum/allotPrice），这正是它存在的原因——"
               "akshare 的 stock_fhps_detail_em 只有分红送转，做过配股的标的"
               "复权因子会偏差（spec §12 风险表，pipeline/dividends.py:1-11）。"
               "历史及兼容模式字段对照：interest → cash_per_share，stockBonus+stockGift → "
               "stock_per_share，allotNum → rights_per_share，allotPrice → "
               "rights_price；dr（单次复权因子）只用于交叉校验，不落库。"
               "返回值以除权日为 DataFrame 索引，无具名日期列。历史脚本曾将 "
               "None/NaN折算成0，当前增量导出器保留null并拒绝不完整响应，旧值无法据零区分。"
               "显式现金选源与历史修复另按#558批次审计，QMT原始快照保留。build_corporate_actions"
               "（akshare 路径）仅在冷启动脚本 bootstrap_warehouse.py 用到，"
               "标准增量链路走的是这个源",
    ),

    # ---- 聚宽（JQData），按需补数，不进定时任务 ----
    Source(
        id="joinquant.minute", provider="聚宽（JQData）", api="minute",
        access=Access.API, status=SourceStatus.ACTIVE,
        credential="config/deploy.local.toml [joinquant]", tier=None,
        acquired="jqdatasdk 1.9.8，scripts/publish_joinquant_minute_overrides.py 按需运行",
        cadence="按需运行，覆盖 QMT 退市证券的分钟零价格记录",
        caveat="09:31 为聚宽原生收盘时刻、含集合竞价与首分钟成交，不可拆成 "
               "09:30/09:31 两根（minute_repairs.py）；入库前须过 240 根原生网格、"
               "数值有效与 Tushare 日线对账（量/额/价相对偏差 ≤ 5%）三项校验，"
               "写入独立数据集 joinquant_minute_bars，不改写 QMT",
        aliases=("joinquant",),
    ),

    # ---- 百度网盘付费源，人工转存，已冻结 ----
    Source(
        id="baidupan.daily", provider="百度网盘付费源（余哈哈数据仓库）", api="daily",
        access=Access.FILE, status=SourceStatus.FROZEN,
        credential="人工转存", tier="付费",
        acquired="2026-09-11 前的历史快照", cadence="已停止更新",
        caveat="实测就是 Tushare 转售（28 列字段级吻合，18,224,238 行逐键零差异）。"
               "**不能删**：2026-09-11 及之前的**名称**真值只有它一个源，"
               "删了 867 个首板会重新消失（D-10）",
    ),
    Source(
        id="baidupan.minute", provider="百度网盘付费源（余哈哈数据仓库）", api="minute",
        access=Access.FILE, status=SourceStatus.FROZEN,
        credential="人工转存", tier="付费",
        acquired="2009–2025 历史分钟", cadence="已停止更新",
        caveat="历史分钟线一次性导入，增量走 qmt.minute",
        aliases=("paid",),
    ),

    # ---- mootdx，已死 ----
    Source(
        id="mootdx.kline", provider="mootdx", api="kline",
        access=Access.API, status=SourceStatus.DEAD,
        credential=None, tier="免费",
        acquired="开源库", cadence="已失效",
        caveat="2026-09-10 起对**全部服务器**返回 None，每次调用等 3.8 秒后返回空表。"
               "不是暂时不稳，是上游失效；bars_daily 因此冻结",
    ),

    # ---- 系统自身的运行时记录生产者，不是外部数据源 ----
    Source(
        id="qsys.runtime", provider="qsys", api="runtime",
        access=Access.API, status=SourceStatus.ACTIVE,
        credential=None, tier=None,
        acquired="系统内建", cadence="随采集与实盘事件写入",
        caveat="系统自身的运行时记录生产者，不是外部真值源；多数字段无法"
               "跨源对账，依靠写入侧测试、幂等键和到货规则保证",
    ),
]

SOURCES: dict[str, Source] = {s.id: s for s in _ALL}
