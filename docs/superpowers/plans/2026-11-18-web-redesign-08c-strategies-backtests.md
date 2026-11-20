# Web 改版 08c：策略、回测与研究任务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在新前端里做出“策略”（`/app/strategies`）和“回测”（`/app/backtest`，含“研究任务”子标签）两页。

**Architecture:**
- 新增只读接口 `/api/app/strategies*`、`/api/app/backtests*`、`/api/app/research-jobs`，派生计算都在后端，前端不重算（§9.5）：
  - 策略推进位置：回测 → 模拟 → 实盘。
  - 回测警告、水下回撤段、对齐基准的收益序列。
- 写操作沿用现有接口：新建或导入策略、新建或取消回测、提交或取消研究任务。
- 只新增两个写入口：
  - 带“使用中只读”保护的策略保存。
  - 研究任务“重新提交”（产生新任务 ID，原记录保留）。
- 旧页面与旧接口不改。

**Tech Stack:** 同计划 01、08a、08b。源码编辑使用等宽 `textarea`，不引入编辑器依赖。

**Spec:** `docs/superpowers/specs/2026-11-17-web-redesign-design.md`（§6.3、§8.10、§8.11、§9）

## Global Constraints

- 计划 01、08a、08b 的全部 Global Constraints 继续有效。
- 本页只碰研究侧，Web 不改任何实盘状态。所有写操作都有确认框，写明影响范围，并注明“不影响实盘”（§6.3）。
- 实盘或模拟正在使用的策略版本只读，修改须另存为新策略（§6.3、§8.10）。
- 回测指标在策略页与回测页同源，都取 `/api/app/backtests` 的同一行（§9.5）。
- 数据铁律：
  - 基准缺某日时，该日基准与超额为 null，图上断开。
  - `daily_return` 缺失不当 0。
  - 研究任务没有进度信息时显示缺失，不推算。

## 设计者决定（执行时写进页面 ⓘ）

**推进位置**按以下顺序判断，命中即停：
1. 部署配置承袭（`shadow_lineage()`）的策略 → “{core_label}”（影子盘或实盘）。
2. 有未结束模拟账户（`PaperStore.ACTIVE`：scheduled、running、paused、error）→ “模拟”。
3. 有成功回测 → “回测”。
4. 以上都没有 → “草稿”。

**“使用中”的判定：** 策略当前草稿的版本号（`sha256(f"{id}:" + draft)[:16]`，与 `WorkbenchStore.enqueue` 同一算法）等于以下任一版本时，该草稿为使用中：
- 承袭版本；
- 任一未结束模拟账户的版本。

使用中的草稿只读，原地保存返回 409。

**回测警告**（顶部提示），逐条列出：
- `rejects > 0`：下单被拒 N 笔（详见 rejects.csv）。
- `benchmark_nav.csv` 缺失：没有基准净值，超额收益不可用。
- 净值日期中缺基准的天数 > 0：N 个交易日缺基准，图上断开。
- `daily_nav.csv` 的 `daily_return` 除首日外有 null：N 日缺日收益。
- `cost_model` 为“聚宽兼容”：按聚宽兼容费用计算，不代表实盘档位。
- 任务状态为 `interrupted`：回测被中断，结果不完整。

**水下回撤段：**
- 从前高开始，经谷底，到收复前高（未收复时为 null）为一段。
- 按深度取最深 5 段。

## Review Focus

1. **试图原地保存模拟/实盘正在用的策略**：
   - 后端返回 409 并提示另存。
   - 前端编辑区只读，只给“另存为新策略”。
   - 归 Task 2 `test_save_in_use_strategy_is_rejected`，以及 Task 3 `in-use strategy is read-only and offers save-as`。
2. **基准净值缺某些日期**：
   - 序列中该日 benchmark 与 excess 为 null，警告列出缺失天数，图上断开。
   - 归 Task 1 `test_warnings_list_missing_benchmark_days`，以及 Task 2 `test_series_keeps_missing_benchmark_as_null`。
3. **回测还在排队或运行**：
   - 选择器显示进度（progress/total、当前交易日）。
   - 详情显示“运行中”与日志末尾，不报错。
   - 运行中每 5 秒刷新，完成后停止刷新。
   - 归 Task 4 `running backtest shows progress and polls until done`。
4. **研究任务失败后重新提交**：
   - 新任务 ID，参数与原任务相同。
   - 原失败记录保留。
   - 提交关闭时（`submission_enabled` 为 false）不给按钮，接口返回 403。
   - 归 Task 2 `test_resubmit_creates_new_job_and_keeps_failed`。
