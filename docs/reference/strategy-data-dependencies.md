# 策略的数据依赖与数据源映射

更新日期：2026-09-14
对应策略版本：`STRUCTURAL_STRATEGY_V1_2_PREAUCTION_EARLY_ORDER_FINAL`（聚宽研究版）

本文把策略实际调用的每一个聚宽接口，映射到自研系统的数据集与可选的外部数据源，
用于判断「哪些能换成真值直取、哪些必须自己采、哪些买不到」。

## 一、策略时序

```
09:14  preauction_build_pool   建昨日/静态候选池（prepare_stock_list），不下单
09:24  preauction_buy_high     HIGH_MAIN / HIGH_CHINEXT 真实竞价挂单
09:26  buy                     正常路线：LOW + 未被早单占用的候选
11:25  sell_1125               HIGH 止盈/MA5；LOW/WEAK 非涨停且盈利则卖
14:25  sell_low_weak_1425      LOW/WEAK 非涨停无条件卖
14:50  sell_high_1450          HIGH 止盈/MA5
```

WEAK 家族在 S7 Gate 之前整体删除（`_v12_keep_non_weak`），炸板池实际无人使用。

## 二、逐接口映射

| 策略调用 | 用途 | 自研系统对应 | Tushare 对应 | 门槛 |
|---|---|---|---|---|
| `get_call_auction(fields=['time','volume','current'])` | **三个入场判定 + 09:24 早单** | `auction_ticks`（QMT，9 秒快照） | **无等价接口** | — |
| `get_price(..., 'high_limit','low_limit')` | 涨停判定、挂单价上界 | `price_limits_daily` / `WarehouseQuery.price_limits()`；`daily_flags` 暂保留可重算派生 | `stk_limit` + 同源 `daily.pre_close` | 2000 积分 |
| `get_price(..., 'paused')` | 停牌过滤 | `suspend_daily` / `WarehouseQuery.suspensions()` | `suspend_d` | 2000 积分 |
| `attribute_history(N,'1d',...)` | 历史 OHLCV/money | `tushare_daily` / `WarehouseQuery.daily_bars()` | `daily` + `adj_factor` | 2000 积分 |
| `get_valuation(turnover_ratio, market_cap, circulating_market_cap)` | HIGH/WEAK 市值过滤 | `tushare_daily` 估值列 | `daily_basic` | 2000 积分 |
| `get_all_securities('stock', date)` | 股票池 | `instruments`（Tushare，含退市） | `stock_basic` | 2000 积分 |
| `get_security_info(s).start_date` | 次新股过滤（50 天） | `instruments.list_date` | `stock_basic.list_date` | 2000 积分 |
| `attribute_history('000300.XSHG', 80, '1d')` | `index_ma20_slope_5d` | `index_bars`（Tushare 已回填至 2026-09-11） | `index_daily` | 2000 积分 |
| `get_trade_days` | 交易日历 | `AShareCalendar`（akshare） | `trade_cal` | 120 积分 |
| `cur[s].is_st` / `'退' in cur[s].name` | ST 与退市过滤 | `name_history` / `WarehouseQuery.name_history()`，按区间展开 | `namechange` | 2000 积分 |
| `get_hl_stock` / `get_ever_hl_stock2` | 昨日首板 / 昨日炸板 | `limit_list_daily` / `WarehouseQuery.limit_list()`；`daily_flags` 保留派生 | `limit_list_d` 的 `limit_times` / `open_times` | 5000 积分 |
| `get_current_data().last_price` | 盘中检查点（11:25/14:25/14:50） | `minute_bars` | — （实盘走 QMT） | — |

## 三、竞价逐笔是唯一买不到的东西

策略的三个入场判定**全部**依赖 `get_call_auction` 的逐笔记录：

| 判定 | 用到的竞价字段 | 阈值 |
|---|---|---|
| `check_low_open` | `current / 昨收` | 0.955 ≤ r ≤ 0.97 |
| `check_high_open` | `volume / 昨日量`、`current / 昨收` | ≥ 0.03；1 < r < 1.06 |
| `check_weak_to_strong` | 同上 | ≥ 0.03；0.98 < r < 1.09 |
| `s4v2_low_main_auction_ratio` | `volume / 昨日量` | ≥ 0.020 |

