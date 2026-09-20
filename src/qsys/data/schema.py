"""仓库四张地基表 + 采集日志的 pyarrow schema。

bars_daily 存不复权价格，只追加永不重写；复权因子单独存于 adj_factors。
"""

from __future__ import annotations

import pyarrow as pa

INSTRUMENTS = pa.schema(
    [
        ("symbol", pa.string()),
        ("name", pa.string()),
        ("market", pa.string()),
        ("list_date", pa.date32()),
        ("delist_date", pa.date32()),  # 未退市为 null
        ("board", pa.string()),  # main / gem / star / bse
        # 取这份快照的日期。instruments 是覆盖写的单张表，没有 as_of 就无法
        # 判断 board / list_date / delist_date 有多陈旧，也无法把 name 安全地
        # 限定在快照日使用——用快照名填历史就是结转。
        ("as_of", pa.date32()),
    ]
)

# 每日 stock_basic 快照的累积表。instruments 只留最新一份，而 daily_flags
# 每次同步要重建近 150 天，一天的快照不够用，所以按观测日逐日留存。
# 这是「点观测」，不是区间——绝不把它转成区间，那需要推断端点。
NAME_SNAPSHOTS = pa.schema(
    [
        ("dt", pa.date32()),  # 观测日，即取 stock_basic 的那一天
        ("symbol", pa.string()),
        ("name", pa.string()),
    ]
)

BARS_DAILY = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.int64()),
        ("amount", pa.float64()),
    ]
)

# daily_bars() 视图的契约。列名与 BARS_DAILY 相同，但 volume 是 float64：
# 付费源给的是带小数的手数（582305.98），取整会丢掉真实精度。
# BARS_DAILY 保留 int64，因为它描述的是已冻结的 bars_daily 旧表。
DAILY_BARS = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
        ("amount", pa.float64()),
    ]
)

# 规范窄表：与旧 tushare_daily 并行构建，生产消费者尚未切换。
BAR_DAILY = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("pre_close", pa.float64()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
        ("amount", pa.float64()),
    ]
)

VALUATION_DAILY = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("turnover_ratio", pa.float64()),
        ("free_turnover_ratio", pa.float64()),
        ("volume_ratio", pa.float64()),
        ("pe", pa.float64()),
        ("pe_ttm", pa.float64()),
        ("pb", pa.float64()),
        ("ps", pa.float64()),
        ("ps_ttm", pa.float64()),
        ("dividend_yield", pa.float64()),
        ("dividend_yield_ttm", pa.float64()),
        ("market_cap", pa.float64()),
        ("circulating_market_cap", pa.float64()),
    ]
)

SHARE_CAPITAL_HISTORY = pa.schema(
    [
        ("symbol", pa.string()),
        ("effective_date", pa.date32()),
        ("announcement_date", pa.date32()),
        ("total_shares", pa.float64()),
        ("circulating_shares", pa.float64()),
    ]
)

ADJUSTMENT_FACTOR_DAILY = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("price_factor", pa.float64()),
        ("share_factor", pa.float64()),
    ]
)

# 阶段 5 规范状态与派生表。与旧表并行构建，生产消费者尚未切换。
PRICE_LIMIT_DAILY = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("pre_close", pa.float64()),
        ("limit_up", pa.float64()),
        ("limit_down", pa.float64()),
    ]
)

SECURITY_STATUS_DAILY = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("is_st", pa.bool_()),
        ("is_suspended", pa.bool_()),
        ("is_delisting", pa.bool_()),
        ("st_source", pa.string()),
        ("suspension_source", pa.string()),
        ("delisting_source", pa.string()),
    ]
)

SHARE_CAPITAL = pa.schema(
    [
        ("symbol", pa.string()),
        # 股本生效日（QMT m_timetag）。股本是公开可即时观察的事实，交易所
        # 每日据此发布总市值，故以生效日而非公告日为准。
        ("dt", pa.date32()),
        ("ann_date", pa.date32()),  # 公告日，留作更严格 PIT 口径的口子
        ("total", pa.float64()),  # 总股本（股）
        ("circulating", pa.float64()),  # 流通股本（股）
    ]
)

