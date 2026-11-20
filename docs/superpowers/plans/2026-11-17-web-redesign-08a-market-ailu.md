# Web 改版 08a：页面组件 + 市场总览 + AI 涨停 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在新前端里做出第一批真实页面（市场总览、AI 涨停）。同时补齐后续所有页面都要用的通用组件，包括结论条、面板、图表、轮询和数据新鲜度。

**Architecture:** 新页面专用的只读接口统一放在 `/api/app/...` 下，旧页面接口保持不变。读 Redis 的接口经过服务端单飞缓存，满足设计文档 §10 的读取边界。前端各页面共用 `src/page/` 下的组件，缺失、缺口、不画未来三条规则在工具函数层统一实现，并单独测试。

**Tech Stack:** 同计划 01（React 19、antd 6、ECharts 6、Vitest、Playwright；FastAPI、pytest）。

**Spec:** `docs/superpowers/specs/2026-11-17-web-redesign-design.md`（§6、§7.3、§8.8、§8.12、§9、§10）

**拆分说明：** 原路线中的计划 08（行情与研究组 5 页）拆成三份：
- 08a：本计划，页面组件、市场总览、AI 涨停。
- 08b：打板看板，包括“昨日涨停今日表现”。
- 08c：策略、回测与研究任务。

后两份在本计划落地后再写。整个改版在长期分支 `feat/web-redesign` 上开发，开发期间不合入 test 或 main（见计划 01 的分支策略）。

## Global Constraints

- 计划 01 的全部 Global Constraints 继续有效，包括：只在 CI 构建前端、涨红跌绿、状态符号加文字。
- 数据铁律：页面只展示直取值或纯派生值，派生口径写在 ⓘ 中。
- 拿不到的值显示 `<Missing why=...>`，不显示 0、`—` 或推算值。
- 图表遇到 null 必须断开（ECharts `connectNulls: false`，数据点保留 null），不能把缺口两侧连成线。
- 图表与时间线不出现当前时刻之后的数据。
- 同一个事实只从一个接口取，前端不各自重算（§9.5）。
- 新接口一律只读（GET），响应头 `Cache-Control: no-store`，不写任何业务状态。
- 读 Redis 的新接口必须经过服务端缓存。缓存时长不短于数据发布间隔：行情快照 15 秒（§10，#666）。前端轮询间隔不短于这个时长。
- AI 涨停页顶部常驻说明：“不是交易信号，不进入实盘或模拟决策。”
- 原型（`docs/superpowers/specs/2026-11-17-web-redesign-prototype/`）只作视觉参考，示意数据生成逻辑不得进入正式代码。

## Review Focus

1. 行情快照不可用（未配置 Redis 或键不存在）：结论条显示“无实时快照”（待运行灰），近 60 日趋势照常显示。归 Task 3，用例 `market page renders history without live snapshot`。
2. 旧生产者没有覆盖信息（`coverage` 为 null），或某市场 `expected` 为 null：覆盖表显示“未绑定覆盖信息”或“未知”，不显示 0。归 Task 3，用例 `coverage null shows unbound, never zeros`。
3. 近 60 日数据某天字段为 null（如 `index_ma20_slope_5d`）：该序列在那一天断开。归 Task 1 `toSeries keeps nulls` 与 Task 2 `test_regime_rows_keep_nulls`。
4. 影子榜单状态为 failed、generating 或 stale：结论条按 §7 着色并显示后端给的 message。failed 不显示榜单，stale 显示“已过期”并保留最近一份。归 Task 4，用例 `ailu hero maps every view state`。
5. 行情控制台未启用（market 为 None）：新接口返回 `{"enabled": false}` 而不是 404，页面显示“行情未启用”，不崩溃。归 Task 2 `test_app_market_disabled` 与 Task 3。

---

### Task 1: 页面通用组件与数据规则工具

