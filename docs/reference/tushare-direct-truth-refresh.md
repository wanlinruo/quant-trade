# Tushare 直取真值刷新与旧源清理

更新日期：2026-09-15

## 目标与裁决

5000 积分账户新增采用 `stock_st`（3000 积分起、历史从 2000-01-01）作为每日
ST 真值。策略生产区间从 2023-01-01 开始，因此可以完整替代
`paid_daily.name -> is_st`。最新接口值按业务键覆盖旧值，但所有回测历史继续保留；
“覆盖”不等于只留最新一天。

| 消费数据 | 新主来源 | 旧路径 |
|---|---|---|
| ST 状态 | `stock_st_daily.is_st` | `paid_daily.name` / `namechange` |
| 涨跌停价 | `price_limits_daily` (`stk_limit`) | `daily_flags` 计算值 |
| 收盘封板／炸板 | 非 ST：`limit_list_daily` (`limit_list_d` U/Z)；ST：价格口径 | 仅凭 OHLC 判断 |
| 停牌 | `suspend_daily` (`suspend_d`) | 无显式策略过滤 |
| 市值、流通市值、换手率 | `tushare_daily` (`daily_basic`) | 收盘价 × QMT 股本反算 |

`name_history`、`name_snapshots`、`share_capital` 和 `corporate_actions` 仍保留：
它们分别描述名称、股本事件和持仓除权，不与 ST 布尔真值重复。

## 完整性设计

`stock_st` 只返回 ST 正例。落库时与同日 `tushare_daily` 股票全集左连接，为每个
股票日显式写入 `is_st=true/false`。表不存在或未回填时是“未知”，不会被误当成
“非 ST”。`daily_flags` 只在整个窗口的 ST 键覆盖完整时使用新表；部分覆盖不会和
旧来源逐行拼接。

停牌和涨跌停榜单采用“指定日期完整替换”语义。重新请求为空时会清掉该日期的旧记录，
解决只增不删造成的残留。行情与涨跌停价格的空响应仍按异常处理，不据此清空事实。

## 生产执行与日志

```bash
uv run python scripts/tushare_truth_backfill.py \
  --warehouse /data/qsys/warehouse --start 2023-01-01 --end YYYY-MM-DD
```

脚本默认按 500 次/分钟主动限流，并自动从 `--start` 向前多回填 150 个自然日，
以满足 flags 年初连续性。它分批回填四张直取表，随后重建 `daily_flags`、
`market_regime` 和 `valuation_snapshot`。结果写入
`artifacts/tushare_truth_backfill_*.json`，日志不含 token。

先运行清理审计：

```bash
uv run python scripts/cleanup_legacy_daily.py \
  --warehouse /data/qsys/warehouse --start 2023-01-01 --end YYYY-MM-DD
```

只有 `stock_st_daily` 与 `tushare_daily` 在生产区间键集合完全一致时才允许清理
`paid_daily`。检查 JSON 中 `missing=0`、`extra=0`、行数及 SHA256 后，在相同命令
末尾增加 `--execute`。默认清理对象仅为 `bars_daily` 和 `paid_daily`；修补证据与
质量报告不会默认删除。

## 验收与回滚

1. 每个 `tushare_daily(symbol, dt)` 都有一个 `stock_st_daily` 键。
2. `daily_flags.st_source` 在新覆盖区间全部为 `stock_st`。
3. 涨跌停价来自 `price_limits_daily`；非 ST 封板／炸板优先来自
   `limit_list_daily` 的 U/Z，不能只凭 `close == limit_up` 宣称封板；停牌标的在选股前剔除。
4. 估值与 `daily_basic` 只有元到亿元的 `1e8` 单位差，不允许股本反算。
5. ST、首板、炸板和生态因子差异形成逐键报告后才清理旧源。
6. 清理日志保存文件路径、字节数和 SHA256；闸门失败不删除任何目录。

若验收失败，停止定时同步并按 manifest 的 SHA256 恢复外部备份，再重建派生表。
不得用当前名称回填历史，也不得把缺失 ST 默认成 false。


## 验收结果（2026-09-15 实测）

回填执行日志：`artifacts/tushare_truth_backfill_20260915.json`
（12:56:33 → 13:39:27，998 个交易日，2023-01-01 ~ 2026-09-14，facts 起点 2022-08-04）。
对账报告：`artifacts/切源对账报告_2026-09-15.md`。

| # | 验收条 | 结果 | 证据 |
|---|---|---|---|
| 1 | 每个 `tushare_daily(symbol,dt)` 都有 `stock_st_daily` 键 | ✅ | 键数各 4,801,648；`missing=0`、`extra=0` |
| 2 | `daily_flags.st_source` 全为 `stock_st` | ✅ | 4,573,249 / 4,573,249（100%），无一行回退旧链路 |
| 3 | 涨跌停价优先直取；停牌标的选股前剔除 | ✅ | 直取价在 41,556 行上生效；`suspend_daily` 897 个交易日全覆盖、20,645 条 `S` |
| 4 | 估值只有元到亿元的 `1e8` 单位差，不许股本反算 | ✅ | 4,801,648 行逐行核对，四列最大差 **0.0000000000**，无 null |
| 5 | ST／首板／炸板／生态因子逐键差异报告 | ✅ | 五份 parquet + 摘要 JSON，节点与本地 SHA256 一致 |
| 6 | 清理日志含路径、字节数、SHA256；闸门失败不删 | ✅ | `artifacts/legacy_daily_cleanup_20260915_135735.json`，`status=ready` |

**旧源未清理，是刻意的。** 闸门自身绿灯（78 个文件 1,294,959,239 bytes 待删），
但没有执行，理由见对账报告「清理闸门」一节：闸门起点是 2022-08-04，
证明不了 `paid_daily` 里 1990-2022 的名称真值有替代物；且 D-13／D-14 未闭合前，
旧源是唯一能重算旧语义、复核修正的参照物。

**本轮新登记的缺陷**：D-13（`limit_pct` 与 `limit_up` 自相矛盾，68,649 行）、
D-14（`universe.py` 仍用 `limit_pct == 0.05` 反推 ST，是地雷）、
D-15（`stock_st` 对退市整理期股票单日误报，4 行，影响可忽略）。
见 `docs/reference/deferred-corrections.md`。