INDEX_BARS = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        # Tushare index_daily.vol 有小数（全历史 5,270 行中实测 186 行）。
        # 2026-09-14 从 int64 无损迁移为 float64，旧整数值读取时自然提升。
        ("volume", pa.float64()),
        ("amount", pa.float64()),
    ]
)

MINUTE_BARS = pa.schema(
    [
        ("symbol", pa.string()),
        # 该分钟的**收盘时刻**：09:30–09:31 这根记 09:31。
        # 如此 PIT 可见性就是 dt <= as_of，没有差一分钟的歧义。
        ("dt", pa.timestamp("us")),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.int64()),
        ("amount", pa.float64()),
    ]
)

# 冻结的付费分钟源。与 QMT MINUTE_BARS 的关键区别是：成交量单位为股，
# 并且源文件逐分钟携带后复权累计因子。它和 PAID_DAILY 一样只读，不能
# 注册进 PARTITIONED，否则 Warehouse.write 会获得覆盖历史证据的能力。
PAID_MINUTE_BARS = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.timestamp("us")),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.int64()),
        ("amount", pa.float64()),
        ("factor", pa.float64()),
    ],
    metadata={
        b"provider": b"yuhaha-baidupan",
        b"frequency": b"1min",
        b"timestamp_policy": b"source value; no shift",
        b"price_unit": b"CNY",
        b"volume_unit": b"share",
        b"amount_unit": b"CNY",
        b"factor": b"source cumulative backward adjustment",
    },
)

# 阶段 6 规范历史分钟线。``ts`` 是分钟收盘标签；``visible_at`` 单独保存
# 实际可见时刻，避免把供应商到达延迟混进市场事实时间。
BAR_MINUTE_1M = pa.schema(
    [
        ("symbol", pa.string()),
        ("ts", pa.timestamp("us")),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),  # 手；外部源的股数除以 100 后可能非整数
        ("amount", pa.float64()),
        ("source", pa.string()),
        ("visible_at", pa.timestamp("us")),
    ]
)

CORPORATE_ACTIONS = pa.schema(
    [
        ("symbol", pa.string()),
        ("ex_date", pa.date32()),  # 除权除息日
        ("cash_per_share", pa.float64()),  # 每股现金红利（税前）
        ("stock_per_share", pa.float64()),  # 每股送转股数
        ("rights_per_share", pa.float64()),  # 每股配股数
        ("rights_price", pa.float64()),  # 配股价
    ]
)

ADJ_FACTORS = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("cum_factor", pa.float64()),  # 后复权累计因子（价格用）
        # 股本累计比例（成交量用）。价格因子含现金分红那一项，拿它去调
        # 成交量会多调——股数只随送转与配股变化，与派现无关。
        ("cum_share_factor", pa.float64()),
    ]
)