**Files:**
- Create: `frontend/src/page/series.ts`（`toSeries`、`clipFuture`）
- Create: `frontend/src/page/format.ts`（`pct`、`signedPct`、`yi`、`int`；入参为 null 时返回 null）
- Create: `frontend/src/page/usePolling.ts`
- Create: `frontend/src/page/Hero.tsx`、`frontend/src/page/Panel.tsx`、`frontend/src/page/EChart.tsx`、`frontend/src/page/Freshness.tsx`、`frontend/src/page/Value.tsx`
- Create: `frontend/src/shell/refresh.ts`（页面刷新注册，供 `R` 快捷键调用）
- Modify: `frontend/src/shell/AppShell.tsx`（`R` 同时刷新顶栏与当前页面）
- Test: `frontend/src/page/series.test.ts`、`frontend/src/page/format.test.ts`、`frontend/src/page/usePolling.test.tsx`、`frontend/src/page/Value.test.tsx`

**Interfaces:**
- Produces:
  - `toSeries<T>(rows: T[], x: (r: T) => string, y: (r: T) => number | null | undefined): [string, number | null][]`：保持行序，`undefined` 和 `NaN` 一律转为 `null`，不丢点。
  - `clipFuture(points: [string, number | null][], now: Date, toDate: (x: string) => Date): [string, number | null][]`：去掉晚于 `now` 的点。
  - `pct(v: number | null, digits = 1): string | null`：`0.623 → "62.3%"`；`signedPct(v)`：`+1.23%` 或 `-0.50%`；`yi(v)`：元转亿元，`"1234 亿"`；`int(v)`：千分位。
  - `<Value v={string | number | null} why="缺失原因" color? />`：值为 null 时渲染 `<Missing why>`，否则渲染 `<Num>`。
  - `<Hero s={Status} title subtitle kpis={{k, v: ReactNode, sub?, color?}[]} />`、`<Panel title={问题句} info?={口径说明} extra? />`、`<EChart option height />`（`echarts/core` 按需注册 line、bar 和 grid、tooltip、legend、markLine、dataZoom 组件；容器尺寸变化时 resize；主题色取 `usePalette()`）。
  - `usePolling<T>(fetcher: (signal: AbortSignal) => Promise<T>, opts: { intervalMs: number; minIntervalMs?: number; paused?: boolean }): { data: T | null; error: string | null; fetchedAt: Date | null; loading: boolean; refresh(): void; paused: boolean; setPaused(b: boolean): void }`。
    - 实际间隔取 `max(intervalMs, minIntervalMs ?? 0)`。
    - 失败时保留上一次数据，error 非空，下次间隔翻倍，上限 120 秒；成功后恢复原间隔。
    - 标签页隐藏（`document.hidden`）时不发请求。
    - 用 `registerPageRefresh(refresh)` 注册，`R` 键可触发刷新。
  - `<Freshness at={Date | null} paused onTogglePause onRefresh error? />`：显示“数据截至 hh:mm:ss · n 秒前”；有 error 时显示黄色“刷新失败，显示的是上一次结果”。
  - `registerPageRefresh(fn: () => void): () => void`、`refreshPage(): void`（`frontend/src/shell/refresh.ts`）。

**设计说明：** 顶栏状态读取失败时不沿用旧结果（计划 01）。这里的页面数据刷新失败时保留上一次结果，但必须用 Freshness 明确标出“刷新失败”和数据时间。两者都不把旧数据当新数据展示。

- [ ] **Step 1: 写失败测试**