5. **研究任务没有进度信息**：
   - 进度列显示缺失，原因为“研究任务运行器不报告进度”，不显示 0%。
   - 归 Task 5 `research job progress shows 缺失`。

---

### Task 1: 回测分析与策略推进的纯函数

**Files:**
- Create: `src/qsys/web/research_views.py`
- Modify: `src/qsys/web/workbench.py`：抽出 `version_id_for(strategy_id: str, source: str) -> str`，`enqueue` 改为调用它，行为不变。
- Test: `tests/web/test_research_views.py`

**Interfaces:**
- Produces:
  - `version_id_for(strategy_id, source) -> str`（`workbench.py`）
  - `drawdown_episodes(nav: list[tuple[str, float]], top: int = 5) -> list[dict]`
    - 每段 `{peak_date, trough_date, recovery_date | None, depth}`，`depth` 为负数。
    - 按 depth 升序（最深在前），最多返回 top 段。
  - `backtest_warnings(*, rejects: int, has_benchmark: bool, missing_benchmark_days: int, missing_daily_returns: int, cost_model: str, status: str | None) -> list[str]`：文案见“设计者决定”。
  - `aligned_series(nav: list[dict], benchmark: dict[str, float]) -> list[dict]`
    - 每日 `{date, strategy, benchmark | None, excess | None, drawdown}`。
    - strategy、benchmark 都是相对首日的累计收益。
    - 基准首日取基准序列第一个值。
  - `strategy_stage(strategy_id, *, succeeded_strategy_ids: set[str], active_accounts: list[PaperAccount], lineage, core_label: str) -> dict`
    - 返回 `{stage: "live"|"paper"|"backtest"|"draft", label, detail}`。
    - live 的 label 为 core_label。
    - paper 的 detail 为账户名列表。
  - `in_use_versions(active_accounts, lineage) -> dict[str, list[str]]`：版本号 → 使用者说明（如 `"影子盘"`、`"模拟账户 A"`）。

- [ ] **Step 1: 写失败测试**

```python
def test_version_id_matches_enqueue(tmp_path): ...                 # enqueue 后 job.version_id == version_id_for(id, draft)
def test_drawdown_episodes_top5_and_unrecovered():
    nav = [("d1",100),("d2",90),("d3",100),("d4",80),("d5",85)]
    eps = drawdown_episodes(nav)
    assert eps[0] == {"peak_date":"d3","trough_date":"d4","recovery_date":None,"depth":pytest.approx(-0.2)}
    assert eps[1]["recovery_date"] == "d3" and eps[1]["depth"] == pytest.approx(-0.1)
def test_drawdown_episodes_limit():                                 # 7 段 → 只返回 5 段且按深度排序
def test_warnings_list_missing_benchmark_days():                    # missing_benchmark_days=3 → 含“3 个交易日缺基准”
def test_warnings_empty_when_clean():                               # 全部正常 → []
def test_aligned_series_keeps_missing_benchmark_as_null():          # 第 2 天基准缺 → benchmark/excess None，strategy 有值
def test_stage_priority_live_over_paper_over_backtest():
def test_in_use_versions_from_lineage_and_active_accounts():        # ended 账户不计入
```

- [ ] **Step 2: 运行，确认失败**：`uv run pytest tests/web/test_research_views.py -q`
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行通过，并运行 `uv run pytest tests/web -q` 确认工作台旧测试不变**
- [ ] **Step 5: Commit**：`feat(web): 回测分析与策略推进位置的纯派生（#662）`

---

### Task 2: 新前端研究侧接口

**Files:**
- Create: `src/qsys/web/app_research.py`（`register_app_research(app, *, workbench, backtests, paper, research_jobs, strategy_templates, shadow_lineage, core_label)`）
- Modify: `src/qsys/web/app.py`：在 `mount_spa` 之前注册，复用 `_backtest_rows`。
- Test: `tests/web/test_app_research.py`

**Interfaces:**
- Consumes: Task 1 全部函数；`WorkbenchStore`、`BacktestService`、`PaperStore.accounts()`、`ResearchJobConsole`。
- Produces（GET 一律 `no-store`；依赖的服务为 None 时返回 `{"enabled": false}`）：

**GET 接口**

