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
- `industry_membership`：申万三级行业历史区间；按 `in_date/out_date` as-of 连接。
- `auction_open_daily`：Tushare 历史竞价源的隔离表。当前账号没有
  `stk_auction_o` 权限，不能用于模型或晋级。
- `auction_ticks`：QMT 09:25 快照，仅用于重叠口径验证和未来实时门控。

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
