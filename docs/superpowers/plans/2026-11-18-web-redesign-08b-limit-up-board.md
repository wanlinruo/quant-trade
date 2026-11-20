# Web 改版 08b：打板看板（含“昨日涨停今日表现”）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在新前端里做出打板看板 `/app/board`。11-11 设计的题材梯队矩阵、情绪栏、盘中实时 / 盘后定版 / 历史日期与盘中回放全部保留，“今/昨对比”改为“昨日涨停今日表现”。

**Architecture:** 打板采集器（`limit-up-board`）和只读读取器 `LimitUpBoardReader` 已在生产运行，本计划不改采集器，也不改旧页面与旧接口。新增 `/api/app/board`，它包装读取器的视图，并在服务端补上两类数据：赚钱效应和逐层晋级表（纯派生，§9.5 前端不重算），以及经共享缓存的交易时段信息。新增 `/api/app/board/series`，在分时之外补上该股的封板、开板、回封事件。前端复用 08a 的页面组件。

**Tech Stack:** 同计划 01 与 08a。

**Spec:**
- `docs/superpowers/specs/2026-11-17-web-redesign-design.md`（§3、§5.5、§7.3、§8.9、§9、§10、§13.4）
- `docs/superpowers/specs/2026-11-11-limit-up-board-dashboard-design.md`（全部维护者已定口径）

## Global Constraints

- 计划 01、08a 的全部 Global Constraints 继续有效。
- 11-11 设计中维护者已定的口径全部保留，包括：
  - 题材按同花顺概念，每只票只归一列。
  - 涨停家数按“当日封过板”计，ST 计入，北交所不计入。
  - 15 秒精度。
  - 封板未知不计入封板、晋级与首封。
  - 北交所灰化，标“北交所 · 无交易权限”。
- 盘中值一律标“暂计”，盘中首封时间带“≈”（§7.3，11-11 §4.3）。
- 封单额在盘口量单位核实前不展示，与旧页一致。
- 拿不到的值显示“未知”或 `<Missing>`，不补值。昨日连板高度未入库时，整个“昨日涨停今日表现”视图显示原因，不回退。
- AI 晋级列只读影子榜单，常驻“AI 影子 · 非交易信号”；没有预测的票显示“无预测”。
- 前端轮询间隔不短于 15 秒；非交易时段不按 15 秒轮询（§10）。
- 本计划不改 `src/qsys/data/board/**`（采集器）、旧模板 `limit_up_board.html` 与旧接口 `/api/market/limit-up-board*`。

## 设计者决定（执行时写进页面 ⓘ）

以下口径由设计者决定，维护者可以改：

| 项 | 口径 |
|---|---|
| 昨日涨停集合 | 读取器 `compare` 中的全部个股，即昨日连板高度 ≥ 1 的票（非 ST 来自 `limit_list_d`，ST 来自 `daily_flags`） |
| 今日涨跌幅 | 读取器给的 `pct`（实时价 ÷ 昨收 − 1）；为 null 的票不进入平均，单独计数“无报价 N 只” |
| 今日去向 | 晋级＝`promoted`；炸板＝`broken`；跌停＝`limit_down`；封板未知＝`unknown`；其余（`rush`、`missed`）为断板，`pct > 0` 记断板红，`pct ≤ 0` 记断板绿，`pct` 为 null 记“无报价” |
| 大面家数 | 今日涨跌幅 ≤ −5% 的昨日涨停票家数（含跌停） |
| 昨日连板今日平均 | 昨日高度 ≥ 2 的票今日涨跌幅平均 |
| 昨日最高板今日表现 | 昨日最高层的全部个股，逐只列出名称、今日涨跌幅与去向 |
| 晋级率的“昨日对比” | 前一交易日看板最终时刻同一层的晋级率；前一交易日没有看板留存时为 null，显示“无昨日数据” |

## Review Focus

1. **昨日连板高度未入库**（`meta.heights_error` 非空，`compare` 为空）：
   - 视图显示“昨日连板高度不可用：<原因>”，赚钱效应与晋级表不显示 0。
   - 归 Task 1 `test_effect_none_when_heights_unavailable`，以及 Task 5 `compare view explains missing heights`。