DAILY_FLAGS = pa.schema(
    [
        ("dt", pa.date32()),
        ("symbol", pa.string()),
        ("prev_close", pa.float64()),  # 无前收（首个 bar）为 null
        # 除权除息参考价。涨跌停是按它算的，不是按 prev_close。
        # 直取自 Tushare 当日 `pre_close`（2026-09-12 起，不再由
        # corporate_actions 事件表自算）：非除权日等于前一日收盘，
        # 除权日是交易所公布的除权后参考价。
        ("adj_prev_close", pa.float64()),
        ("limit_up", pa.float64()),
        ("limit_down", pa.float64()),
        ("is_limit_up", pa.bool_()),  # 封板：非 ST 优先取 limit_list_d 的 U/Z
        ("is_limit_down", pa.bool_()),
        ("touched_limit_up", pa.bool_()),  # 盘中触及涨停（含封住）
        ("is_broken_board", pa.bool_()),  # 炸板：触及但未封住
        ("is_first_board", pa.bool_()),  # 首板：今日涨停且昨日未涨停
        # 该行 limit_up 实际对应的那一档涨跌幅，由直取涨停价解码得出，
        # 与同一行的 limit_up 恒自洽；解不出就是 null。
        # **不能用它判 ST**：2026-07-06 起主板 ST 与普通股同为 0.10，
        # 而创业板/科创板 ST 一直是 0.20。ST 只认 is_st。
        ("limit_pct", pa.float64()),
        # ST 真值，只认 Tushare stock_st（D-11）；缺行为 null，不从名称推断。
        # 2026-09-12 之前这里是三条价格启发式反推出来的，一致率 93.8%，已删除。
        ("is_st", pa.bool_()),
        # 该行 ST 值的来源：有 stock_st 行为 "stock_st"，否则 null。
        ("st_source", pa.string()),
    ]
)

MARKET_REGIME = pa.schema(
    [
        ("dt", pa.date32()),
        ("limit_up_count", pa.int64()),
        ("limit_down_count", pa.int64()),
        ("limit_up_down_ratio", pa.float64()),
        ("touched_limit_up_count", pa.int64()),
        ("broken_board_count", pa.int64()),
        ("broken_board_rate", pa.float64()),
        ("first_board_count", pa.int64()),
        ("market_advance_ratio", pa.float64()),
        ("market_median_return_1d", pa.float64()),
        ("market_total_money", pa.float64()),
        ("prior_limitup_nextday_return_mean", pa.float64()),
        ("prior_limitup_nextday_return_median", pa.float64()),
        ("valid_stock_count", pa.int64()),
        ("index_ma20_slope_5d", pa.float64()),  # 无指数数据时为 null，不填 0
    ]
)

# 字段暂与旧表同构，以便逐键逐字段验证；血缘已改为只依赖规范事实。
MARKET_FLAGS_DAILY = DAILY_FLAGS
MARKET_REGIME_DAILY = MARKET_REGIME

VALUATION_SNAPSHOT = pa.schema(
    [
        ("dt", pa.date32()),
        ("symbol", pa.string()),
        ("close", pa.float64()),
        ("market_cap", pa.float64()),  # 总市值（元）
        ("circulating_market_cap", pa.float64()),  # 流通市值（元）
        ("turnover_ratio", pa.float64()),  # 换手率（%）
    ]
)

INGEST_LOG = pa.schema(
    [
        ("dataset", pa.string()),
        ("dt", pa.date32()),
        ("provider", pa.string()),
        ("ingested_at", pa.timestamp("us")),
        ("row_count", pa.int64()),
        ("ok", pa.bool_()),
        ("message", pa.string()),
    ]
)

# --- 数据层盘点：自动验证结果与人工证据物理隔离 ---

METRIC_CHECKS = pa.schema(
    [
        ("run_id", pa.string()),
        ("checked_at", pa.timestamp("us")),
        ("metric_id", pa.string()),
        ("check_id", pa.string()),
        ("category", pa.string()),
        ("scope_start", pa.date32()),
        ("scope_end", pa.date32()),
        ("passed", pa.bool_()),
        ("severity", pa.string()),
        ("known_issue", pa.string()),
        ("violations", pa.int64()),
        ("checked_rows", pa.int64()),
        ("detail", pa.string()),
        ("sample_path", pa.string()),
    ]
)

METRIC_EVIDENCE = pa.schema(
    [
        ("metric_id", pa.string()),
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("our_value", pa.string()),
        ("reference_value", pa.string()),
        ("reference_source", pa.string()),
        ("verdict", pa.string()),
        ("checked_by", pa.string()),
        ("checked_at", pa.timestamp("us")),
        ("note", pa.string()),
    ]
)

