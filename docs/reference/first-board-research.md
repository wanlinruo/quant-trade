# AI 首板机会研究（Issue #230）

## 边界

本研究在 T 日收盘后从当日未封板的普通沪深 A 股中产生 T+1 首板候选。
`daily_flags.is_first_board` 是唯一收盘首板标签。模型输出不是交易授权；历史竞价
数据、真实成交和交易收益门禁未全部通过时，不生成 `strategies/` 文件。

固定切分为 TRAIN 2023-04-01～2024-12-31、VALID 2025-01-01～06-30、
TEST 2025-07-01～12-31、OBS 2026-01-01～09-30。特征组合只按 VALID
Precision@20 选择，进入 TEST 前冻结。

## 数据依赖

- `moneyflow_daily`：Tushare `moneyflow`，金额统一为元；每日完整截面替换写。
- `industry_membership`：申万三级行业历史区间；采集时显式分页合并
  `is_new=Y/N`（上游默认只返回 Y），并按 `in_date/out_date` as-of 连接。
- `auction_open_daily`：Tushare 历史竞价源的隔离表。当前账号没有
  `stk_auction_o` 权限，不能用于模型或晋级。
- `auction_ticks`：QMT 09:25 快照，仅用于重叠口径验证和未来实时门控。

资金流与行业成员关系先在目标环境做幂等回补。`--industry` 会执行全表原子覆盖；
`--moneyflow` 只替换交易日完整截面，因此中断后可以用相同日期安全重跑：

```bash
uv run python scripts/backfill_first_board_inputs.py \
  --warehouse /data/qsys-test/warehouse-current \
  --config config/deploy.local.toml \
  --start 2023-01-01 --end 2026-09-30 \
  --moneyflow --industry
```

首次回补先在测试仓库执行并核对日期覆盖、唯一键和金额单位；验证完成前不得直接把
相同命令指向生产仓库。

历史竞价解除隔离前必须运行：

```bash
uv run python scripts/verify_auction_open_parity.py \
  --warehouse /data/qsys/warehouse --start 2026-08-17 --end 2026-09-30 \
  --out /data/qsys/research/issue-230/auction-parity.json
```

门禁要求至少 20 个交易日、1,000 个匹配标的日，价格误差不超过半分的比例至少
99.5%，量额在自动识别单位倍率后相对误差不超过 1% 的比例至少 99%。

## 隔夜研究

模型比较 #210 重排、动量、固定启发式、Logistic，以及纯价量、资金流、行业扩散、
资金流与行业组合四种两阶段 LightGBM。两阶段概率为：

`P(T+1 首板) = P(T+1 触板) × P(T+1 首板 | 已触板)`。

运行入口：

```bash
uv run python scripts/research_first_board.py \
  --warehouse /data/qsys/warehouse \
  --legacy-ranking /data/qsys/research/issue-210/run-20261010/predictions.parquet \
  --out /data/qsys/research/issue-230/run-YYYYMMDD
```

产物包含数据指纹、全量预测、Top20、指标、月份、校准、冷启动/反包分层、特征重要性、
模型和 verdict。即使隔夜排序通过，竞价证据缺失时 verdict 仍固定
`promote_to_shadow=false`、`promote_to_trading=false`。

## 当前能力结论

2026-10-10 只读权限探测：`moneyflow` 和 `index_member_all` 可用；
`stk_auction_o` 返回权限错误 40203。故 #233 可进入回补，#234 在开通权限前保持未完成，
#230 只能先形成隔夜排序证据和 provisional 交易结论。

## 09:31 首分钟过滤替代路径（2026-10-11）

历史竞价既用于原设计的 Top20→0–3 只门控，也用于开盘附近的成交裁决；但冻结
隔夜模型本身不读取竞价数据。为避免在门控价值尚未得到证据前购买历史权限，先独立
裁决一个延迟入场变体：

1. 隔夜 Top20、胜出模型与 VALID/TEST/OBS 切分保持冻结；
2. 09:31 只使用此时已经可见的第一根连续竞价分钟线训练二次门控，每日留下 0–3 只；
3. 下单后只允许用严格后继、在 09:32 可见的分钟线判断成交；缺线直接拒单，禁止回退
   到日线开盘价；
4. 分钟身份以规范表 `visible_at` 为准，而非供应商 `ts`。付费源第一分钟可能标为
   09:30，QMT 标为 09:31，但两者均在 09:31 才完整可见；
5. 二次 Logistic 门控只在 VALID 前70%拟合，在 VALID 后30%从预先限定的
   80%/90%/95%分位阈值中选择一次，TEST/OBS 不参与模型或阈值选择；
6. 主裁决为下一交易日开盘退出、额外1.0%入场成本压力；同时检查0.5%/1.5%成本和
   11:25/14:25退出，并与隔夜 Rank Top1/Top3、无阈值 Gate Top3 对照。

运行入口：

```bash
uv run python scripts/research_first_board_first_minute.py \
  --warehouse /data/qsys/warehouse \
  --ranking /data/qsys/research/issue-230/run-20261011/predictions.parquet \
  --out /data/qsys/research/issue-230/first-minute-YYYYMMDD
```

研究专用执行适配器位于 `scripts/issue_230_first_minute_strategy.py`，刻意不放入
`strategies/`，不能从策略页导入。该路径即使历史门禁通过，也只允许另开冻结规则后的
前瞻只读影子观察；它不替代 #234 对原 09:25 竞价路径的历史同口径验证，也不直接授权
模拟或实盘。