2. **部分昨日涨停票没有今日报价**（`pct` 为 null）：
   - 平均值只用有报价的票，并显示“无报价 N 只”，不把 null 当 0。
   - 归 Task 1 `test_effect_ignores_null_pct_and_counts_it`。
3. **回放到某时刻 T**（`?at=`）：
   - 赚钱效应、晋级表、矩阵都按 T 时刻计算，不缓存成最新值。
   - 归 Task 2 `test_replay_is_not_served_from_latest_cache`。
4. **概念快照拉取失败**（`meta.concept_error`）：
   - 矩阵退化为单列，结论条为警告并显示原因，页面不崩溃。
   - 归 Task 3 `board hero warns on concept error`。
5. **非交易时段或回看历史日**：
   - 页面不按 15 秒轮询：历史与定版不轮询，休市时间隔 60 秒。
   - 归 Task 3 `board polls only live mode and backs off when closed`。

---

### Task 1: 赚钱效应与逐层晋级表（纯函数）

**Files:**
- Create: `src/qsys/web/board_effect.py`
- Test: `tests/web/test_board_effect.py`

**Interfaces:**
- Consumes: `CompareGroup`、`CompareItem`（`src/qsys/web/limit_up_board.py`）
- Produces:
  - `money_effect(groups: list[CompareGroup]) -> dict | None`。`groups` 为空时返回 None，否则返回：
    - `total`
    - `priced`：有 pct 的家数
    - `no_quote`
    - `avg_pct`
    - `red_rate`：`pct > 0` 家数 ÷ priced
    - `multi_avg_pct`：level ≥ 2
    - `big_loss`：`pct ≤ -0.05`
    - `top_level`
    - `top_items: [{symbol, name, pct, status}]`
    - 任何分母为 0 的比值或平均为 None。
  - `layer_rows(groups: list[CompareGroup], prev_rates: dict[int, float | None]) -> list[dict]`。每层一行，按 level 降序，字段：
    - `level`、`total`
    - `promoted`、`broken`、`break_up`、`break_down`、`limit_down`、`unknown`、`no_quote`
    - `rate`：promoted ÷ total
    - `prev_rate`：`prev_rates.get(level)`
    - `avg_pct`
  - `layer_rates(rows: list[dict]) -> dict[int, float | None]`：把某天的 `layer_rows` 转成 level → rate，供第二天的“昨日对比”使用。
  - `BIG_LOSS = -0.05`

- [ ] **Step 1: 写失败测试**

```python
def g(level, *items): return CompareGroup(level=level, items=[CompareItem(symbol=s, name=s, pct=p, column=None, status=st, first_seal=None, open_times=None, prediction=None) for s, st, p in items], promoted=sum(st == "promoted" for _, st, _ in items))

def test_effect_basic():
    e = money_effect([g(2, ("A", "promoted", 0.10), ("B", "limit_down", -0.10)), g(1, ("C", "missed", 0.02), ("D", "missed", -0.06))])
    assert e["total"] == 4 and e["priced"] == 4 and e["no_quote"] == 0
    assert e["avg_pct"] == pytest.approx(-0.01) and e["red_rate"] == 0.5
    assert e["multi_avg_pct"] == pytest.approx(0.0) and e["big_loss"] == 2
    assert e["top_level"] == 2 and [i["symbol"] for i in e["top_items"]] == ["A", "B"]

def test_effect_ignores_null_pct_and_counts_it():
    e = money_effect([g(1, ("A", "missed", None), ("B", "missed", 0.04))])
    assert e["priced"] == 1 and e["no_quote"] == 1 and e["avg_pct"] == pytest.approx(0.04) and e["red_rate"] == 1.0

def test_effect_none_when_heights_unavailable():
    assert money_effect([]) is None

def test_layer_rows_split_breaks_by_sign_and_keep_prev_rate():
    rows = layer_rows([g(1, ("A", "promoted", 0.1), ("B", "rush", 0.08), ("C", "missed", 0.0), ("D", "broken", 0.03), ("E", "unknown", 0.1), ("F", "missed", None))], {1: 0.25})
    r = rows[0]
    assert (r["promoted"], r["break_up"], r["break_down"], r["broken"], r["unknown"], r["no_quote"]) == (1, 1, 1, 1, 1, 1)
    assert r["rate"] == pytest.approx(1 / 6) and r["prev_rate"] == 0.25

def test_layer_rows_prev_rate_missing_is_none():
    assert layer_rows([g(3, ("A", "promoted", 0.1))], {})[0]["prev_rate"] is None
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/web/test_board_effect.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行，确认通过**
- [ ] **Step 5: Commit**：`feat(web): 打板看板赚钱效应与逐层晋级表的纯派生（#662）`

