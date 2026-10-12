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

日常评分只加载已通过 #210 门禁的保留目录，并从权威交易日历连接 T+1：

```bash
.venv/bin/python scripts/run_limit_up_shadow.py \
  --warehouse /data/qsys/warehouse \
  --research-run /data/qsys/research/issue-210/run-20261010 \
  --state /data/qsys/var-prod/limit-up-shadow.sqlite3 \
  --drift-reference /data/qsys/var-prod/limit-up-drift-reference.json \
  --config /path/to/deploy.toml
```

任务会核对 `verdict.json` 的晋级结论、`manifest.json` 的完整源码/数据指纹和当前
`FEATURE_COLUMNS`，然后仅在内存中计算当日合格股票及 Top-20。模型返回值必须继续满足
两阶段概率恒等式。证券状态真值缺失的标的直接排除；合格股票少于默认完整性下限
3,000 只时整次发布失败。相同预测日已有最终快照时幂等跳过，不覆盖历史。

LightGBM 与 scikit-learn 是生产运行依赖；模型仍来自只读的研究证据目录，不复制出第二份
模型或训练集。

## 漂移与自动调度

漂移采用全体合格标的 `P(收盘封板)` 相对 #210 OBS LightGBM 冻结分布的 Population
Stability Index（PSI）。基线只含固定分桶占比、样本数和 `predictions.parquet` SHA，不含
证券代码或逐标的预测：

```bash
.venv/bin/python scripts/build_limit_up_drift_reference.py \
  --predictions /data/qsys/research/issue-210/run-20261010/predictions.parquet \
  --out /data/qsys/var-prod/limit-up-drift-reference.json
```

输出文件不可覆盖；来源变化时必须人工审阅并生成新基线。日榜仅保存 PSI 单值。`<0.10`
视为稳定，`0.10–0.25` 需关注，`>0.25` 表示显著漂移；它只用于观测，绝不自动调参、换模
或触发交易。

`qsys-limit-up-shadow.timer` 在交易日 19:35 触发。任务持有仓库共享锁，因此会等待仍在
执行的 Tushare 独占写锁释放，然后依次回填到期结果和发布当日榜单。两步共同写入
`limit-up-shadow` 的 `ServiceRun`，可在系统服务管理页检查成功、失败和耗时；单次最长等待
两小时。首次启用前必须先生成上面的基线文件。

目标交易日收盘真值就绪后，结果观察任务补写独立、不可变的事实表：

```bash
.venv/bin/python scripts/observe_limit_up_shadow.py \
  --warehouse /data/qsys/warehouse \
  --state /data/qsys/var-prod/limit-up-shadow.sqlite3
```

任务仅处理目标日已进入 `daily_flags` 的待观察榜单。缺少整行行情、标的集合不完整或事实
逻辑矛盾时整日拒绝写入；若只有权威 `is_limit_up` 仍为空，则先不可变地记录真值完整的
标的，将未知标的保留为待确认并在后续周期重试。未知值不得按“未封板”计数，观察日只有
在原 Top-20 全部补齐后才进入最近 60 个完整观察日的滚动 Precision@20 和 Brier 校准误差。
预测快照本身保持不变，已写入事实精确重跑幂等且不得改写。

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
训练集或逐标的全量预测。正常日常评分不生成中间文件；人工 JSON 发布路径产生的临时
JSON、日志和试验目录须在发布校验完成后清理。正式 SQLite 位于已登记为永久运行状态的
`var-prod`（测试环境位于对应 `var`），随现有备份策略管理。研究模型直接引用 #210 的
唯一保留证据，不另行复制。基线构建、测试环境首跑产生的临时 JSON、日志或试验目录在
校验完成后删除；只保留登记过的 SQLite、PSI 基线与 `ServiceRun`。

## 冻结研究回测页面

`/market/limit-up-research`（API：`/api/market/limit-up-research?day=YYYY-MM-DD`）
单独展示 #210 的 TEST/OBS 样本外历史。它只读取保留目录中的 `top20.csv`、
`manifest.json`、`verdict.json`、`metrics.csv` 与 `monthly_precision.csv`，不读取 659 MB
训练集或 84 MB 全量预测，也不把研究样本写入影子 SQLite。

Reader 只接受已经通过冻结晋级门禁的 LightGBM 结果，并校验 126 个 TEST 日、181 个 OBS
日、每日完整 Top-20、概率恒等式和实际结果逻辑。任一证据缺失或不一致时页面 fail closed。
研究页的日/月 Precision 仅描述离线研究，不进入生产影子滚动 Precision、校准或漂移观测。
研究产物本身只保存证券代码；页面展示名称时按预测日优先匹配规范
`security_name_history` 有效区间，确无历史区间时才回退 `security_master` 当前名称。名称只作
只读展示，不写回冻结 CSV，也不改变任何研究概率、排名或结果。
