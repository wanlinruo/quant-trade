"""参考类指标 49 条。

这些表决定「这一天这只股是什么状态」，是结转事故的高发区——
`instruments` 与 `name_snapshots` 都是**点观测**，`name_history` 是**区间**，
三者的 validity 必须标对，否则总有人拿快照去填历史。

本模块没有沿用计划草案的 `_TABLE_SPEC` / `_MEANING` 两张统一映射表，
原因是逐列核对后发现草案对三件事系统性搞错了（控制器裁决 R11/R13/R14）：

**R11——meaning 不是证据，是待验假设**：草案的 49 条 meaning 未经核实。
本模块每一条 meaning 只从三处找支持：`schema.py` 注释、`pipeline/*.py`
的注释或代码、`docs/reference/*.md`；找不到就写 `UNKNOWN`，不编一句
听起来合理的中文。`limit_list_daily` 的 `first_time` / `fd_amount` /
`limit_amount` / `float_mv` 四列在这三处都没有只字片语的支持，标 `UNKNOWN`。

**R12——`share_capital` 缺了一个源**：它不来自 `qmt.minute`，而是 QMT
`download_financial_data`（`scripts/win_download_capital.py`）。已在
`catalog/sources.py` 补登 `qmt.capital`。

**R13——源字段名 ≠ 仓库列名，也不是每一列都是 DIRECT**：草案的
`_build()` 无条件把全部 49 列判成 DIRECT。逐列核对
`pipeline/tushare_reference.py`（六张表）、`pipeline/capital.py`、
`pipeline/dividends.py`、`pipeline/adjust.py`、以及产出这两张表真实
字段名的 Windows 侧脚本 `scripts/win_download_capital.py` /
`scripts/win_download_dividends.py` 后，以下 8 列改判：

| id | 改判 | 理由 |
|---|---|---|
| `instruments.market` | DERIVED | `ts_code.split(".").last()`，机械字符串操作 |
| `instruments.board` | FACTOR | `board_of()` 号段分类表，含判断口径且曾因号段扩容修订过 |
| `instruments.as_of` | DERIVED | `pl.lit(as_of)`，调用方传入的挂钟日期字面量，无上游 |
| `name_snapshots.dt` | DERIVED | 等于同批次 `instruments.as_of`，同一字面量 |
| `price_limits_daily.pre_close` | DERIVED | `stk_limit.pre_close` 缺失时回退 `tushare.daily.pre_close`，两个候选源字段，不满足「唯一」 |
| `limit_list_daily.limit_type` | DERIVED | 调用方按 U/D/Z 传入的查询参数字面量，响应体本身不带这一列 |
| `corporate_actions.stock_per_share` | DERIVED | `stockBonus + stockGift` 两个源字段之和，不是单一字段 |
| `adj_factors.cum_share_factor` | DERIVED | 由 `corporate_actions` 的送转/配股两列连乘算出 |

`adj_factors.cum_factor` 逐列核对后仍是 DIRECT——它是 `tushare.adj_factor`
的无条件直取，只是可信度有问题（D-1/D-2），类型判据只问「怎么来的」，
不问「对不对」。

**R14——`corporate_actions` 真正的源是 QMT，不是 akshare**：草案假定
`corporate_actions` 来自 `akshare.stock_fhps_detail_em`，但
`pipeline/dividends.py` 顶部注释明说数据来自 QMT
`get_divid_factors`——正是因为 akshare 该接口不含配股。已在
`catalog/sources.py` 补登 `qmt.dividends`，`corporate_actions` 六列全部
改指向它。akshare 那条接口仍保留在 `sources.py`（`build_corporate_actions`
只在冷启动脚本 `scripts/bootstrap_warehouse.py` 里用到），但不再是本表
六列的血缘目标。

**另一条发现，记录但不在本任务修**：`scripts/win_download_dividends.py`
的 `_num()`（第 66-74 行）把 `get_divid_factors` 返回的 `None`/`NaN` 一律
折算成 `0.0` 才落 parquet；`scripts/win_download_capital.py` 对
`total_capital` / `circulating_capital` 也用 `... or 0.0` 做了同样的事
（第 136-137 行）。这与「有观测但取值恰好为 0」在下游无法区分，是「拿不到
就记 null、不准填」红线的又一处违例——但它发生在 `scripts/`，不在
`src/qsys/data/` 内，Task 15 的 `C1.no_fill` 静态扫描大概率扫不到。
已在相关列的 `meaning` 里记明，不在此任务里改动脚本。
"""

from __future__ import annotations

from qsys.data.catalog.types import (
    UNCOVERED,
    Computed,
    Direct,
    Kind,
    Metric,
    MetricStatus,
    UNKNOWN,
    Validity,
)