---

### Task 2: `/api/app/board` 与 `/api/app/board/series`

**Files:**
- Create: `src/qsys/web/app_board.py`（`register_app_board(app, board, live_parts)`）
- Modify: `src/qsys/web/app_market.py`
  - 把 15 秒的 live 缓存挂到 `app.state.app_live_parts`。
  - market 为 None 时该值为 None。
- Modify: `src/qsys/web/limit_up_board.py`：新增只读方法 `symbol_events(day: date, symbol: str) -> list[TimelineEvent]`。它只读 events 表中该股的 seal、open、reseal 事件，不改动现有方法。
- Modify: `src/qsys/web/app.py`：在 `mount_spa` 之前调用 `register_app_board(app, limit_up_board, app.state.app_live_parts)`。
- Test: `tests/web/test_app_board.py`（复用 `tests/web/test_limit_up_board.py` 的 `build`、`reader`、`shadow` 夹具，按需移到 `tests/web/board_fixtures.py`）

**Interfaces:**
- Consumes:
  - `LimitUpBoardReader.view(day, at) -> BoardView`
  - `.series(day, symbol) -> SeriesView`
  - Task 1 的三个函数
  - 08a 的 `SingleFlightCache`
- Produces:
  - `GET /api/app/board?day=&at=`（`no-store`）返回 `BoardView` 的 JSON，并增加以下字段：
    - `effect`：`money_effect(view.compare)`
    - `layers`：`layer_rows(view.compare, prev_rates)`
    - `prev_day`：`meta.prev_day`
    - `schedule`：来自 `app.state.app_live_parts` 的缓存值，读不到时为 null
    - `provisional`：`mode == "live"`
  - `prev_rates` 的取法：
    - 对 `meta.prev_day` 调 `reader.view(prev_day)` 取得最终时刻的视图，再用 `layer_rates(layer_rows(prev.compare, {}))` 计算。
    - 按 prev_day 缓存 300 秒。
    - prev_day 不在 `days` 中或读取失败时为 `{}`。
  - `day` 与 `at` 都为空（最新视图）时，经 15 秒 `SingleFlightCache`。指定 `day` 或 `at` 的请求不走这个缓存。
  - 读取器为 None 时返回 `{"available": false, "message": "打板看板数据源未配置"}`。
  - `GET /api/app/board/series?day=&symbol=` 返回 `SeriesView` 的 JSON，并增加 `events: [{at, kind, detail}]`（来自 `symbol_events`）。

- [ ] **Step 1: 写失败测试**

```python
def test_app_board_unconfigured(...)                    # available False + 文案
def test_app_board_adds_effect_and_layers(...)          # build() 数据：effect.total == 2（A 昨 1 板、C 昨 2 板），layers 含 level 2 与 1，provisional True
def test_effect_null_when_heights_missing(...)          # build(heights=False) → effect None、layers []、meta.heights_error 原样
def test_latest_view_cached_15s(...)                    # 计数包装 reader.view：两次 GET /api/app/board 只调 1 次（含 prev_day 调用另计）
def test_replay_is_not_served_from_latest_cache(...)    # 先 GET 最新，再 GET ?at=09:31:15 → at 为 09:31:15 且 sentiment 对应该时刻
def test_prev_rate_from_previous_day_board(...)         # 构造 PREV 日也有看板 → layers[*].prev_rate 来自前一日；无前一日 → None
def test_series_includes_seal_open_reseal_events(...)   # A.SH：kind 序列 == ["seal", "open", "reseal"]
def test_board_endpoints_no_store(...)
def test_schedule_from_shared_live_cache_not_per_request(...)  # FakeMarket.schedule 计数：多次 GET /api/app/board + /api/app/market 共 1 次
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/web/test_app_board.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**。以下点要按此实现：
  - 视图对象先 `model_dump(mode="json")`，再合并新增字段。
  - 新增字段读取失败时置 null，并写入 `errors`，不回显异常文本。

- [ ] **Step 4: 运行 `uv run pytest tests/web -q`**
Expected: PASS（旧打板测试不变）

- [ ] **Step 5: Commit**：`feat(web): 新前端打板看板接口，补赚钱效应、逐层晋级与个股事件（#662）`

