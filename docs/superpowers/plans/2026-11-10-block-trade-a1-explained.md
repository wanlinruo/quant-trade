# #608 大宗交易入库与 A1 已解释警告 实施计划

> 维护者 2026-11-08 裁决：有直取大宗证据才降为已解释警告，否则维持阻断。依据：
> [探针与发布时点](../../reference/tushare-block-trade-probe.md)。

**目标**：把 Tushare `block_trade` 作为直取事实入库（`block_trade_daily`），A1 量纲规则把
“当日存在直取大宗且扣除后均价回到 [low, high]”的违规改由独立规则报**警告**，其余仍阻断。

## 全局约束

- 数据铁律：只直取或纯派生；`block_trade` 空响应 = 未发布/未知，**不写任何行**，不推断“当日无大宗”。
- 单位：`vol` 源单位万股、`amount` 源单位万元（探针 52/52 自洽）。沿用 moneyflow 先例，`amount`
  入库 ×10000 为元并在血缘登记 multiplier；`vol` 保持源单位万股，列名与含义写明单位，不换算成手。
- 同一标的同日可有多笔、无成交编号：按交易日**整天替换**，`seq` 为该日确定性排序后的序号。
- 不修改、回填或回退任何既有市场数据；不放宽 A1 本身阈值；无大宗、明细未入库时维持阻断。
- 17:00 档常为空（11-09 实测 17:20～17:40 首次出现）；与 D-12 一样回看最近 4 个交易日仍无行的日期重取。

## Task 1：`block_trade_daily` 入库

- `schema.BLOCK_TRADE_DAILY`：symbol, dt, seq(int32), price, vol, amount, buyer, seller。
- `TushareProvider.block_trade(day)`；`tushare_reference.ingest_block_trade(provider, wh, days)`。
- `WarehouseQuery.block_trades(symbols, start, end)`。
- `tushare_sync`：`_REFERENCE_STEPS`/`STEP_SOURCES`/`_REFERENCE_INGESTS` 接线，含近 4 个交易日回看重取。
- 指标字典：source `tushare.block_trade`、dataset、metrics（D.MARKET）。
- 测试：空响应不写；整天替换幂等；多笔同标的保留；单位与 multiplier；接线守卫。

## Task 2：A1 拆分

- `A1.dimension.amount_vs_volume_close` 只报**未被解释**的违规（阻断语义不变）。
- 新规则 `A1.dimension.amount_vs_volume_block_explained`：报被直取大宗解释的违规，登记已知问题
  “#608 总成交含大宗交易（已直取解释）”→ 警告；样本附大宗量额证据。
- 解释条件：当日该标的 `block_trade_daily` 有行；剩余量 = volume(手) − Σvol×100 > 0；
  剩余额 = amount − Σamount(元) 落在剩余量×100×[low×0.999, high×1.001]。
- 测试：无大宗→阻断；有大宗且扣除后回到区间→警告；有大宗但扣除后仍越界→阻断；
  大宗表无当日行→阻断；剩余量 ≤0→阻断。

## Review Focus

- 17:00 空响应不能写成“无大宗”；19:00 才写入，回看重取不覆盖已有非空日为空。
- 北交所口径不稳定：只在扣除后**确实**回到区间才降级，不对未违规行做任何扣除。