# 表级与关系级健康证据。字段级历史仍只写 METRIC_CHECKS，三层结果物理隔离，
# 避免用一张含大量 nullable 列的万能表掩盖检查对象的语义差异。
DATASET_CHECKS = pa.schema(
    [
        ("run_id", pa.string()),
        ("checked_at", pa.timestamp("us")),
        ("dataset_id", pa.string()),
        ("check_id", pa.string()),
        ("dimension", pa.string()),
        ("scope_start", pa.date32()),
        ("scope_end", pa.date32()),
        ("status", pa.string()),
        ("evidence_at", pa.timestamp("us")),
        ("expires_at", pa.timestamp("us")),
        ("required", pa.bool_()),
        ("violations", pa.int64()),
        ("checked_rows", pa.int64()),
        ("detail", pa.string()),
    ]
)

RELATION_CHECKS = pa.schema(
    [
        ("run_id", pa.string()),
        ("checked_at", pa.timestamp("us")),
        ("upstream", pa.string()),
        ("downstream", pa.string()),
        ("check_id", pa.string()),
        ("dimension", pa.string()),
        ("scope_start", pa.date32()),
        ("scope_end", pa.date32()),
        ("status", pa.string()),
        ("evidence_at", pa.timestamp("us")),
        ("expires_at", pa.timestamp("us")),
        ("required", pa.bool_()),
        ("upstream_rows", pa.int64()),
        ("downstream_rows", pa.int64()),
        ("matched_rows", pa.int64()),
        ("orphan_rows", pa.int64()),
        ("violations", pa.int64()),
        ("detail", pa.string()),
    ]
)

# --- 实盘会话日志。监控台只读这四张表 ---

LIVE_EQUITY = pa.schema(
    [
        ("dt", pa.date32()),
        ("cash", pa.float64()),
        ("market_value", pa.float64()),
        ("total_value", pa.float64()),
    ]
)

LIVE_FILLS = pa.schema(
    [
        ("dt", pa.date32()),
        ("client_order_id", pa.string()),
        ("broker_order_id", pa.string()),
        ("broker_trade_id", pa.string()),
        ("symbol", pa.string()),
        ("side", pa.string()),
        ("qty", pa.int64()),
        ("price", pa.float64()),
        ("fee", pa.float64()),
        ("filled_at", pa.timestamp("us")),
    ]
)

LIVE_REJECTS = pa.schema(
    [
        ("dt", pa.date32()),
        ("client_order_id", pa.string()),
        ("symbol", pa.string()),
        ("qty", pa.int64()),
        ("reason", pa.string()),
        ("at", pa.timestamp("us")),
    ]
)

LIVE_RECON = pa.schema(
    [
        ("dt", pa.date32()),
        ("at", pa.timestamp("us")),
        ("layer", pa.string()),  # intraday / eod / sim_vs_live
        ("is_consistent", pa.bool_()),
        ("cash_diff", pa.float64()),
        ("n_mismatches", pa.int64()),
        ("detail", pa.string()),
    ]
)


AUCTION_TICKS = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),  # 交易日
        # 快照时刻（北京时间）。QMT 的 tick.time 是毫秒 epoch（UTC），
        # 导出时已 +8 小时。沪市固定 3 秒一笔，深市按盘口变化推送。
        ("ts", pa.timestamp("us")),  # 与 minute_bars.dt 同精度
        # 竞价期间 bid1 == ask1 == **虚拟开盘价**，bid_vol1 == ask_vol1 ==
        # **匹配量（手）**。09:25 撮合完成后这条恒等式才失效。
        ("bid1", pa.float64()),
        ("ask1", pa.float64()),
        ("bid_vol1", pa.int64()),
        ("ask_vol1", pa.int64()),
        ("last_price", pa.float64()),  # 撮合前为 0
        ("volume", pa.int64()),  # 累计成交量（手），撮合前为 0
        ("amount", pa.float64()),
    ]
)

