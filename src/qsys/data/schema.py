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
        ("volume", pa.int64()),
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
        # 非除权日等于 prev_close；除权日 = (前收 - 现金红利 + 配股价x配股比) / (1 + 送转比 + 配股比)
        ("adj_prev_close", pa.float64()),
        ("limit_up", pa.float64()),
        ("limit_down", pa.float64()),
        ("is_limit_up", pa.bool_()),
        ("is_limit_down", pa.bool_()),
        ("touched_limit_up", pa.bool_()),  # 盘中触及涨停（含封住）
        ("is_broken_board", pa.bool_()),  # 炸板：触及但未封住
        ("is_first_board", pa.bool_()),  # 首板：今日涨停且昨日未涨停
        ("limit_pct", pa.float64()),  # 实际生效的涨跌幅：0.05 为反推出的 ST，否则按板块 0.10 / 0.20
        # ST 反推是 fallback（免费源没有历史 ST 名单），三个字段留作审计：
        ("st_inferred_raw", pa.bool_()),  # 价格行为启发式的原始结论
        ("st_inference_rejected_by_price_evidence", pa.bool_()),  # 被制度硬否决
        ("st_inferred_final", pa.bool_()),  # 实际采用的结论
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


PARTITIONED: dict[str, pa.Schema] = {
    "bars_daily": BARS_DAILY,
    "index_bars": INDEX_BARS,
    "minute_bars": MINUTE_BARS,
    "auction_ticks": AUCTION_TICKS,
    "daily_flags": DAILY_FLAGS,
    "market_regime": MARKET_REGIME,
    "valuation_snapshot": VALUATION_SNAPSHOT,
    "ingest_log": INGEST_LOG,
    "live_equity": LIVE_EQUITY,
    "live_fills": LIVE_FILLS,
    "live_rejects": LIVE_REJECTS,
    "live_recon": LIVE_RECON,
}

UNPARTITIONED: dict[str, pa.Schema] = {
    "instruments": INSTRUMENTS,
    "corporate_actions": CORPORATE_ACTIONS,
    "adj_factors": ADJ_FACTORS,
    "share_capital": SHARE_CAPITAL,
}

ALL: dict[str, pa.Schema] = {**PARTITIONED, **UNPARTITIONED}