DATASETS = frozenset({
    "instruments", "name_history", "name_snapshots", "price_limits_daily",
    "suspend_daily", "limit_list_daily", "corporate_actions",
    "share_capital", "adj_factors", "stock_st_daily",
})

# --- QMT 分红送配链路两处「空值归零」的填充问题，供多列复用一段说明 -------

_QMT_NUM_FILL_NOTE = (
    "scripts/win_download_dividends.py 的 _num()（第 66-74 行，其自身注释"
    "「空值归零」）把 QMT 返回的 None 与 NaN 一律折算成 0.0 才写入 parquet——"
    "这与「本次事件里这项真的是 0」无法区分。发现于本任务，不在本任务修复范围"
)

_AKSHARE_BOOTSTRAP_FILL_NOTE = (
    "冷启动旁路 akshare_provider.fetch_corporate_actions（只被 "
    "scripts/bootstrap_warehouse.py 的一次性历史初始化调用，标准增量链路不经过"
    "它）把这一列硬编码成 0.0 字面量（akshare_provider.py:215-219 的"
    "「已知缺口」注释承认这一点），那是填充不是观测。此条目描述的是标准 QMT "
    "链路（qmt.dividends），不是这条旁路"
)


# ---------------------------------------------------------------------------
# instruments（点观测，7 列）——依据 schema.py INSTRUMENTS 注释、
# pipeline/tushare_reference.py:131-170 ingest_instruments 的实现与文档字符串、
# src/qsys/markets/ashare.py:22-37 board_of()。
# ---------------------------------------------------------------------------

_INSTRUMENTS_CHECKS = ("B4.arrival",)


def _instruments() -> list[Metric]:
    return [
        Metric(
            id="instruments.symbol", kind=Kind.DIRECT, validity=Validity.POINT,
            status=MetricStatus.ACTIVE,
            meaning="标的代码，直取 stock_basic.ts_code（含交易所后缀，如 "
                    "000001.SZ，pipeline/tushare_reference.py:149）",
            lineage=Direct("tushare.stock_basic", "ts_code"),
            checks=_INSTRUMENTS_CHECKS,
        ),
        Metric(
            id="instruments.name", kind=Kind.DIRECT, validity=Validity.POINT,
            status=MetricStatus.ACTIVE,
            meaning=(
                "快照当日 stock_basic 返回的证券简称——这是**当前**名称，只对"
                "本次取数当天成立；回填历史就是结转，这正是 validity=POINT 要拦"
                "的（pipeline/tushare_reference.py:132-140）"
            ),
            lineage=Direct("tushare.stock_basic", "name"),
            checks=_INSTRUMENTS_CHECKS,
        ),
        Metric(
            id="instruments.market", kind=Kind.DERIVED, validity=Validity.POINT,
            status=MetricStatus.ACTIVE,
            meaning="交易所代码，由 symbol（即 ts_code）按 '.' 分割取最后一段"
                    "得到（pipeline/tushare_reference.py:150）——机械字符串操作，"
                    "无判断口径，故归 DERIVED 不归 FACTOR",
            lineage=Computed(
                upstream=("instruments.symbol",),
                formula="symbol.split('.').last()",
            ),
            checks=_INSTRUMENTS_CHECKS,
        ),
        Metric(
            id="instruments.list_date", kind=Kind.DIRECT, validity=Validity.POINT,
            status=MetricStatus.ACTIVE,
            meaning="上市日期，直取 stock_basic.list_date",
            lineage=Direct("tushare.stock_basic", "list_date"),
            checks=_INSTRUMENTS_CHECKS,
        ),
        Metric(
            id="instruments.delist_date", kind=Kind.DIRECT, validity=Validity.POINT,
            status=MetricStatus.ACTIVE,
            meaning="退市日期，未退市为 null（schema.py INSTRUMENTS 注释），"
                    "直取 stock_basic.delist_date",
            lineage=Direct("tushare.stock_basic", "delist_date"),
            checks=_INSTRUMENTS_CHECKS,
        ),
        Metric(
            id="instruments.board", kind=Kind.FACTOR, validity=Validity.POINT,
            status=MetricStatus.ACTIVE,
            meaning=(
                "板块分类。由 markets.ashare.board_of() 按 symbol 号段前缀判定："
                "688/689→star，300/301→gem，43/83/87/92→bse（含 2023 年起启用"
                "的 920 号段），90/20→bshare，其余→main"
                "（src/qsys/markets/ashare.py:22-37）。这是一张会随号段扩容而"
                "修订的分类表（92 号段就补过一次，见函数文档字符串），含判断"
                "口径而非无歧义的机械公式，故归 FACTOR 不归 DERIVED。附注："
                "schema.py 的列注释只写了 main/gem/star/bse 四种，未提 "
                "board_of() 实际还会返回的 bshare，文档与代码分支不完全一致"
            ),
            lineage=Computed(
                upstream=("instruments.symbol",),
                formula="board_of(symbol.split('.')[0])",
            ),
            checks=_INSTRUMENTS_CHECKS,
        ),
        Metric(
            id="instruments.as_of", kind=Kind.DERIVED, validity=Validity.POINT,
            status=MetricStatus.ACTIVE,
            meaning=(
                "取这份快照的日期，调用方传入的挂钟日期字面量，不来自任何 API "
                "字段（pipeline/tushare_reference.py:156）。没有它就无法判断 "
                "board / list_date / delist_date 有多陈旧，也无法把 name 安全"
                "地限定在快照日使用（schema.py INSTRUMENTS 注释）"
            ),
            lineage=Computed(
                upstream=(),
                formula="pl.lit(as_of)  # 调用方传入的挂钟日期，无上游",
            ),
            checks=_INSTRUMENTS_CHECKS,
        ),
    ]