---

### Task 3: 打板看板页框架（结论条、情绪栏、日期与回放、轮询）

**Files:**
- Create: `frontend/src/api/board.ts`（类型与 `fetchBoard(day, at, signal)`、`fetchBoardSeries(day, symbol, signal)`）
- Create: `frontend/src/pages/board/BoardPage.tsx`、`frontend/src/pages/board/SentimentBar.tsx`、`frontend/src/pages/board/DayControls.tsx`、`frontend/src/pages/board/fixtures.ts`
- Modify: `frontend/src/pages/registry.ts`（`board: BoardPage`）
- Test: `frontend/src/pages/board/BoardPage.test.tsx`

**Interfaces:**
- Consumes: Task 2 的 JSON；08a 的 Hero、Panel、Freshness、usePolling、`useSearch`、`setSearch`
- Produces:
  - `BoardPage`
  - URL 参数：`?day=YYYY-MM-DD&at=HH:MM:SS&view=ladder|compare`，默认 ladder。
  - `BoardData` 类型，供 Task 4 与 Task 5 使用。

**页面结构：**

- **结论条**
  - 标题：`涨停 {sealed} · 跌停 {limit_down} · 最高 {max_height ?? '未知'} 板 · 封板率 {pct(seal_rate)}`。
  - 状态：

    | 情形 | 状态 | 文字 |
    |---|---|---|
    | live 且快照新鲜 | 正常 | “盘中 · 暂计” |
    | live 且最后快照早于当前 45 秒以上，并且 `schedule.is_open` | 警告 | “快照已过期” |
    | final | 正常 | “盘后定版” |
    | final 且 `mismatch_count > 0` | 警告 | “盘后定版 · ≠ 直取 N 处” |
    | history | 休市 | “历史 · 只读” |
    | `available == false` | 待运行 | 后端给的 message |
    | `meta.concept_error` 或 `meta.heights_error` 非空 | 警告 | 在副标题列出原因 |

  - 副标题：交易日、快照时刻（盘中带“≈”）、`message`。
- **情绪栏 9 格**（`SentimentBar`）：
  - 涨停/跌停、触板/炸板、封板率、最高板。
  - 晋级 1→2、2→3、3→4+，显示“今/昨”，任一为 null 时显示“未知”。
  - 冲板中（≥7%）、缺涨停价/封板未知。
- **日期与回放**（`DayControls`）：
  - 交易日下拉取 `days`。
  - `times.length > 1` 且 `mode !== "live"` 时显示回放滑杆，拖动写入 `at`。
  - 回看历史日时显示黄色“正在回看 · 只读”与“回到今天”。
- **轮询**：
  - 只在 `mode === "live"` 且没有 `at` 时轮询，`intervalMs: 15000, minIntervalMs: 15000`。
  - `schedule.is_open === false` 时改为 60000。
  - 其余情况取一次即暂停，`R` 键仍可刷新。
- **主面板**：标题处用 antd `Segmented` 切换“题材梯队 / 昨日涨停今日表现”，内容由 Task 4 与 Task 5 填充（本任务先放占位）。

- [ ] **Step 1: 写失败测试**

```tsx
test('board hero shows counts and provisional tag in live mode', ...)      // 标题含“涨停 2 · 跌停 1”，状态“● 盘中 · 暂计”
test('board hero warns on concept error', ...)                            // meta.concept_error → ▲ 且副标题含原因
test('final mode shows mismatch count', ...)                              // “▲ 盘后定版 · ≠ 直取 3 处”
test('unavailable board shows message', ...)                              // available false → “○ 打板看板采集器尚未产生数据”
test('sentiment null promotions show 未知', ...)
test('board polls only live mode and backs off when closed', ...)         // fake timers：history 模式 60 秒内 fetch 1 次；live+closed 间隔 60 秒
test('replay slider writes at into the url and refetches', ...)
```

- [ ] **Step 2: 运行，确认失败**：`cd frontend && npm test`
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行 `npm test && npm run typecheck && npm run build`**，确认 PASS
- [ ] **Step 5: Commit**：`feat(web): 新前端打板看板框架：结论条、情绪栏、日期回放与轮询（#662）`