# 冻结的付费源日线，28 列。永久只读——**不要**注册进
# PARTITIONED，那会让 Warehouse.write("paid_daily", ...) 成为可能。
# 列顺序严格对应卖家 Excel 第三行；单位换算只做乘法，见 pipeline/paid_daily.py。
_PAID_DAILY_NUMERIC = [
    "open", "high", "low", "close", "pre_close", "change", "pct_change",
    "volume", "amount", "factor", "turnover_ratio", "free_turnover_ratio",
    "volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm",
    "dividend_yield", "dividend_yield_ttm", "total_shares",
    "circulating_shares", "free_shares", "market_cap", "circulating_market_cap",
]
PAID_DAILY = pa.schema(
    [("dt", pa.date32()), ("symbol", pa.string()), ("name", pa.string())]
    + [(n, pa.float64()) for n in _PAID_DAILY_NUMERIC],
    metadata={
        b"provider": b"yuhaha-baidupan",
        b"volume_unit": b"lot=100 shares",
        b"money_unit": b"CNY",
        b"share_capital_unit": b"share",
        b"percentage_unit": b"percent",
        b"factor": b"cumulative backward adjustment",
    },
)

# Tushare Pro 日线，28 列。与 PAID_DAILY 同名、同序、同类型——换源不改 schema，
# 只改 metadata 的来源标记。这样计划 A 建立的查询层与派生链路无需任何改动。
TUSHARE_DAILY = pa.schema(
    list(PAID_DAILY),
    metadata={
        b"provider": b"tushare-pro",
        b"volume_unit": b"lot=100 shares",
        b"money_unit": b"CNY",
        b"share_capital_unit": b"share",
        b"percentage_unit": b"percent",
        b"factor": b"cumulative backward adjustment",
        b"apis": b"daily+daily_basic+adj_factor",
    },
)

# Tushare 参考事实。四张日表均为外部接口直取值，不用价格或名称反推。
PRICE_LIMITS_DAILY = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("pre_close", pa.float64()),
        ("limit_up", pa.float64()),
        ("limit_down", pa.float64()),
    ]
)

SUSPEND_DAILY = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("suspend_type", pa.string()),  # S 停牌 / R 复牌
        ("suspend_timing", pa.string()),  # 全天停牌时可为 null
    ]
)

# 每日 ST 真值。Tushare stock_st 返回的是当日 ST 正例列表；落库时与当日
# tushare_daily 股票全集左连，显式写出 True/False，避免把“接口失败/未回填”
# 和“当日不是 ST”混为一谈。
STOCK_ST_DAILY = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("is_st", pa.bool_()),
        ("name", pa.string()),
        ("st_type", pa.string()),
        ("st_type_name", pa.string()),
    ]
)

# limit_list_d 实测自 2020 年起，且不含 ST 股票统计；同步 U 涨停与 Z 炸板。
LIMIT_LIST_DAILY = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("limit_type", pa.string()),  # U 涨停 / D 跌停 / Z 炸板
        ("limit_times", pa.int64()),
        ("open_times", pa.int64()),
        ("first_time", pa.string()),
        ("fd_amount", pa.float64()),
        ("limit_amount", pa.float64()),
        ("float_mv", pa.float64()),
    ]
)

# 曾用名区间原样落库；ST 状态由区间覆盖关系纯派生。
NAME_HISTORY = pa.schema(
    [
        ("symbol", pa.string()),
        ("name", pa.string()),
        ("start_date", pa.date32()),
        ("end_date", pa.date32()),
        ("ann_date", pa.date32()),
        ("change_reason", pa.string()),
    ]
)

# --- 规范基础资料（与旧表并行构建，消费者尚未切换） ---

SECURITY_MASTER = pa.schema(
    [
        ("symbol", pa.string()),
        ("name", pa.string()),
        ("market", pa.string()),
        ("list_date", pa.date32()),
        ("delist_date", pa.date32()),
        ("board", pa.string()),
        ("as_of", pa.date32()),
    ]
)