# ---------------------------------------------------------------------------
# name_snapshots（点观测，3 列）
# ---------------------------------------------------------------------------

_NAME_SNAPSHOTS_CHECKS = ("C2.pit_join", "B4.arrival")


def _name_snapshots() -> list[Metric]:
    return [
        Metric(
            id="name_snapshots.dt", kind=Kind.DERIVED, validity=Validity.POINT,
            status=MetricStatus.ACTIVE,
            meaning=(
                "观测日，即取 stock_basic 的那一天——与同一次同步产出的 "
                "instruments.as_of 是同一个字面量，不是独立观测"
                "（pipeline/tushare_reference.py:160-163）。**这是点观测不是"
                "区间**——转成区间要推断端点"
            ),
            lineage=Computed(
                upstream=("instruments.as_of",),
                formula="等于同一次同步产出的 instruments.as_of",
            ),
            checks=_NAME_SNAPSHOTS_CHECKS,
        ),
        Metric(
            id="name_snapshots.symbol", kind=Kind.DIRECT, validity=Validity.POINT,
            status=MetricStatus.ACTIVE,
            meaning="标的代码，直取 stock_basic.ts_code",
            lineage=Direct("tushare.stock_basic", "ts_code"),
            checks=_NAME_SNAPSHOTS_CHECKS,
        ),
        Metric(
            id="name_snapshots.name", kind=Kind.DIRECT, validity=Validity.POINT,
            status=MetricStatus.ACTIVE,
            meaning=(
                "观测日的证券简称，直取 stock_basic.name。按 (symbol, dt) "
                "**精确连接**，只对它实际有行的那一天生效，不向前后蔓延"
                "（schema.py NAME_SNAPSHOTS 注释，"
                "pipeline/tushare_reference.py:160-167）"
            ),
            lineage=Direct("tushare.stock_basic", "name"),
            checks=_NAME_SNAPSHOTS_CHECKS,
        ),
    ]


# ---------------------------------------------------------------------------
# name_history（区间，6 列）——依据 schema.py NAME_HISTORY 注释、
# pipeline/tushare_reference.py:173-186、docs/reference/deferred-corrections.md D-10。
# ---------------------------------------------------------------------------

_NAME_HISTORY_CHECKS = ("C3.span_gaps",)
_D10 = ("D-10",)