---

### Task 4: 题材梯队矩阵与个股分时抽屉

**Files:**
- Create: `frontend/src/pages/board/LadderMatrix.tsx`、`frontend/src/pages/board/BoardCard.tsx`、`frontend/src/pages/board/StockDrawer.tsx`
- Test: `frontend/src/pages/board/LadderMatrix.test.tsx`、`frontend/src/pages/board/StockDrawer.test.tsx`

**Interfaces:**
- Consumes: `BoardData.columns`、`.rows`、`.cards`、`.mode`；`fetchBoardSeries`
- Produces: `<LadderMatrix data onPick(symbol) />`、`<StockDrawer day symbol open onClose />`

**矩阵（与 11-11 §5.3 一致）：**
- 列：`columns` 原顺序。列头：题材名、`sealed_count` 家（null 时不显示数字）、梯度（`gradient`，如“3·2·5”）、首家时间。
- 行：`rows` 原顺序。行头为 `label`；`yesterday` 非 null 时加“昨 {yesterday} →”。
- 矩阵放在面板内横向滚动，页面本身不溢出。

**卡片显示规则（对齐旧页 `chip`）：**

| 情形 | 显示 |
|---|---|
| sealed | `≈hh:mm:ss`（final/history 不带 ≈）、`开 N`；回封时加浅橙底与“回封” |
| broken | 虚线框，“炸 · 现 x%”与 `开 N` |
| touched | 虚线框，“触板未封 · x%” |
| rush | 虚线框，橙色“冲 x%” |
| unknown | 标签“封板未知” |
| `is_st` 为 true | 标签“ST” |
| `is_st` 为 null | 标签“ST未知” |
| `is_bj` | 灰化，标签“北交所 · 无交易权限” |
| final 模式且 `diffs` 非空 | 红字“≠ {字段} {直取值}” |

- 每格最多 6 张卡片，超出部分显示“展开 N 只”，点击后展开。
- 点击卡片调用 `onPick(symbol)`，打开抽屉。

**个股抽屉：**
- 主图：分时按涨跌幅 `price / prev_close − 1` 绘制。这是纯派生，`prev_close` 为 null 时整图显示缺失。
- 涨停价线：`limit_up / prev_close − 1`，虚线。
- 首封、开板、回封用 markPoint 标出，带时间。
- 成交量柱：volume 为 null 的点保留为 null，不画。
- `available == false` 时显示后端 message。
- 抽屉标题：名称、代码、今日高度（未知时写“高度未知”）。

- [ ] **Step 1: 写失败测试**

```tsx
test('matrix renders columns, row labels with yesterday counts and cards', ...)
test('cell folds beyond 6 cards with 展开 N 只', ...)
test('card variants: live ≈ time, final no ≈ and shows ≠ diffs, bj greyed with 无交易权限, st unknown', ...)
test('drawer plots pct series with limit line and event marks, null volume stays null', ...) // 捕获 EChart option
test('drawer shows message when series unavailable', ...)
```

- [ ] **Step 2–4:** 运行 `npm test`，先确认失败；实现后运行 `npm test && npm run typecheck`，确认通过。
- [ ] **Step 5: Commit**：`feat(web): 打板看板题材梯队矩阵与个股分时抽屉（#662）`

---

### Task 5: 昨日涨停今日表现

**Files:**
- Create: `frontend/src/pages/board/CompareView.tsx`、`frontend/src/pages/board/LayerTable.tsx`
- Test: `frontend/src/pages/board/CompareView.test.tsx`

**Interfaces:**
- Consumes: `BoardData.effect`、`.layers`、`.compare`、`.shadow`、`.provisional`、`.meta.heights_error`
- Produces: `<CompareView data onPick />`

**视图结构（§8.9）：**
- **顶部 5 项赚钱效应**：
  - 昨日涨停今日平均：附“有报价 {priced}/{total}，无报价 {no_quote}”。
  - 红盘率、昨日连板今日平均、大面家数。
  - 昨日最高板今日表现：`top_level` 板，逐只“名称 涨跌幅 去向”。
  - `provisional` 时每项标“暂计”。
  - ⓘ 写明“设计者决定”表中的口径。
