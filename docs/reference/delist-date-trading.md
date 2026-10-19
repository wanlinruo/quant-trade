# 退市日当天是否交易（探针结论）

> 实测：2026-10-19，生产 warehouse 只读查询（DuckDB 256–384 MiB、单线程、私有 spill 目录）。

## 结论

Tushare `stock_basic.delist_date`（仓库 `instruments.delist_date`）当天**不交易**。
分钟采集清单只应包含 `delist_date > dt` 的股票日；`delist_date` 当天的 QMT 空结果
不是缺数据，而是本不该请求。

## 证据

- `instruments` 中有 341 只 `delist_date` 非空的标的（1999-07-12 至 2026-10-19），
  其中 340 只在 `tushare_daily` 有历史日线。
- `tushare_daily` 中在 `delist_date` **当天**有行的：0；在 `delist_date` **之后**有行的：0。
- 近期样例的最后交易日均早于退市日，例如 920305.BJ 7-29 / 7-30、002898.SZ 7-16 / 7-17、
  000004.SZ 7-13 / 7-14。
- 2026-10-19 现场：601059.SH（信达证券）与 601198.SH（东兴证券）`delist_date=2026-10-19`，
  当日无 Tushare 日线、不在 `suspend_daily`，QMT 分钟为空；归档因此把它们判为
  “未知空结果”，退出码 1，canonical `bar_minute_1m` 当日未发布。

## 影响范围

只收窄 `daily_archive_cli._full_market_stock_days`。`alive_on` 保持闭区间语义：
service-checks 和退市整理期池等消费者另有口径，修改它们需要单独核对，不在本次修复范围内。