def _name_history() -> list[Metric]:
    return [
        Metric(
            id="name_history.symbol", kind=Kind.DIRECT, validity=Validity.SPAN,
            status=MetricStatus.ACTIVE,
            meaning="标的代码，直取 namechange.ts_code",
            lineage=Direct("tushare.namechange", "ts_code"),
            checks=_NAME_HISTORY_CHECKS,
        ),
        Metric(
            id="name_history.name", kind=Kind.DIRECT, validity=Validity.SPAN,
            status=MetricStatus.ACTIVE,
            meaning="该区间内的证券简称，直取 namechange.name。ST 状态由区间"
                    "覆盖关系纯派生（schema.py NAME_HISTORY 注释）",
            lineage=Direct("tushare.namechange", "name"),
            checks=_NAME_HISTORY_CHECKS, known_issues=_D10,
        ),
        Metric(
            id="name_history.start_date", kind=Kind.DIRECT, validity=Validity.SPAN,
            status=MetricStatus.ACTIVE,
            meaning="区间起日，直取 namechange.start_date",
            lineage=Direct("tushare.namechange", "start_date"),
            checks=_NAME_HISTORY_CHECKS, known_issues=_D10,
        ),
        Metric(
            id="name_history.end_date", kind=Kind.DIRECT, validity=Validity.SPAN,
            status=MetricStatus.ACTIVE,
            meaning="区间止日，直取 namechange.end_date；2006-10-08 之后大量"
                    "标的止步于此再无后继区间（D-10）",
            lineage=Direct("tushare.namechange", "end_date"),
            checks=_NAME_HISTORY_CHECKS, known_issues=_D10,
        ),
        Metric(
            id="name_history.ann_date", kind=Kind.DIRECT, validity=Validity.SPAN,
            status=MetricStatus.ACTIVE,
            meaning="变更公告日，直取 namechange.ann_date",
            lineage=Direct("tushare.namechange", "ann_date"),
            checks=_NAME_HISTORY_CHECKS,
        ),
        Metric(
            id="name_history.change_reason", kind=Kind.DIRECT, validity=Validity.SPAN,
            status=MetricStatus.ACTIVE,
            meaning="变更原因，直取 namechange.change_reason",
            lineage=Direct("tushare.namechange", "change_reason"),
            checks=_NAME_HISTORY_CHECKS,
        ),
    ]


# ---------------------------------------------------------------------------
# price_limits_daily（5 列）——依据 pipeline/tushare_reference.py:45-75。
# ---------------------------------------------------------------------------

_PRICE_LIMITS_CHECKS = ("A3.limit_price_vs_selfcalc", "B1.calendar")


def _price_limits_daily() -> list[Metric]:
    return [
        Metric(
            id="price_limits_daily.symbol", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="标的代码，直取 stk_limit.ts_code",
            lineage=Direct("tushare.stk_limit", "ts_code"),
            checks=_PRICE_LIMITS_CHECKS,
        ),
        Metric(
            id="price_limits_daily.dt", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="交易日，直取 stk_limit.trade_date",
            lineage=Direct("tushare.stk_limit", "trade_date"),
            checks=_PRICE_LIMITS_CHECKS,
        ),
        Metric(
            id="price_limits_daily.pre_close", kind=Kind.DERIVED, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning=(
                "前收盘价（元）。优先取 stk_limit 响应体自带的 pre_close；若该次"
                "响应未包含这一列（历史上确有发生，"
                "test_price_limits_get_pre_close_from_tushare_daily_when_api_omits_it "
                "覆盖了这条路径），则按 (ts_code, trade_date) 左连接回退取当日 "
                "tushare.daily 的 pre_close（pipeline/tushare_reference.py:52-66）。"
                "两个候选值分属不同的 (source_id, field)，不满足 DIRECT 要求的"
                "「唯一」，故归 DERIVED"
            ),
            lineage=Computed(
                upstream=("tushare_daily.pre_close",),
                formula="stk_limit.pre_close，若响应缺失该列则回退 "
                        "tushare_daily.pre_close",
            ),
            checks=_PRICE_LIMITS_CHECKS,
        ),
        Metric(
            id="price_limits_daily.limit_up", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="涨停价（元），交易所公布值。源字段名是 up_limit，仓库改名"
                    "为 limit_up（pipeline/tushare_reference.py:64）",
            lineage=Direct("tushare.stk_limit", "up_limit"),
            checks=_PRICE_LIMITS_CHECKS,
        ),
        Metric(
            id="price_limits_daily.limit_down", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="跌停价（元），交易所公布值。源字段名是 down_limit，仓库"
                    "改名为 limit_down（pipeline/tushare_reference.py:65）",
            lineage=Direct("tushare.stk_limit", "down_limit"),
            checks=_PRICE_LIMITS_CHECKS,
        ),
    ]


# ---------------------------------------------------------------------------
# suspend_daily（4 列）——依据 schema.py SUSPEND_DAILY 注释、
# pipeline/tushare_reference.py:78-101。
# ---------------------------------------------------------------------------

_SUSPEND_CHECKS = ("B4.arrival",)