```ts
// series.test.ts
test('toSeries keeps nulls and order', () => {
  const rows = [{ d: '11-13', v: 1 }, { d: '11-14', v: null }, { d: '11-17', v: undefined }, { d: '11-18', v: NaN }]
  expect(toSeries(rows, (r) => r.d, (r) => r.v as number | null)).toEqual([['11-13', 1], ['11-14', null], ['11-17', null], ['11-18', null]])
})
test('clipFuture drops points after now', () => {
  const pts: [string, number | null][] = [['2026-11-17', 1], ['2026-11-18', 2]]
  expect(clipFuture(pts, new Date(2026, 10, 17, 20), (x) => new Date(x + 'T15:00:00'))).toEqual([['2026-11-17', 1]])
})
// format.test.ts
test('formatters return null for null and keep sign', () => {
  expect(pct(null)).toBeNull(); expect(pct(0.623)).toBe('62.3%')
  expect(signedPct(0.0123)).toBe('+1.23%'); expect(signedPct(-0.005)).toBe('-0.50%')
  expect(yi(123_400_000_000)).toBe('1234 亿'); expect(int(5441)).toBe('5,441')
})
// Value.test.tsx
test('Value renders Missing for null and Num otherwise', () => { /* null → 文本“缺失”；0 → 文本“0”，绝不显示缺失 */ })
// usePolling.test.tsx（vi.useFakeTimers）
test('usePolling respects minIntervalMs', () => { /* intervalMs 5000, minIntervalMs 15000 → 15 秒内只调用 1 次 */ })
test('usePolling keeps last data on failure, sets error and backs off', () => { /* 成功→失败：data 保留、error 非空；下一次在 2× 间隔后 */ })
test('usePolling pauses and resumes', () => {})
```

- [ ] **Step 2: 运行，确认失败**

Run: `cd frontend && npm test`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现以上组件与工具；AppShell 的 `R` 改为 `() => { shell.refresh(); refreshPage() }`**

- [ ] **Step 4: 运行测试与类型检查**