两条路线取的**不是同一行**：

- 09:26 路线用 `get_first_auction_record` → `df.iloc[0]`，即 09:15 之后**第一条**有效快照；
- 09:24 早单用 `_preauction_latest_auction_record` → 按 `time` 排序后**最后一条**，
  区间 `09:15:00 ~ 09:24:00`。

**Tushare 的 `stk_auction`（集合竞价成交，¥500/年，9:26~9:29 更新，历史自 2025-01）
给的是 9:25 撮合完成后的最终结果**——既不是第一条，也不是 09:24 那一条。
用它替代任一路线都会引入前视偏差，且偏差最大的正是「竞价末段价格剧烈收敛」
的标的，也就是首板次日高开这一类。

**结论：竞价逐笔只有实时行情通道（QMT）能给，必须自己每天采，漏一天永久缺一天。**

## 四、策略作者已确认的限制

策略文件顶部原文：

> Historical JoinQuant minute backtests were previously confirmed **not to expose
> pre-09:25 auction rows**. Therefore this early-order path is intended for an
> environment where 09:24 auction data is actually observable.

也就是说，**09:24 早单路径在聚宽历史回测里根本不会触发**——`auction_row is None`
时直接 `continue`，全部回落到 09:26 路线。聚宽回测出来的绩效是纯 09:26 路线的绩效，
早单路径未经任何历史验证。

自研系统的 `auction_ticks` 正是「09:24 可观测」的环境，但截至 2026-09-13 只有
2026-09-03 至 09-10 共 8 天、265 只标的。**8 天不足以做任何绩效结论，只够做机制验证。**

## 五、`filter_kcbj_stock` 漏掉 920 号段

```python
def filter_kcbj_stock(initial_list):
    return [s for s in initial_list if s[0] != '4' and s[0] != '8' and s[:2] != '68']
```

意图是排除北交所与科创板。实际能排掉 `43xxxx`、`83xxxx`、`87xxxx`、`68xxxx`，
**但排不掉 `920xxx`**——北交所 2023 年启用的新号段，首字符是 `9`。

付费日线里 2026-09-09 全市场 5,550 只中有 343 只北交所，其中就包含 920 号段。
若聚宽的 `get_all_securities('stock')` 返回北交所标的，这些会漏进候选池。
**需要在聚宽环境实测确认。**

自研系统的 `qsys.markets.ashare.board_of` 把 `43/83/87/92` 一律判为 `bse`，
不存在这个漏洞。

### B 股口径的未来风险（2026-09-13）

`market_regime` 先沿用原策略的 4/8/68 前缀过滤，再按调用方的 `boards`
取交集。前一层不排 B 股（900/200），生产默认的后一层
`main,gem,star` 则会排除 `bshare`，两层语义并不完全一致。

当前不合并这两层：实测 2026-09-09 的付费源全市场 5,550 只和旧表中，
900/200 开头均为 0 只，因此不会影响现有派生结果。若未来数据源开始包含
B 股，必须先明确广度股票池是否纳入外币计价标的，再统一过滤口径并重新
评估 S6/S7 阈值；不能让两层过滤的偶然交集替代口径决策。

## 六、09:24 早单的资金占用

`preauction_buy_high` 按 S8 权重把 `available_cash` 全额分配给早单候选，
`buy()`（09:26）再用当时的 `available_cash`。限价单挂出后资金被冻结，
**早单若未成交，那部分资金在 09:26 仍处于冻结状态**，正常路线可用资金相应减少。

这是设计取舍不是缺陷，但迁移到自研系统时撮合与资金冻结语义必须一致复现，
否则回测与实盘的资金路径会分叉。

## 七、结论

| 类别 | 数据 | 来源 |
|---|---|---|
| **买不到，必须自采** | 竞价逐笔（09:15–09:24） | QMT 实时，每日采集 |
| **可换真值直取** | 涨跌停价、停牌、指数日线、退市股、首板/炸板 | Tushare |
| **已有** | 日线、估值、复权因子、参考表、分钟线（2009-2025） | `tushare_daily`、Tushare 参考接口、QMT |
| **不必买** | 集合竞价成交、历史分钟、RT 行情 | 口径对不上 / 已有 / QMT 已覆盖 |