def _suspend_daily() -> list[Metric]:
    return [
        Metric(
            id="suspend_daily.symbol", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="标的代码，直取 suspend_d.ts_code",
            lineage=Direct("tushare.suspend_d", "ts_code"),
            checks=_SUSPEND_CHECKS,
        ),
        Metric(
            id="suspend_daily.dt", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="交易日，直取 suspend_d.trade_date",
            lineage=Direct("tushare.suspend_d", "trade_date"),
            checks=_SUSPEND_CHECKS,
        ),
        Metric(
            id="suspend_daily.suspend_type", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="S 停牌 / R 复牌（schema.py SUSPEND_DAILY 注释），直取 "
                    "suspend_d.suspend_type",
            lineage=Direct("tushare.suspend_d", "suspend_type"),
            checks=_SUSPEND_CHECKS,
        ),
        Metric(
            id="suspend_daily.suspend_timing", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="停牌时段。**全天停牌时为 null**，是取值不是缺失"
                    "（schema.py SUSPEND_DAILY 注释），直取 "
                    "suspend_d.suspend_timing。代码里另有一处列缺失时置 null "
                    "的防御分支（pipeline/tushare_reference.py:86-87），但那只"
                    "在整列缺席时触发，未测试覆盖过，且不改变——它仍然只指向"
                    "这一个源字段，故仍归 DIRECT",
            lineage=Direct("tushare.suspend_d", "suspend_timing"),
            checks=_SUSPEND_CHECKS,
        ),
    ]


# ---------------------------------------------------------------------------
# limit_list_daily（9 列）——依据 schema.py LIMIT_LIST_DAILY 注释、
# pipeline/tushare_reference.py:210-236、
# docs/reference/deferred-corrections.md D-11/D-12、
# docs/reference/strategy-data-dependencies.md。
# ---------------------------------------------------------------------------

_LIMIT_LIST_CHECKS = ("A3.limit_list_vs_selfcalc", "B4.zero_limit_up")


def _limit_list_daily() -> list[Metric]:
    return [
        Metric(
            id="limit_list_daily.symbol", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="标的代码，直取 limit_list_d.ts_code",
            lineage=Direct("tushare.limit_list_d", "ts_code"),
            checks=_LIMIT_LIST_CHECKS,
        ),
        Metric(
            id="limit_list_daily.dt", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="交易日，直取 limit_list_d.trade_date；发布时点晚于 "
                    "17:00，实测 17:02 取到 0 行、19:00 才有 55 行（D-12）",
            lineage=Direct("tushare.limit_list_d", "trade_date"),
            checks=_LIMIT_LIST_CHECKS, known_issues=("D-12",),
        ),
        Metric(
            id="limit_list_daily.limit_type", kind=Kind.DERIVED, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning=(
                "U 涨停 / D 跌停 / Z 炸板（schema.py LIMIT_LIST_DAILY 注释）。"
                "**不含 ST 股票**——与自算涨停的差集精确等于当日 ST 涨停集"
                "（D-11）。这一列不是 limit_list_d 响应体里的字段：ingest_limit_list "
                "按调用方传入的 limit_type 参数分别请求接口，再把这个参数本身"
                "写成字面量（pipeline/tushare_reference.py:210-227；测试 fixture "
                "里的 raw 帧从不带这一列），指不到任何 (source_id, field)，归 "
                "DERIVED"
            ),
            lineage=Computed(
                upstream=(),
                formula="调用方传入 ingest_limit_list 的 limit_type 查询参数字面量"
                        "（U/D/Z），响应体本身不携带该分类字段",
            ),
            checks=_LIMIT_LIST_CHECKS, known_issues=("D-11", "D-12"),
        ),
        Metric(
            id="limit_list_daily.limit_times", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="连续涨停计数（含当日）。"
                    "docs/reference/strategy-data-dependencies.md 记录 "
                    "get_hl_stock 判定「昨日首板」正是依据这一列，直取 "
                    "limit_list_d.limit_times",
            lineage=Direct("tushare.limit_list_d", "limit_times"),
            checks=_LIMIT_LIST_CHECKS,
        ),
        Metric(
            id="limit_list_daily.open_times", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="当日打开（炸板）次数。"
                    "docs/reference/strategy-data-dependencies.md 记录 "
                    "get_ever_hl_stock2 判定「昨日炸板」正是依据这一列，直取 "
                    "limit_list_d.open_times",
            lineage=Direct("tushare.limit_list_d", "open_times"),
            checks=_LIMIT_LIST_CHECKS,
        ),
        Metric(
            id="limit_list_daily.first_time", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE, meaning=UNKNOWN,
            lineage=Direct("tushare.limit_list_d", "first_time"),
            checks=_LIMIT_LIST_CHECKS,
        ),
        Metric(
            id="limit_list_daily.fd_amount", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE, meaning=UNKNOWN,
            lineage=Direct("tushare.limit_list_d", "fd_amount"),
            checks=_LIMIT_LIST_CHECKS,
        ),
        Metric(
            id="limit_list_daily.limit_amount", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE, meaning=UNKNOWN,
            lineage=Direct("tushare.limit_list_d", "limit_amount"),
            checks=_LIMIT_LIST_CHECKS,
        ),
        Metric(
            id="limit_list_daily.float_mv", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE, meaning=UNKNOWN,
            lineage=Direct("tushare.limit_list_d", "float_mv"),
            checks=_LIMIT_LIST_CHECKS,
        ),
    ]