- `GET /api/app/strategies`，返回：
  - `enabled`、`core_label`、`templates: [文件名]`
  - `strategies: [{id, name, updated_at, version_id, stage, stage_label, stage_detail, in_use, used_by, latest_backtest}]`
  - `latest_backtest` 为该策略最近一次 succeeded 回测在 `/api/app/backtests` 中的同一行，没有时为 null。
- `GET /api/app/strategies/{id}`，返回：
  - 上一项的字段，加 `source`、`parameters`（ParameterSpec 字典）、`versions`
  - `backtests`：该策略的回测行，取 `_backtest_rows` 中 `strategy_id` 匹配的行。
  - 不存在时返回 404。
- `GET /api/app/backtests`，返回：
  - `enabled`
  - `rows`：`_backtest_rows` 原样，按 created_at 降序。
  - 每行再加 `progress`、`total`、`current_session`（来自 job，没有 job 时为 null）。
- `GET /api/app/backtests/{name}`：
  - 有报告时返回：
    - `{report: true, summary, metrics, series, episodes, monthly, fills, fills_total, rejects, warnings, strategy_id, job}`。
    - `summary`：RunSummary 字段加 `return_pct`。
    - `metrics`：`metric_map`。
    - `series`：`aligned_series`。
    - `episodes`：`drawdown_episodes`。
    - `monthly`：monthly_returns.csv 原样。
    - `fills`：fills.csv 最后 500 行。
    - `fills_total`：fills.csv 行数。
    - `rejects`：rejects.csv 最后 200 行。
  - 只有排队或运行中的 job 时返回 `{report: false, job: {...status, progress, total, percent, current_session, error}, log_tail}`，`log_tail` 为日志最后 4000 字符。
  - 都没有时返回 404。
- `GET /api/app/research-jobs`，返回：
  - `enabled`、`submission_enabled`
  - `jobs: [{id, state, detail, task, parameters, timeout_seconds, created_at, code_sha, snapshot_id, snapshot_content_sha256, artifacts: [{path, bytes, sha256}]}]`

**写接口**

- `PUT /api/app/strategies/{id}`，body 为 `{name, source}`：
  - 当前草稿使用中时返回 409，detail 为“该版本正被{使用者}使用，只读；请另存为新策略”。
  - 否则调用 `workbench.save_strategy`；语法错误时返回 422。
- `POST /api/app/research-jobs/{id}/resubmit`：
  - 只允许原任务为 failed、cancelled 或 timeout 等终止且非成功的状态，其他状态返回 409。
  - 用原 `task`、`parameters`、`timeout_seconds` 调用 `research_jobs.submit`，返回 `{id: 新ID}`。
  - 提交未启用时返回 403。

- [ ] **Step 1: 写失败测试**

```python
def test_strategies_list_stage_and_latest_backtest(...)      # 一条 succeeded 回测 → stage "backtest"，latest_backtest.return_pct 与 /api/app/backtests 行一致
def test_strategy_stage_paper_and_live(...)                  # 建模拟账户 → "模拟"；lineage 指向 → core_label
def test_save_in_use_strategy_is_rejected(...)               # 模拟账户使用当前草稿版本 → PUT 409 且草稿未变；改用未使用策略 → 200
def test_backtest_detail_report(...)                         # 用 BacktestService 夹具目录：series/episodes/monthly/fills/warnings 字段齐全
def test_series_keeps_missing_benchmark_as_null(...)         # benchmark_nav 少一天 → 该日 benchmark None、warnings 含缺基准
def test_backtest_detail_running_job(...)                    # 只有 queued job → report False，job.status "queued"
def test_research_jobs_list_and_resubmit_creates_new_job_and_keeps_failed(...)
def test_resubmit_rejected_when_submission_disabled_or_job_active(...)  # 403 / 409
def test_endpoints_disabled_without_services(...)            # {"enabled": False}
```

回测与研究任务的夹具优先复用 `tests/web/` 中已有的测试数据（执行时查找 `test_backtest*`、`test_research_jobs*`）。

- [ ] **Step 2: 运行，确认失败**：`uv run pytest tests/web/test_app_research.py -q`
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行 `uv run pytest tests/web -q`**，确认 PASS
- [ ] **Step 5: Commit**：`feat(web): 新前端策略、回测与研究任务接口（#662）`

---

### Task 3: 策略页

