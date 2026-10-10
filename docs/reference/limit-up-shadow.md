# AI 涨停概率只读影子榜单

Issue #212 的页面入口是 `/market/limit-up-shadow`，只读 API 是
`/api/market/limit-up-shadow?day=YYYY-MM-DD`。行情总览 `/market` 仅展示最新状态和
前三名摘要。

## 安全边界

- 快照存储独立于策略、账户、订单和风控状态库；默认路径为
  `var/limit-up-shadow.sqlite3`。
- Web 进程始终以 SQLite `mode=ro` 和 `query_only=ON` 读取，不建表、不补数据、不更新
  结果。数据库缺失、损坏、任务失败或最新目标交易日已过去时，API 的 `available` 均为
  `false`。
- `P(收盘封板) = P(触板) × P(触板后封板)` 在发布入口强校验，最终状态的每日快照不可
  覆盖。页面没有任何下单动作。

## 离线发布契约

离线任务先输出一份紧凑 JSON，再通过校验入口发布：

```bash
.venv/bin/python scripts/publish_limit_up_shadow.py \
  --input /path/to/snapshot.json \
  --state /data/qsys/var-prod/limit-up-shadow.sqlite3
```

JSON 顶层包含 `prediction_date`、`target_session`、带时区的 `generated_at`、`status`、
模型和数据版本/SHA、合格标的数、观测指标与 `entries`。`ready` 必须有连续排名且最多
20 条；`waiting`、`generating`、`failed` 不得带榜单，其中 `failed` 必须写明错误。
最终 `ready`/`failed` 快照不可覆盖，避免事后改写历史。

实际触板、收盘封板、涨停开盘和一字板结果可随历史快照提供；页面同时展示滚动
Precision@20、校准误差与漂移分数，缺失时明确显示“待累计”。

## 数据卫生与保留

SQLite 仅保存版本化的每日 Top-20、运行状态和必要观测指标，不保存全市场特征矩阵、
训练集或逐标的全量预测。离线生成时的临时 JSON、特征文件、日志和试验目录在发布校验
完成后清理；正式 SQLite 位于已登记为永久运行状态的 `var-prod`（测试环境位于对应
`var`），随现有备份策略管理。研究原始证据仍按 #210 的独立保留记录执行。