# ---------------------------------------------------------------------------
# corporate_actions（6 列）——R14：真正的源是 QMT get_divid_factors，
# 不是草案假定的 akshare.stock_fhps_detail_em。依据 schema.py
# CORPORATE_ACTIONS 注释、pipeline/dividends.py:1-19、
# scripts/win_download_dividends.py。
# ---------------------------------------------------------------------------

_CORPORATE_ACTIONS_CHECKS = ("B5.span",)


def _corporate_actions() -> list[Metric]:
    return [
        Metric(
            id="corporate_actions.symbol", kind=Kind.DERIVED, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning=(
                "标的代码。get_divid_factors(symbol) 按单个标的逐次调用"
                "（scripts/win_download_dividends.py:129-131），返回的 "
                "DataFrame 只以除权日为索引，响应体里没有任何字段携带标的"
                "身份——这一列是脚本按调用时的**入参**逐行回填，不是从响应"
                "读出来的，因此指不到任何 (source_id, field)，归 DERIVED，"
                "与 R10 的 instruments.as_of 同构（复核意见：见 fix report）"
            ),
            lineage=Computed(
                upstream=(),
                formula="脚本按 get_divid_factors(symbol) 的**入参**逐行回填；"
                        "响应体不含该字段。见 scripts/win_download_dividends.py",
            ),
            checks=UNCOVERED(
                "这一列的真值就是调用参数本身，没有独立的源字段可供比对——"
                "验证它没有意义，因为它不可能与自己以外的任何东西不一致"
            ),
        ),
        Metric(
            id="corporate_actions.ex_date", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="除权除息日（schema.py CORPORATE_ACTIONS 注释）。"
                    "get_divid_factors 返回的 DataFrame 以除权日为索引，不是"
                    "具名列（scripts/win_download_dividends.py:5-6）",
            lineage=Direct(
                "qmt.dividends", "index",
                note="get_divid_factors 返回值以除权日为 DataFrame 索引，"
                     "脚本解析该索引得到 ex_date，源侧没有同名列",
            ),
            checks=_CORPORATE_ACTIONS_CHECKS,
        ),
        Metric(
            id="corporate_actions.cash_per_share", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="每股现金红利（税前，元）（schema.py 注释），直取 "
                    "get_divid_factors 的 interest 列（"
                    "scripts/win_download_dividends.py:11）。" + _QMT_NUM_FILL_NOTE,
            lineage=Direct("qmt.dividends", "interest"),
            checks=_CORPORATE_ACTIONS_CHECKS,
        ),
        Metric(
            id="corporate_actions.stock_per_share", kind=Kind.DERIVED, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning=(
                "每股送转股数（schema.py 注释）。get_divid_factors 把送股"
                "（stockBonus）与转增（stockGift）分成两列，"
                "scripts/win_download_dividends.py:148 按行相加写入这一列——"
                "两个字段之和不是单一 (source_id, field)，不满足 DIRECT，"
                "归 DERIVED。" + _QMT_NUM_FILL_NOTE
            ),
            lineage=Computed(
                upstream=(),
                formula="qmt.dividends.stockBonus + qmt.dividends.stockGift",
            ),
            checks=_CORPORATE_ACTIONS_CHECKS,
        ),
        Metric(
            id="corporate_actions.rights_per_share", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="每股配股数（schema.py 注释），直取 get_divid_factors 的 "
                    "allotNum 列（scripts/win_download_dividends.py:13）。"
                    + _QMT_NUM_FILL_NOTE + "。" + _AKSHARE_BOOTSTRAP_FILL_NOTE,
            lineage=Direct("qmt.dividends", "allotNum"),
            checks=_CORPORATE_ACTIONS_CHECKS,
        ),
        Metric(
            id="corporate_actions.rights_price", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="配股价（元）（schema.py 注释），直取 get_divid_factors 的 "
                    "allotPrice 列（scripts/win_download_dividends.py:14）。"
                    + _QMT_NUM_FILL_NOTE + "。" + _AKSHARE_BOOTSTRAP_FILL_NOTE,
            lineage=Direct("qmt.dividends", "allotPrice"),
            checks=_CORPORATE_ACTIONS_CHECKS,
        ),
    ]


# ---------------------------------------------------------------------------
# share_capital（5 列）——R12：源是 QMT download_financial_data，不是
# qmt.minute。依据 schema.py SHARE_CAPITAL 注释、pipeline/capital.py、
# scripts/win_download_capital.py。
# ---------------------------------------------------------------------------