**Files:**
- Create: `frontend/src/api/research.ts`（类型；`fetchStrategies`、`fetchStrategy`、`saveStrategy`、`createStrategy`、`importTemplate`、`fetchBacktests`、`fetchBacktest`、`createBacktest`、`cancelBacktest`、`fetchResearchJobs`、`submitResearchJob`、`cancelResearchJob`、`resubmitResearchJob`）
  - 写操作失败时抛 Error，message 取后端 `detail`。
- Create: `frontend/src/page/ConfirmAction.tsx`
  - 签名：`confirmAction({title, impact, onOk})`。
  - 封装 antd `Modal.confirm`。
  - 正文固定含 `impact` 与“不影响实盘”。
- Create: `frontend/src/pages/strategies/StrategiesPage.tsx`、`StrategyDetail.tsx`、`NewBacktestDialog.tsx`
- Modify: `frontend/src/pages/registry.ts`（`strategies`）
- Test: `frontend/src/pages/strategies/StrategiesPage.test.tsx`

**页面结构：**

- **结论条**：
  - 标题“共 N 个策略 · 实盘/影子 a · 模拟 b · 回测 c · 草稿 d”。
  - 状态：有策略为正常，没有策略为待运行。
- **“有哪些策略、走到哪一步？”** 表格：
  - 列：名称、版本号（前 8 位）、推进位置（四段进度：草稿 → 回测 → 模拟 → {core_label}，当前段高亮）、最近回测收益/最大回撤（取 `latest_backtest`，null 显示“无回测”）、更新时间。
  - 点击行写入 `?id=`，打开详情。
- **详情**（同页下方面板）：
  - 源码：等宽 textarea。使用中时只读，并显示“该版本正被 X 使用，只读”。
  - 参数表：名称、类型、默认值、范围、说明。
  - 版本列表。
  - 回测记录：点击跳到 `/app/backtest?id=`。
  - 按钮（每个都经 `confirmAction`）：
    - 未使用中时有“保存”。
    - 始终有“另存为新策略”，名称默认“{原名} 副本”。
    - “新建回测”：打开对话框，填起止日期、初始资金、参数（按 ParameterSpec 生成输入框）、竞价入场口径 `open | continuous`、聚宽兼容勾选，然后调用 `POST /api/backtests`，成功后跳到 `/app/backtest?id=`。
- **页头操作**：“新建策略”（名称 + 空源码模板）、“从模板导入”（下拉选 `templates`）。

- [ ] **Step 1: 写失败测试**

```tsx
test('strategy table shows stage steps and latest backtest from the shared row', ...)
test('in-use strategy is read-only and offers save-as', ...)   // textarea readOnly，没有“保存”，有“另存为新策略”
test('saving asks for confirmation mentioning 不影响实盘 and surfaces 409 detail', ...)
test('new backtest dialog builds params from spec and posts', ...) // 断言 fetch body
test('workbench disabled shows message', ...)
```

- [ ] **Step 2–4:** 先运行 `npm test` 确认失败；实现后运行 `npm test && npm run typecheck && npm run build`，确认通过。
- [ ] **Step 5: Commit**：`feat(web): 新前端策略页（#662）`

---

### Task 4: 回测页（报告与选择器）

**Files:**
- Create: `frontend/src/pages/backtest/BacktestPage.tsx`（子标签：“回测”与“研究任务”，写入 `?tab=`）
- Create: `frontend/src/pages/backtest/BacktestPicker.tsx`、`BacktestReport.tsx`、`MonthlyHeatmap.tsx`
- Modify: `frontend/src/pages/registry.ts`（`backtest`）
- Test: `frontend/src/pages/backtest/BacktestPage.test.tsx`

**页面结构（§8.11）：**

- **选择器**：
  - antd Select，可搜索（策略名、ID、区间）。
  - 选项显示状态、区间、收益。
  - 选中写入 `?id=`；默认选最近一条有报告的回测。
- **有警告时**：顶部黄色 Alert，逐条列出 `warnings`。
- **统计横幅（Hero KPI）**：区间收益、基准收益、超额、最大回撤、交易笔数（买/卖）、被拒笔数、费用模型、入场口径。缺的指标显示缺失。
- **“跑赢基准了吗？”**：累计收益 vs 基准与超额三条线，null 断开。
- **“回撤有多深？”**：
  - 水下回撤面积图。
  - 用 markArea 标出最深 5 段，并列出段表：前高、谷底、收复（未收复显示“未收复”）、深度。