SECURITY_NAME_HISTORY = pa.schema(
    [
        ("symbol", pa.string()),
        ("name", pa.string()),
        ("start_date", pa.date32()),
        ("end_date", pa.date32()),
        ("ann_date", pa.date32()),
        ("change_reason", pa.string()),
    ]
)

TRADING_CALENDAR = pa.schema(
    [
        ("market", pa.string()),
        ("dt", pa.date32()),
        ("is_open", pa.bool_()),
        ("pretrade_date", pa.date32()),
    ]
)


# 退市整理期池：每个交易日处于退市整理期的标的，纯派生自 name_history 中
# change_reason='退市整理期' 的区间（Tushare namechange 直取）。保留公告日，
# 使用方按「T-1 已公告」自行过滤；不从名称里找「退」字（D-11）。
DELISTING_POOL = pa.schema(
    [
        ("dt", pa.date32()),
        ("symbol", pa.string()),
        ("name", pa.string()),
        ("start_date", pa.date32()),
        ("end_date", pa.date32()),
        ("ann_date", pa.date32()),
    ]
)


PARTITIONED: dict[str, pa.Schema] = {
    "bars_daily": BARS_DAILY,
    "bar_daily": BAR_DAILY,
    "valuation_daily": VALUATION_DAILY,
    "adjustment_factor_daily": ADJUSTMENT_FACTOR_DAILY,
    "price_limit_daily": PRICE_LIMIT_DAILY,
    "security_status_daily": SECURITY_STATUS_DAILY,
    "market_flags_daily": MARKET_FLAGS_DAILY,
    "market_regime_daily": MARKET_REGIME_DAILY,
    "index_bars": INDEX_BARS,
    "minute_bars": MINUTE_BARS,
    "bar_minute_1m": BAR_MINUTE_1M,
    "auction_ticks": AUCTION_TICKS,
    "daily_flags": DAILY_FLAGS,
    "market_regime": MARKET_REGIME,
    "valuation_snapshot": VALUATION_SNAPSHOT,
    "ingest_log": INGEST_LOG,
    "metric_checks": METRIC_CHECKS,
    "metric_evidence": METRIC_EVIDENCE,
    "dataset_checks": DATASET_CHECKS,
    "relation_checks": RELATION_CHECKS,
    "live_equity": LIVE_EQUITY,
    "live_fills": LIVE_FILLS,
    "live_rejects": LIVE_REJECTS,
    "live_recon": LIVE_RECON,
    "tushare_daily": TUSHARE_DAILY,
    "price_limits_daily": PRICE_LIMITS_DAILY,
    "suspend_daily": SUSPEND_DAILY,
    "stock_st_daily": STOCK_ST_DAILY,
    "limit_list_daily": LIMIT_LIST_DAILY,
    "name_snapshots": NAME_SNAPSHOTS,
    "delisting_pool": DELISTING_POOL,
    "trading_calendar": TRADING_CALENDAR,
}

UNPARTITIONED: dict[str, pa.Schema] = {
    "instruments": INSTRUMENTS,
    "corporate_actions": CORPORATE_ACTIONS,
    "adj_factors": ADJ_FACTORS,
    "share_capital": SHARE_CAPITAL,
    "name_history": NAME_HISTORY,
    "security_master": SECURITY_MASTER,
    "security_name_history": SECURITY_NAME_HISTORY,
    "share_capital_history": SHARE_CAPITAL_HISTORY,
}

ALL: dict[str, pa.Schema] = {**PARTITIONED, **UNPARTITIONED}

# 外部冻结证据可以查询和登记进指标字典，但不能通过 Warehouse.write 改写。
READ_ONLY: dict[str, pa.Schema] = {
    "paid_daily": PAID_DAILY,
    "paid_minute_bars": PAID_MINUTE_BARS,
}