_SHARE_CAPITAL_CHECKS = ("B5.span",)

_CAPITAL_FILL_NOTE = (
    "scripts/win_download_capital.py 用 `float(row.get(...) or 0.0)` 兜底"
    "（第 136-137 行），None 会被折算成 0.0，与「真实股本恰好为 0」无法区"
    "分——与 _QMT_NUM_FILL_NOTE 同类的填充问题，发现于本任务，不在本任务"
    "修复范围"
)


def _share_capital() -> list[Metric]:
    return [
        Metric(
            id="share_capital.symbol", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning=(
                "标的代码。与 corporate_actions.symbol 不同：这里 "
                "get_financial_data(chunk, ...) 是**批量**调用"
                "（scripts/win_download_capital.py:111-116，chunk 是一批标的"
                "而非单个），返回值 `data` 本身是按标的建索引的字典——脚本"
                "用 `data.get(symbol)` 取出这一只标的那一份"
                "（scripts/win_download_capital.py:122-123）。标的身份是"
                "响应体自身的结构（字典键），不是脚本凭空回填的入参，与 "
                "paid_daily.symbol 的 R9 判例同构（值确实来自源，只是载体"
                "是键不是列），故保留 DIRECT"
            ),
            lineage=Direct(
                "qmt.capital", "symbol",
                note="get_financial_data 的返回值以标的为字典键，脚本按"
                     "该键取值（scripts/win_download_capital.py:122-123）；"
                     "键本身不是一张表里的具名列，故仍在 note 里说明",
            ),
            checks=_SHARE_CAPITAL_CHECKS,
        ),
        Metric(
            id="share_capital.dt", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="股本**生效日**（QMT m_timetag）。股本是公开可即时观察的"
                    "事实，交易所每日据此发布总市值，故以生效日而非公告日为准"
                    "（schema.py SHARE_CAPITAL 注释，"
                    "scripts/win_download_capital.py:7-9）",
            lineage=Direct("qmt.capital", "m_timetag"),
            checks=_SHARE_CAPITAL_CHECKS,
        ),
        Metric(
            id="share_capital.ann_date", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="公告日，留作更严格 PIT 口径的口子（schema.py 注释），"
                    "直取 QMT m_anntime",
            lineage=Direct("qmt.capital", "m_anntime"),
            checks=_SHARE_CAPITAL_CHECKS,
        ),
        Metric(
            id="share_capital.total", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="总股本（股）（schema.py 注释），直取 QMT total_capital。"
                    + _CAPITAL_FILL_NOTE,
            lineage=Direct("qmt.capital", "total_capital"),
            checks=_SHARE_CAPITAL_CHECKS,
        ),
        Metric(
            id="share_capital.circulating", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="流通股本（股）（schema.py 注释），直取 QMT "
                    "circulating_capital。" + _CAPITAL_FILL_NOTE,
            lineage=Direct("qmt.capital", "circulating_capital"),
            checks=_SHARE_CAPITAL_CHECKS,
        ),
    ]


# ---------------------------------------------------------------------------
# adj_factors（4 列）——依据 schema.py ADJ_FACTORS 注释、
# pipeline/adjust.py。
# ---------------------------------------------------------------------------

_ADJ_FACTORS_CHECKS = ("A3.missed_exdiv",)


def _adj_factors() -> list[Metric]:
    return [
        Metric(
            id="adj_factors.symbol", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="标的代码。build_adj_factors 直接复用仓库里的 "
                    "tushare_daily.symbol（pipeline/adjust.py:60-63、106-115），"
                    "根源仍是 tushare.daily.ts_code",
            lineage=Direct("tushare.daily", "ts_code"),
            checks=_ADJ_FACTORS_CHECKS,
        ),
        Metric(
            id="adj_factors.dt", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="交易日，同上复用 tushare_daily.dt，根源是 "
                    "tushare.daily.trade_date",
            lineage=Direct("tushare.daily", "trade_date"),
            checks=_ADJ_FACTORS_CHECKS,
        ),
        Metric(
            id="adj_factors.cum_factor", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning=(
                "后复权累计因子（价格用，schema.py 注释）。直取 "
                "tushare.adj_factor 的 adj_factor 列（pipeline/adjust.py:6、"
                "106-115）。**源字段自身不自洽**：非股改期有 688 条价格反推"
                "对不上直取值，另有因子该跳而未跳的漏除权（D-1）；920 号段"
                "北交所另有独立异常簇（D-2）。价格反推降级为校验"
                "（verify_computed_factors），不得覆盖真值"
            ),
            lineage=Direct("tushare.adj_factor", "adj_factor"),
            checks=_ADJ_FACTORS_CHECKS, known_issues=("D-1", "D-2"),
        ),
        Metric(
            id="adj_factors.cum_share_factor", kind=Kind.DERIVED, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning=(
                "股本累计比例（成交量用，schema.py 注释）。只随**送转**与"
                "配股变化，与派现无关——价格因子含现金分红那一项，拿它调"
                "成交量会多调（pipeline/adjust.py:12-19）。公式：单日股本"
                "比例 = 1 + stock_per_share + rights_per_share（缺失记 0），"
                "对每只标的按 dt 连乘（pipeline/adjust.py:82-91）"
            ),
            lineage=Computed(
                upstream=(
                    "corporate_actions.stock_per_share",
                    "corporate_actions.rights_per_share",
                ),
                formula="cum_share_factor(t) = ∏_{ex_date<=t} "
                        "(1 + stock_per_share + rights_per_share)",
            ),
            checks=_ADJ_FACTORS_CHECKS,
        ),
    ]