- **晋级表（LayerTable）**：每层一行。
  - 列：层（“昨 N 板”）、家数、今日去向堆叠条、晋级、炸板、断板红、断板绿、跌停、未知、晋级率与“昨日 x%”、今日平均。
  - 堆叠条用 div 按家数比例画（晋级红、炸板橙、断板红浅红、断板绿浅绿、跌停绿、未知与无报价灰），每段带文字说明，不只靠颜色。
  - `prev_rate` 为 null 时写“无昨日数据”。
- **分列卡片**：按 `compare` 的层降序，每组标题“昨 N 板 → 今 N+1 板 · 晋级 {promoted}/{len}”。
  - 卡片顺序沿用后端（已晋级在前）。
  - 首板组（level 1）默认只显示 promoted、rush 与 unknown，其余折叠为“其余 N 只 展开”。
  - 卡片内容：名称、今日涨跌幅（涨红跌绿）、去向、已晋级时的 `≈首封` 与开板次数、AI 晋级（影子）。
- **AI 晋级（影子）**：
  - 组区顶部常驻“AI 影子 · 非交易信号”，以及模型版本、滚动 P@20、`hits`（仅盘中）。
  - 每只票显示 `prediction` 百分比，null 时显示“无预测”。
  - `shadow` 为 null 或 `state !== "ready"` 时，整列显示 `shadow.message`，不显示数字。
- **高度不可用**：`effect == null` 且 `meta.heights_error` 非空时，整个视图只显示“昨日连板高度不可用：{heights_error}”。

- [ ] **Step 1: 写失败测试**

```tsx
test('effect cards show averages with coverage and provisional tag', ...)
test('layer table shows breakdown, rate and previous-day rate or 无昨日数据', ...)
test('first-board group folds non-promoted items', ...)
test('ai column shows prediction or 无预测, and status text when shadow not ready', ...)
test('compare view explains missing heights', ...)
```

- [ ] **Step 2–4:** 运行 `npm test`，先确认失败；实现后运行 `npm test && npm run typecheck && npm run build`，确认通过。
- [ ] **Step 5: Commit**：`feat(web): 打板看板“昨日涨停今日表现”视图（#662）`

---

### Task 6: 题材曲线、封板时间轴、布局冒烟与本地联调

**Files:**
- Create: `frontend/src/pages/board/SidePanels.tsx`
  - **“哪些题材在发酵？”**：取 `curves`，前 4 个题材的累计涨停家数用阶梯线展示，不画未来。
  - **“封板时间轴”**：取 `timeline`，显示时间、名称、事件（首封“第 N 家”、开板、回封、改列），点击名称调用 `onPick`。
- Modify: `frontend/src/pages/board/BoardPage.tsx`：宽屏（≥1280）时主视图在左、两个侧栏面板在右；窄屏时上下排列。
- Create: `frontend/e2e/fixtures/board.json`、`frontend/e2e/fixtures/board-series.json`（形状来自后端模型，数值为测试用）
- Modify: `frontend/e2e/pages.spec.ts`：加入 `/app/board`（两种视图）在 390/1280/1600 下无整页横向溢出，以及抽屉打开。
- Test: `frontend/src/pages/board/SidePanels.test.tsx`

- [ ] **Step 1: 写失败测试**：`curves keep step shape and clip future`、`timeline click opens drawer`
- [ ] **Step 2–4:** 运行 `npm test`，先确认失败，实现后确认通过；然后运行 `npm run build && npx playwright test`，确认全部通过，并做变异检查（加 2000px 元素，确认 390 用例失败后撤销）。
- [ ] **Step 5: 本地联调**：用只读预览（空状态目录）打开 `/app/board`，结论条显示“打板看板数据源未配置”或采集器无数据的 message，页面不报错。
- [ ] **Step 6: Commit 并推送需求分支**：`test(web): 打板看板布局冒烟（#662）`

---

## 完成后

- 在 #662 评论记录：完成情况、裁决，以及“设计者决定”表中的口径，请维护者确认。
- 旧接口 `/api/market/limit-up-board` 每次请求都直接读 Redis 的 `schedule`，不符合 §10。旧页面会在改版合入后下线，所以本计划不改它；在 #662 记录这一点，合入时一并处理。
- 本计划不涉及任何生产组件或控制面改动，留在 `feat/web-redesign`。