- **“每月表现如何？”**：月度收益热力图，行为年、列为 1–12 月，用 div 网格实现，涨红跌绿，空月份留空。
- **“交易明细”**：fills 表格，分页，显示“共 {fills_total} 笔，显示最后 500 笔”。被拒单为单独一个折叠面板。
- **报告文件**：链接到旧下载路由 `/backtests/{name}/file/{filename}`。
- **运行中**：
  - 显示进度条（progress/total）、当前交易日、日志末尾。
  - 有“取消回测”按钮（经确认）。
  - `usePolling(intervalMs 5000, enabled: 运行中)`，完成后停止。

- [ ] **Step 1: 写失败测试**

```tsx
test('report shows kpis, warnings and charts with null benchmark gaps', ...)
test('underwater chart marks the 5 deepest episodes and lists them', ...)
test('monthly heatmap lays out years by months', ...)
test('running backtest shows progress and polls until done', ...)   // fake timers：queued→running→report
test('picker defaults to latest report and writes id to url', ...)
```

- [ ] **Step 2–4:** 先运行 `npm test` 确认失败；实现后运行 `npm test && npm run typecheck && npm run build`，确认通过。
- [ ] **Step 5: Commit**：`feat(web): 新前端回测页（#662）`

---

### Task 5: 研究任务子标签

**Files:**
- Create: `frontend/src/pages/backtest/ResearchJobs.tsx`
- Test: `frontend/src/pages/backtest/ResearchJobs.test.tsx`

**结构：**

- **任务表**：
  - 列：ID 前 8 位、任务、状态（StatusTag 映射：succeeded → 正常；failed 或 timeout → 失败；cancelled → 休市样式“已取消”；queued、claimed、running → 运行中）、进度、提交时间、代码与快照哈希（前 12 位，悬停看全文）。
  - 进度：运行器不报告进度，统一显示 `<Missing why="研究任务运行器不报告进度">`。
  - 展开行：detail、参数 JSON、产物文件链接（旧路由 `/research-jobs/{id}/file/{path}`）。
- **操作**（都经 `confirmAction`）：
  - 运行中可“取消”。
  - 失败、超时或已取消可“重新提交”，提示“将以相同参数创建新任务，原记录保留”。
  - 页头“提交研究任务”（任务名 + 参数 JSON + 超时秒数，JSON 无效时前端拦截）。
  - `submission_enabled` 为 false 时以上写按钮都不显示，并说明“本节点未开启网页提交”。
- **轮询**：有运行中任务时每 15 秒刷新，否则不轮询。

- [ ] **Step 1: 写失败测试**

```tsx
test('research job progress shows 缺失', ...)
test('failed job can be resubmitted after confirmation and list refreshes', ...)
test('submission disabled hides write actions', ...)
test('invalid parameter json is rejected before submit', ...)
```

- [ ] **Step 2–4:** 先运行 `npm test` 确认失败；实现后运行 `npm test && npm run typecheck && npm run build`，确认通过。
- [ ] **Step 5: Commit**：`feat(web): 回测页研究任务子标签（#662）`

---

### Task 6: 布局冒烟与本地联调

**Files:**
- Create: `frontend/e2e/fixtures/strategies.json`、`strategy.json`、`backtests.json`、`backtest.json`、`research-jobs.json`（形状取自 Task 2 接口的真实输出，数值为测试用）
- Modify: `frontend/e2e/pages.spec.ts`

- [ ] **Step 1: 写用例**：`/app/strategies?id=`、`/app/backtest?id=`、`/app/backtest?tab=research` 在 390/1280/1600 下无整页横向溢出；回测页图表 canvas 可见。
- [ ] **Step 2: 运行与变异检查**：运行 `npm run build && npx playwright test`，确认全部通过；在回测页临时加 2000px 元素，确认 390 用例失败后撤销。
- [ ] **Step 3: 本地联调**：只读预览（空工作台）打开两页，显示“未启用”或空状态，不报错。
- [ ] **Step 4: Commit 并推送**：`test(web): 策略与回测页布局冒烟（#662）`

---

## 完成后

- 在 #662 评论记录：完成情况、裁决与“设计者决定”中的口径。
- 旧接口 `PUT /api/strategies/{id}` 没有“使用中只读”保护。旧页面在改版合入后下线，在 #662 记录，合入时一并处理。
- 旧报告页 `BacktestService.chart_series` 把缺失的 `daily_return` 填成 0（`float(daily_return or 0.0)`），不符合数据铁律。新接口不使用它；在 #662 记录，旧页下线时一并移除。
- 本计划不涉及任何生产组件或控制面改动，留在 `feat/web-redesign`。