Run: `cd frontend && npm test && npm run typecheck`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/page frontend/src/shell/refresh.ts frontend/src/shell/AppShell.tsx
git commit -m "feat(web): 新前端页面通用组件与缺失/断开/不画未来规则（#662）"
```

---

### Task 2: 市场总览只读接口 `/api/app/market`

**Files:**
- Create: `src/qsys/web/read_cache.py`（`SingleFlightCache`）
- Create: `src/qsys/web/app_market.py`（`market_payload`、`register_app_market`）
- Modify: `src/qsys/web/app.py`（在 `mount_spa` 之前调用 `register_app_market(app, market)`）
- Test: `tests/web/test_app_market.py`、`tests/web/test_read_cache.py`

**Interfaces:**
- Consumes: `MarketService.live()`、`.schedule()`、`.limit_check()`、`.history(limit)`（`src/qsys/web/data_views.py`）
- Produces:
  - `SingleFlightCache[T](fn: Callable[[], T], ttl_seconds: float, clock=time.monotonic)`，方法 `get() -> T`。线程安全，TTL 内只调用一次 `fn`。`fn` 抛异常时异常原样向上抛，且不缓存。
  - `GET /api/app/market` 返回（`Cache-Control: no-store`）：

```json
{
  "enabled": true,
  "live": {"available": true, "stale": false, "snapshot": {...MarketOverviewSnapshot 原样 JSON...} | null},
  "schedule": {"is_trading_day": true, "is_open": false, "next_open_at": "..."} | null,
  "limit_check": {...MarketService.limit_check() 原样...} | null,
  "regime": [{"dt": "2026-11-17", "limit_up_count": 54, ..., "index_ma20_slope_5d": null}, ...],
  "regime_columns": ["dt", "limit_up_count", ...],
  "missing": {
    "index_intraday": "行情快照只保存当前值，没有分时序列；需采集器保存（见 Issue）",
    "return_distribution": "行情快照没有涨跌幅分布；需采集器提供（见 Issue）"
  }
}
```

  - `regime` 按 dt 升序，最多 60 行。每行包含 `schema.MARKET_REGIME` 的全部列，null 保留为 JSON null，不丢行、不丢字段。
  - live、schedule、limit_check 三项来自 Redis，通过同一个 `SingleFlightCache`（TTL 15 秒）读取。regime 读 warehouse，通过另一个缓存（TTL 300 秒）读取。
  - 任一部分读取异常时，该部分为 null，并在 `errors: {部分名: "读取失败"}` 中注明，其他部分照常返回。错误信息不回显异常文本或路径。
  - `market is None` 时返回 `{"enabled": false}`，状态码 200。

- [ ] **Step 1: 写失败测试**

```python
def test_single_flight_cache_shares_one_call_within_ttl(): ...      # 8 线程 × 32 次 → fn 调用 1 次；推进 ttl 后再调用 1 次
def test_single_flight_cache_does_not_cache_errors(): ...           # 第一次抛错 → 再 get 会重新调用
def test_app_market_disabled(client_without_market):                # {"enabled": false}，200
def test_regime_rows_keep_nulls(fake_market):                       # 构造某天 index_ma20_slope_5d=None → 该行该键为 null，行数不变
def test_regime_sorted_ascending_and_capped_at_60(fake_market): ...
def test_live_parts_are_cached_for_15s(fake_market, clock): ...     # 两次请求只读一次 live/schedule/limit_check
def test_part_failure_is_isolated_and_not_leaked(fake_market): ...  # live() 抛 RuntimeError("/secret/path") → live 为 null、errors.live == "读取失败"、正文不含 "secret"
def test_endpoint_no_store(fake_market): ...
```

`fake_market` 是实现了 `live/schedule/limit_check/history` 的计数桩，直接传给 `create_app(..., market=fake)`。

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/web/test_read_cache.py tests/web/test_app_market.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

regime 不用 `MarketService.series()`，它会丢弃 null。改用 `history(60)` 并在接口里升序排序，日期序列化为 ISO 字符串。

- [ ] **Step 4: 运行本文件与全部 Web 测试**

Run: `uv run pytest tests/web -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/qsys/web/read_cache.py src/qsys/web/app_market.py src/qsys/web/app.py tests/web/test_read_cache.py tests/web/test_app_market.py
git commit -m "feat(web): 市场总览只读接口，Redis 部分经 15 秒服务端缓存（#662）"
```

---

### Task 3: 市场总览页面

**Files:**
- Create: `frontend/src/api/market.ts`（类型与 `fetchMarket`）
- Create: `frontend/src/pages/market/MarketPage.tsx`、`frontend/src/pages/market/TrendChart.tsx`、`frontend/src/pages/market/CoverageTable.tsx`、`frontend/src/pages/market/LimitCheck.tsx`
- Create: `frontend/src/pages/registry.ts`（`PAGE_COMPONENTS: Partial<Record<string, ComponentType>>`）
- Modify: `frontend/src/shell/AppShell.tsx`（有组件就渲染组件，否则渲染 Placeholder）
- Test: `frontend/src/pages/market/MarketPage.test.tsx`

**Interfaces:**
- Consumes: Task 1 全部组件；Task 2 的 `/api/app/market` JSON
- Produces: `PAGE_COMPONENTS.market = MarketPage`；`registry.ts` 供 Task 4 及后续计划继续登记页面

**页面结构（§6.1、§8.8）：**
- **结论条**：
  - 标题：“涨停 54 · 跌停 3 · 上涨占比 62.3%”。
  - 副标题：快照时间、“有效 5 438 / 应有 5 441”。应有取 `coverage.expected`；为 null 时显示“应有 未知”。
  - 状态：快照可用且未过期为正常；过期为警告，标“已过期”；不可用为待运行，标题“无实时快照”；休市时标“休市 · 最近快照”。
  - KPI：触板/炸板、炸板率、中位涨幅、成交额（亿）、缺涨跌停价数、封板未知（涨/跌，null 显示缺失）。
- **指数卡片**：取 `snapshot.indices`，按 A 股惯例涨红跌绿。
- **“近 60 日市场情绪怎样？”**：
  - 涨停用实心柱，炸板用虚线框柱，跌停用柱，上涨占比用折线（右轴）。
  - HS300 MA20 五日斜率单独成一个小图。
  - 全部用 `toSeries` 生成，`clipFuture` 裁掉未来，null 断开。
  - ⓘ 写明数据来自 `market_regime_daily`，为收盘后直取派生。
- **“上证分时”“涨跌幅分布”**：两个面板直接渲染 `<Missing why={missing.index_intraday}>` 和 `<Missing why={missing.return_distribution}>`。执行时开 GitHub Issue 登记这两项采集需求（`area:web`、`area:data`），并把 Issue 号写进 Task 2 的 missing 文案。
- **“各市场行情覆盖全了吗？”**：
  - 覆盖表每行一个市场（沪、深、北），列：应有、有效、缺失、待核验（无效 + 其他交易日 + 停牌）、源时间范围。
  - `coverage` 为 null 时整表替换为“生产者未绑定覆盖信息，无法判断覆盖是否完整”。
  - `expected` 为 null 时显示“未知”（#588）。
- **“涨停价核对”**：照搬旧页 limit_check 的信息，包括核对数、不一致数与样本、无涨跌幅限制等价数、QMT 未覆盖（名录内/外）。为 null 时显示“今日核对结果未发布”。
- **轮询**：`usePolling(fetchMarket, { intervalMs: 5000, minIntervalMs: 15000 })`；页头放 Freshness。
- **未启用**：`enabled === false` 时显示 `Result`“行情控制台未启用”，并给出旧页链接。

- [ ] **Step 1: 写失败测试**（mock `fetch`）

```tsx
test('market hero shows counts, coverage and snapshot time', async () => { /* 标题含“涨停 54”“跌停 3”“62.3%”，副标题含“有效 5,438 / 应有 5,441” */ })
test('market page renders history without live snapshot', async () => { /* live.snapshot=null → “无实时快照”，趋势面板仍渲染 */ })
test('coverage null shows unbound, never zeros', async () => { /* coverage=null → “未绑定覆盖信息”；expected=null 的行显示“未知” */ })
test('intraday and distribution panels show 缺失 with reason', async () => {})
test('trend series keeps null days as gaps', async () => { /* 捕获传给 EChart 的 option：slope 序列含 null，connectNulls 为 false */ })
test('market disabled shows message', async () => {})
```

捕获 option 的方式：`vi.mock('../../page/EChart', ...)`，把收到的 option 存到数组里，再对它断言。

- [ ] **Step 2: 运行，确认失败**

Run: `cd frontend && npm test`
Expected: FAIL

- [ ] **Step 3: 实现页面与注册表**

- [ ] **Step 4: 运行测试、类型检查与构建**

Run: `cd frontend && npm test && npm run typecheck && npm run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/market.ts frontend/src/pages frontend/src/shell/AppShell.tsx
git commit -m "feat(web): 新前端市场总览页（#662）"
```

---

### Task 4: AI 涨停页面

**Files:**
- Create: `frontend/src/api/ailu.ts`（`fetchShadow(day?)`、`fetchResearch(day?)`，分别调用现有的 `/api/market/limit-up-shadow` 与 `/api/market/limit-up-research`）
- Create: `frontend/src/pages/ailu/AiluPage.tsx`、`frontend/src/pages/ailu/ShadowView.tsx`、`frontend/src/pages/ailu/ResearchView.tsx`
- Modify: `frontend/src/pages/registry.ts`（`ailu`）
- Test: `frontend/src/pages/ailu/AiluPage.test.tsx`

**Interfaces:**
- Consumes: 现有接口的 `LimitUpShadowView` 与 `LimitUpResearchView` JSON（字段见 `src/qsys/web/limit_up_shadow.py`、`src/qsys/web/limit_up_research.py`），以及 Task 1 的组件。
- Produces: `PAGE_COMPONENTS.ailu = AiluPage`

**说明：** 这两个接口只读本地 SQLite 和文件，不读 Redis，可以直接复用，不新增后端。

**页面结构（§8.12）：**
- **顶部常驻提示**（antd `Alert` info，不可关闭）：“不是交易信号，不进入实盘或模拟决策。”
- 两个子标签，状态写进 URL 查询参数 `?tab=shadow|research&day=YYYY-MM-DD`：
  - “影子榜单”（默认）
  - “研究回测”

**影子榜单子标签：**
- **结论条状态映射**（与后端 `ViewState` 一一对应）：

| 后端状态 | 结论条 |
|---|---|
| ready | 正常 |
| stale | 警告，“已过期” |
| generating | 运行中 |
| waiting | 待运行 |
| failed | 失败，显示 message |
| unavailable | 待运行，显示 message |

- **结论条 KPI**：
  - 预测日 → 目标交易日、模型版本、可选股票数。
  - 校准误差 `calibration_error` 与漂移分数 `drift_score`，为 null 时显示缺失。
- **“今天模型看好谁？”榜单表**：
  - 列：排名、名称/代码、板块、触板概率 × 触板后封板 ＝ 收盘封板（三列，百分比）、实际结果（是否触板、是否封板、开盘即涨停、一字板）。
  - 实际结果为 null 时显示“未出结果”。目标交易日未收盘时，表头标“盘中快照 / 未出结果”。
- **“模型最近准不准？”**：
  - 用 `history` 的 `precision_at_20` 画逐日折线，null 断开，并裁掉未来。
  - 基线、10 日滚动、校准曲线、特征漂移序列：显示缺失，原因写“影子榜单记录尚未提供基线与分桶校准数据”。执行时开 Issue 登记，并在文案中写入 Issue 号。
- **历史列表**：点击某一天，设置 `day` 并重新取数；回看时顶部显示“正在查看 YYYY-MM-DD”与“回到最新”。

**研究回测子标签：**
- **结论条**：样本区间、TEST/OBS 会话数、OBS 月胜率、是否已晋级影子。
- **“月度稳定吗？”**：月度 `hits / predictions` 柱图，按 split 分色。
- **逐日列表**：点击后显示该日榜单与实际结果；页面底部显示 `source_sha` 与 `data_sha` 收据。

**轮询：** 影子榜单 `intervalMs: 60000`（榜单按日生成）；研究回测不轮询（冻结数据）。

- [ ] **Step 1: 写失败测试**

```tsx
test('ailu always shows the not-a-trading-signal notice', async () => {})
test('ailu hero maps every view state', async () => { /* 逐一给 6 种 state，断言 StatusTag 文本与符号 */ })
test('shadow table multiplies probabilities and shows 未出结果 for null outcomes', async () => {})
test('precision chart keeps nulls and baseline shows 缺失', async () => {})
test('failed shadow shows error message and no table', async () => {})
test('research tab shows monthly hits and receipts', async () => {})
```

- [ ] **Step 2: 运行，确认失败**

Run: `cd frontend && npm test`
Expected: FAIL

- [ ] **Step 3: 实现**

- [ ] **Step 4: 运行测试、类型检查与构建**

Run: `cd frontend && npm test && npm run typecheck && npm run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/ailu.ts frontend/src/pages
git commit -m "feat(web): 新前端 AI 涨停页（影子榜单与研究回测）（#662）"
```

---

### Task 5: 页面级布局冒烟与本地联调

**Files:**
- Modify: `frontend/e2e/layout.spec.ts`（对 `/app/market` 与 `/app/ailu` 拦截接口返回固定 JSON，在 390/1280/1600 三个宽度下断言无整页横向溢出）
- Create: `frontend/e2e/fixtures/market.json`、`frontend/e2e/fixtures/shadow.json`、`frontend/e2e/fixtures/research.json`（结构取自后端模型，数值为测试用，不来自原型示意数据生成器）

- [ ] **Step 1: 写用例并运行**

Run: `cd frontend && npm run build && npx playwright test`
Expected: 新用例 PASS。为证明用例有效，做一次变异检查：临时给市场页加一个 2000px 宽的元素，确认 390 用例失败，然后撤销。

- [ ] **Step 2: 本地联调**

用计划 01 的只读预览方式启动：空 warehouse、不连 Redis。打开 `/app/market` 与 `/app/ailu`。
Expected:
- 市场页结论条显示“无实时快照”；近 60 日趋势为空时显示“暂无历史数据”而不是报错；两个缺失面板显示原因。
- AI 涨停页显示后端 unavailable 的 message 与常驻提示。

- [ ] **Step 3: Commit 并推送需求分支**

```bash
git add frontend/e2e
git commit -m "test(web): 市场总览与 AI 涨停页布局冒烟（#662）"
git push
```

---

## 完成后

- 在 #662 评论记录：完成情况、执行中的裁决、新开的两个数据需求 Issue 号。
- 本计划不涉及任何生产组件或控制面改动，留在 `feat/web-redesign`。