_STOCK_ST_CHECKS = ("B1.calendar", "B5.span")


def _stock_st_daily() -> list[Metric]:
    """每日 ST 真值（2026-09-15 直取换源新增）。

    `stock_st` 只返回当日 ST **正例**。落库时与当日 `tushare_daily` 的
    股票全集左连接，才把 `is_st` 显式写成 True/False——不这样做，
    「接口失败或未回填」与「当日不是 ST」在表里长得一模一样。
    因此 `is_st` 是 DERIVED（由正例列表与全集左连接得到），
    而随正例一起返回的 name/st_type/st_type_name 三列是 DIRECT。
    """
    return [
        Metric(
            id="stock_st_daily.symbol", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="标的代码，直取 stock_st.ts_code",
            lineage=Direct("tushare.stock_st", "ts_code"),
            checks=_STOCK_ST_CHECKS,
        ),
        Metric(
            id="stock_st_daily.dt", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="交易日，直取 stock_st.trade_date",
            lineage=Direct("tushare.stock_st", "trade_date"),
            checks=_STOCK_ST_CHECKS,
        ),
        Metric(
            id="stock_st_daily.is_st", kind=Kind.DERIVED, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning=(
                "当日是否为 ST。**接口只给正例**，True 来自「该标的出现在当日"
                "响应里」，False 来自「当日 tushare_daily 有它而响应里没有」"
                "——是左连接的结果而非某个源字段，故归 DERIVED。表缺失或某日"
                "未回填时该股-日整行不存在，那是「未知」，绝不等同于 False"
                "（pipeline/tushare_reference.py:123-159）。"
                "**这是全系统唯一的 ST 真值**：2026-07-06 起主板 ST 的涨跌幅"
                "上限由 5% 改为 10%，与普通股相同，价格档位再也反推不出 ST"
            ),
            lineage=Computed(
                upstream=("tushare_daily.symbol", "tushare_daily.dt"),
                formula="is_st = symbol ∈ stock_st(trade_date) 的正例集合，"
                        "全集取当日 tushare_daily",
            ),
            checks=_STOCK_ST_CHECKS,
            known_issues=("D-15",),
        ),
        Metric(
            id="stock_st_daily.name", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning=(
                "ST 期间的股票名称，直取 stock_st.name。只有正例行有值，"
                "左连接补出的 False 行为 null——那是「不适用」不是缺失。"
                "**不要拿它当名称真值源**：退市整理期标的实测出现过单日"
                "回到旧 *ST 名的误报（D-15）"
            ),
            lineage=Direct("tushare.stock_st", "name"),
            checks=_STOCK_ST_CHECKS,
        ),
        Metric(
            id="stock_st_daily.st_type", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning="ST 类型代码，直取 stock_st.type。只有正例行有值",
            lineage=Direct("tushare.stock_st", "type"),
            checks=_STOCK_ST_CHECKS,
        ),
        Metric(
            id="stock_st_daily.st_type_name", kind=Kind.DIRECT, validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning=(
                "ST 类型名称，直取 stock_st.name_type，实测取值如「风险警示板」。"
                "**不足以推出涨跌幅档位**：实测同为「风险警示板」的股-日里，"
                "2026-07-06 前是 5% 档、之后是 10% 档，档位只能取 stk_limit"
            ),
            lineage=Direct("tushare.stock_st", "name_type"),
            checks=_STOCK_ST_CHECKS,
        ),
    ]


METRICS: tuple[Metric, ...] = tuple(
    _instruments() + _name_snapshots() + _name_history()
    + _price_limits_daily() + _suspend_daily() + _limit_list_daily()
    + _corporate_actions() + _share_capital() + _adj_factors()
    + _stock_st_daily()
)
