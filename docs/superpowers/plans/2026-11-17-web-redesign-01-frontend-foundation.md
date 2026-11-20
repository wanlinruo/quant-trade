# Web 改版 01：前端工程与全局框架 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在仓库中建立新前端工程（React + antd + ECharts + Lightweight Charts），由 CI 构建并打进 qsys wheel，经现有 FastAPI 在 `/app` 下提供全局框架（左侧 6 组导航、顶部全局状态栏、停机闸横幅、明暗主题、搜索与快捷键），顶栏状态来自一个带服务端缓存的只读接口。

**Architecture:** 前端源码放在 `frontend/`，`npm run build` 输出到 `src/qsys/web/spa_dist/`（gitignore，hatch 以 artifacts 方式打进 wheel）。FastAPI 新增 `/app` 路由提供单页应用、`/api/shell/status` 提供顶栏状态（复用 `LiveKillReader`，服务端缓存，不按请求直接读 Redis）。旧 Jinja 页面不动；19 个页面在本计划中只是占位页，链接到对应旧页面。

**Tech Stack:** Node 24 + npm、Vite 8、React 19、TypeScript 7、antd 6、ECharts 6、lightweight-charts 5、Vitest + Testing Library、Playwright（仅布局冒烟）；Python 3.11+、FastAPI、hatchling、pytest。

**Spec:** `docs/superpowers/specs/2026-11-17-web-redesign-design.md`（实现 §3 前端发布、§5 全局框架、§6.4 颜色、§10 读取边界的框架部分；页面内容由后续计划实现）

## 总路线（本计划是第 1 份）

| # | 计划 | 依赖 | 设计文档章节 |
|---|---|---|---|
| **01** | **前端工程与全局框架**（本计划；生产 web 组件发布即可上线 `/app`） | — | §3、§5、§6.4、§10 |
| 02 | 测试环境部署支持前端构建产物（节点侧 test-runtime 入口安装 CI 构建的 spa_dist；控制面引导） | 01 | §12 |
| 03 | live-core / health-observer 结构化记录（策略事件、09:25 漏斗、链路时间序列、带稳定键的告警）；#137 观察期后、收盘后发布 | — | §3、§8.1、§8.2 |
| 04 | 告警稳定键与已知警告受管清单（含基线判定）、告警汇总只读服务、“确认已知悉”存储 | 03 | §7.2、§8.2、§13.2 |
| 05 | 停机闸受管解除入口的机器可读核对输出 | — | §8.5、§13.6 |
| 06 | 第一期页面：今日运行、告警中心、交易组 5 页（含按日回看） | 01、03、04、05 | §8.1–§8.7、§5.5 |
| 07 | 运维组：采集器快照（资源、链路、进程、版本、安全核验）+ 3 页 | 01 | §8.14–§8.16、§13.3 |
| 08 | 行情与研究组 5 页 | 01 | §8.8–§8.12 |
| 09 | 数据组 4 页 + #442 性能治理；旧模板下线 | 01 | §8.13、§10 |

后续每份计划在前一份落地后再写，避免基于未落地接口的猜测。

## Global Constraints

- 前端只在 CI 构建；节点上不安装 Node；仓库不提交构建产物（`src/qsys/web/spa_dist/` 必须在 `.gitignore`）。
- 行情颜色涨红跌绿（#646）；状态色：正常绿、警告黄、故障/失败红、待运行灰、运行中蓝、休市暗灰；状态标签同时带符号 `● ▲ ✕ ○ ◐ —`。
- 明暗主题默认跟随系统，可手动切到白天或黑暗，选择存 `localStorage['qs-theme']`，取值 `system | light | dark`。
- 任何宽度下不得整页横向滚动；390 像素视口也不得溢出（#449）。
- 网页不提供任何改变实盘交易状态的操作；本计划只新增只读接口。
- Web 不按请求直接读 Redis：`/api/shell/status` 对停机闸与心跳读取做服务端缓存，缓存期 5 秒，并发请求共用一次读取。
- 拿不到的值返回 `null` 并附 `reason`，前端显示“缺失”，不推算、不给默认值。
- 新增或修改受 Git 管理的路径时，同时检查 `deploy/deployment.toml` 的路径分类：`frontend/**` 归入生产 `linux-web` 与测试 `test-runtime`。测试部署用可信 `main` 上的 planner，未分类路径会让规划失败，所以最终合入时 **Task 0 必须先单独合入 main**，之后才能把含 `frontend/` 的提交推到 test。
- **分支策略（维护者 2026-11-17 决定）**：改版全部在长期需求分支 `feat/web-redesign` 上开发（从 `origin/main` 拉出），开发期间**不合入 test / main**，避免干扰线上修复。定期把 `origin/main` merge 进本分支取得最新代码（main 不会被改写；仅在确需未上线修复时才 merge test）。本分支可推到 origin 作为备份，不触发 CI/部署；阶段性需要完整 CI 时手动 `workflow_dispatch`。会改变生产组件行为的后端工作（采集器写快照、live-core 结构化记录等）不放进本分支，单独开 `feat/` / `fix/` 走正常流程。整体合入时机与每一步合并由维护者确认。
- 改动 `.github/workflows/ci.yml` 与 `scripts/build_component_release.py` 属于控制面改动：先按 AGENTS.md §2.1 核对 source/controller 哈希与证据复用条件，再推进发布。
- 所有 GitHub Actions 引用固定到完整提交 SHA 并注释版本号（与 `ci.yml` 现有写法一致）。
- 提交信息遵循 Conventional Commits，正文中文；每个 Task 一个提交。

## Review Focus

1. 构建产物缺失（本地开发、测试环境、旧 wheel）时访问 `/app`：应返回 503 和中文说明“前端构建产物缺失”，旧页面照常可用——Task 3 `test_app_missing_build_returns_503_and_old_pages_still_work`。
2. 客户端路由深链接（如 `/app/orders`）刷新：应返回 `index.html`；而 `/app/assets/不存在.js` 应返回 404，不能把 HTML 当 JS 返回——Task 3 `test_deep_link_serves_index_but_missing_asset_404`。
3. 停机闸读取异常或过期：顶栏显示“停机状态未知”（红色），绝不显示“未触发”——Task 4 `test_unknown_kill_state_is_never_reported_clear`。
4. 多标签页同时轮询：5 秒内的多次请求只触发一次底层读取——Task 4 `test_concurrent_requests_share_one_read_within_ttl`。
5. 窄屏：390、1280、1600 像素下 `scrollWidth == clientWidth`——Task 6 `layout.spec.ts`。

---

### Task 0: 路径分类先行（最终合入时先单独发布到 main）

**Files:**
- Modify: `deploy/deployment.toml`（`linux-web.paths` 与 `test-runtime.paths` 各追加 `"frontend/**"`）
- Test: `tests/ops/test_deployment.py`

**Interfaces:**
- Produces: 可信 main 上的 planner 能分类 `frontend/**`。

- [ ] **Step 1: 写失败测试**

```python
def test_frontend_paths_belong_to_web_components():
    manifest = DeploymentManifest.load(MANIFEST)
    prod = manifest.plan(["frontend/src/main.tsx", "frontend/package-lock.json"], environment="production")
    assert {c.name for c in prod.components} == {"linux-web"}
    test = manifest.plan(["frontend/src/main.tsx"], environment="test")
    assert {c.name for c in test.components} == {"test-runtime"}
```

（`DeploymentPlan` 的组件字段名以 `src/qsys/ops/deployment.py` 实际定义为准。）

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/ops/test_deployment.py::test_frontend_paths_belong_to_web_components -v`
Expected: FAIL（未分类路径）

- [ ] **Step 3: 修改 `deploy/deployment.toml`**

- [ ] **Step 4: 运行全部部署测试**

Run: `uv run pytest tests/ops -q`
Expected: PASS

- [ ] **Step 5: Commit 并按流程单独发布**

```bash
git add deploy/deployment.toml tests/ops/test_deployment.py
git commit -m "chore(deploy): frontend 路径归入 linux-web 与 test-runtime（#662）"
```

开发期间随 `feat/web-redesign` 提交即可，不单独发布。最终合入时：先把本改动单独 squash 到 test → CI → 测试部署 → PR → 合并 main，确认 main 已包含后，再合入其余改版提交。

---

### Task 1: 前端工程骨架与设计令牌

**Files:**
- Create: `frontend/package.json`, `frontend/package-lock.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/index.html`, `frontend/src/main.tsx`
- Create: `frontend/src/theme/palette.ts`, `frontend/src/theme/status.ts`, `frontend/src/theme/antd.ts`
- Create: `frontend/src/ui/StatusTag.tsx`, `frontend/src/ui/Missing.tsx`, `frontend/src/ui/Num.tsx`
- Test: `frontend/src/theme/status.test.ts`, `frontend/src/ui/StatusTag.test.tsx`, `frontend/src/ui/Missing.test.tsx`
- Modify: `.gitignore`（追加 `frontend/node_modules/`、`src/qsys/web/spa_dist/`）

**Interfaces:**
- Produces:
  - `type Mode = 'dark' | 'light'`；`palette: Record<Mode, Palette>`，`Palette` 字段：`bg panel panel2 border grid text muted head up down ok warn bad idle info running accent line line2`
  - `type Status = 'ok' | 'warn' | 'bad' | 'idle' | 'running' | 'closed'`
  - `statusSymbol: Record<Status, string>` = `{ ok: '●', warn: '▲', bad: '✕', idle: '○', running: '◐', closed: '—' }`
  - `statusText: Record<Status, string>` = `{ ok: '正常', warn: '警告', bad: '失败', idle: '待运行', running: '运行中', closed: '休市' }`
  - `statusColor(p: Palette, s: Status): string`
  - `antdTheme(mode: Mode): ThemeConfig`
  - `<StatusTag s={Status} text?={string} />`、`<Missing why={string} />`（显示“缺失”，悬停显示 why）、`<Num>`（等宽数字）

可参考原型 `.superpowers/spike-redesign/src/theme.ts`、`ui.tsx` 的取值与样式；不得复制原型的示意数据生成逻辑。

- [ ] **Step 1: 建工程并锁定依赖**

`frontend/package.json` 的 scripts：`dev`（vite）、`build`（`tsc -b && vite build`）、`typecheck`（`tsc -b --noEmit`）、`test`（`vitest run`）。`vite.config.ts` 设 `base: '/app/'`、`build.outDir: '../src/qsys/web/spa_dist'`、`build.emptyOutDir: true`。依赖固定精确版本（无 `^`），生成 `package-lock.json`。

- [ ] **Step 2: 写失败测试**

```ts
// status.test.ts
test('every status has symbol, text and a distinct color in both modes', () => {
  for (const s of ['ok','warn','bad','idle','running','closed'] as Status[]) {
    expect(statusSymbol[s]).toBeTruthy(); expect(statusText[s]).toBeTruthy()
  }
  for (const m of ['dark','light'] as Mode[]) {
    const p = palette[m]
    expect(new Set(['ok','warn','bad'].map((s) => statusColor(p, s as Status))).size).toBe(3)
    expect(p.up).not.toBe(p.down)
  }
})
test('A-share convention: up is red-ish, down is green-ish', () => {
  for (const m of ['dark','light'] as Mode[]) {
    const [r1, g1] = hexRgb(palette[m].up); const [r2, g2] = hexRgb(palette[m].down)
    expect(r1).toBeGreaterThan(g1); expect(g2).toBeGreaterThan(r2)
  }
})
// StatusTag.test.tsx
test('StatusTag renders symbol plus text, not color alone', () => {
  render(<StatusTag s="bad" />); expect(screen.getByText(/✕/)).toHaveTextContent('✕ 失败')
})
// Missing.test.tsx
test('Missing shows 缺失 and exposes reason on hover', async () => { /* hover → 显示 why */ })
```

- [ ] **Step 3: 运行，确认失败**

Run: `cd frontend && npm ci && npm test`
Expected: FAIL（模块不存在）

- [ ] **Step 4: 实现 palette / status / antd 主题与三个 UI 组件**

- [ ] **Step 5: 运行，确认通过**

Run: `cd frontend && npm test && npm run typecheck`
Expected: 全部 PASS，无类型错误

- [ ] **Step 6: Commit**

```bash
git add frontend .gitignore
git commit -m "feat(web): 新前端工程骨架与设计令牌（#662）"
```

---

### Task 2: 构建产物打进 wheel 并带构建信息

**Files:**
- Create: `frontend/scripts/write-build-info.mjs`
- Modify: `frontend/package.json`（`build` 末尾执行 `node scripts/write-build-info.mjs`）
- Modify: `pyproject.toml`（`[tool.hatch.build.targets.wheel]` 加 `artifacts = ["src/qsys/web/spa_dist/**"]`）
- Test: `tests/web/test_spa_build_info.py`

**Interfaces:**
- Produces: `src/qsys/web/spa_dist/build-info.json`，结构 `{"schema_version": 1, "files": {"<相对路径>": "<sha256>"}, "index_sha256": "<sha256>"}`，`files` 覆盖除 `build-info.json` 外的全部文件；`qsys.web.spa.read_build_info(root: Path) -> dict | None`（缺失或校验失败返回 `None`）。

- [ ] **Step 1: 写失败测试**

```python
def test_read_build_info_accepts_matching_hashes(tmp_path): ...   # 构造 index.html + assets/a.js + 正确 build-info → 返回 dict
def test_read_build_info_rejects_tampered_file(tmp_path): ...     # 改动 a.js 内容 → None
def test_read_build_info_missing_returns_none(tmp_path): ...      # 无 build-info.json → None
def test_pyproject_ships_spa_dist_as_wheel_artifact():
    data = tomllib.loads(Path("pyproject.toml").read_text())
    assert "src/qsys/web/spa_dist/**" in data["tool"]["hatch"]["build"]["targets"]["wheel"]["artifacts"]
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/web/test_spa_build_info.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 `src/qsys/web/spa.py::read_build_info` 与 `write-build-info.mjs`，并改 pyproject**

- [ ] **Step 4: 运行测试并做一次真实构建**

Run: `uv run pytest tests/web/test_spa_build_info.py -v && (cd frontend && npm run build) && uv build && unzip -l dist/qsys-*.whl | grep spa_dist/index.html`
Expected: 测试 PASS；wheel 中包含 `qsys/web/spa_dist/index.html` 与 `build-info.json`

- [ ] **Step 5: Commit**

```bash
git add frontend/scripts frontend/package.json pyproject.toml src/qsys/web/spa.py tests/web/test_spa_build_info.py
git commit -m "build(web): 前端构建产物带哈希清单并打进 wheel（#662）"
```

---

### Task 3: FastAPI 在 `/app` 提供单页应用

**Files:**
- Modify: `src/qsys/web/spa.py`（新增 `mount_spa`）
- Modify: `src/qsys/web/app.py:142-170`（`create_app` 新增参数 `spa_root: Path | None = None`，默认 `Path(__file__).parent / "spa_dist"`，调用 `mount_spa`）
- Test: `tests/web/test_spa_routes.py`

**Interfaces:**
- Consumes: `read_build_info(root) -> dict | None`（Task 2）
- Produces: `mount_spa(app: FastAPI, root: Path) -> None`。路由：
  - `GET /app`、`GET /app/{path}`：`path` 以 `assets/` 开头时只返回 build-info 中登记的文件（`Cache-Control: public, max-age=31536000, immutable`），不存在返回 404；其他路径返回 `index.html`（`Cache-Control: no-store`）。
  - build-info 缺失或校验失败：`/app*` 返回 503，正文含“前端构建产物缺失”。
  - 只允许 GET/HEAD；路径含 `..` 或绝对路径返回 404。

- [ ] **Step 1: 写失败测试**

```python
def test_app_serves_index_with_no_store(spa_tmp, client_factory): ...
def test_deep_link_serves_index_but_missing_asset_404(spa_tmp, client_factory):
    c = client_factory(spa_tmp)
    assert c.get("/app/orders").text.startswith("<!doctype html")
    assert c.get("/app/assets/missing.js").status_code == 404
def test_registered_asset_is_immutable_cached(spa_tmp, client_factory): ...
def test_path_traversal_rejected(spa_tmp, client_factory):
    assert client_factory(spa_tmp).get("/app/assets/..%2f..%2fapp.py").status_code == 404
def test_app_missing_build_returns_503_and_old_pages_still_work(tmp_path, client_factory):
    c = client_factory(tmp_path / "none")
    r = c.get("/app"); assert r.status_code == 503 and "前端构建产物缺失" in r.text
    assert c.get("/healthz").status_code == 200
```

`client_factory` 复用 `tests/web/test_app.py` 现有的 `create_app(MonitorService(...), cfg, ...)` 构造方式，额外传 `spa_root`。

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/web/test_spa_routes.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 `mount_spa` 并接入 `create_app`**

- [ ] **Step 4: 运行本文件与全部 Web 测试**

Run: `uv run pytest tests/web -q`
Expected: 全部 PASS（旧页面测试不受影响）

- [ ] **Step 5: Commit**

```bash
git add src/qsys/web/spa.py src/qsys/web/app.py tests/web/test_spa_routes.py
git commit -m "feat(web): 在 /app 提供新前端，构建产物缺失时明确报 503（#662）"
```

---

### Task 4: 顶栏状态只读接口 `/api/shell/status`

**Files:**
- Create: `src/qsys/web/shell_status.py`
- Modify: `src/qsys/web/app.py`（注册路由，使用 `app.state.live_kill`）
- Test: `tests/web/test_shell_status.py`

**Interfaces:**
- Consumes: `LiveKillReader.read() -> LiveKillStatus`（`src/qsys/web/live_kill_status.py`，state ∈ `stopped | clear | unknown`，`sources` 中 `id == 'agent'` 的 `recorded_at` 为 agent 心跳时间）
- Produces:
  - `class ShellStatusCache(reader: LiveKillReader, ttl_seconds: float = 5.0, clock: Callable[[], float] = time.monotonic)`，方法 `get() -> dict`，线程安全，TTL 内复用同一次读取。
  - `GET /api/shell/status` 返回（`Cache-Control: no-store`）：

```json
{
  "schema_version": 1,
  "checked_at": "ISO 时间",
  "kill": {"state": "stopped|clear|unknown", "label": "...", "message": "...", "sources": [...]},
  "live": {"state": "trading|reduce_only|unknown", "label": "实盘运行|仅允许减仓|实盘状态未知"},
  "agent_heartbeat_at": "ISO 时间或 null",
  "qmt": null, "qmt_reason": "第 03 份计划提供结构化链路记录后接入",
  "alerts_open": null, "alerts_reason": "第 04 份计划提供告警汇总后接入"
}
```

  `live.state`：kill 为 `stopped` → `reduce_only`；`clear` → `trading`；`unknown` → `unknown`。本计划不判断开收盘（时钟阶段由前端按本地时间显示，不进接口）。

- [ ] **Step 1: 写失败测试**

```python
def test_status_maps_kill_states_to_live_posture(): ...            # stopped→reduce_only, clear→trading, unknown→unknown
def test_unknown_kill_state_is_never_reported_clear(): ...          # reader 抛异常或返回 unknown → kill.state=='unknown' 且 live.state=='unknown'
def test_concurrent_requests_share_one_read_within_ttl():
    reader = CountingReader(); cache = ShellStatusCache(reader, ttl_seconds=5, clock=fake_clock)
    with ThreadPoolExecutor(8) as ex: list(ex.map(lambda _: cache.get(), range(32)))
    assert reader.calls == 1
    fake_clock.advance(5.01); cache.get(); assert reader.calls == 2
def test_missing_fields_are_null_with_reason():                   # qmt/alerts_open 为 None 且 *_reason 非空
def test_endpoint_is_no_store_and_escapes_nothing_sensitive(client): ...  # 响应头 no-store；正文不含 KILL 文件路径
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/web/test_shell_status.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 `ShellStatusCache`（读取异常时返回 kill.state='unknown'，不抛到接口外）并注册路由**

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/web/test_shell_status.py tests/web/test_live_kill_status.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/qsys/web/shell_status.py src/qsys/web/app.py tests/web/test_shell_status.py
git commit -m "feat(web): 顶栏状态只读接口，服务端缓存停机闸读取（#662）"
```

---

### Task 5: 全局框架（导航、顶栏、横幅、主题、搜索、快捷键）

**Files:**
- Create: `frontend/src/shell/nav.ts`（6 组 19 页定义）、`frontend/src/shell/AppShell.tsx`、`frontend/src/shell/TopBar.tsx`、`frontend/src/shell/KillBanner.tsx`、`frontend/src/shell/ThemeProvider.tsx`、`frontend/src/shell/SearchPalette.tsx`、`frontend/src/shell/shortcuts.ts`、`frontend/src/shell/Placeholder.tsx`、`frontend/src/api/shell.ts`、`frontend/src/shell/shell.css`
- Test: `frontend/src/shell/nav.test.ts`、`frontend/src/shell/TopBar.test.tsx`、`frontend/src/shell/ThemeProvider.test.tsx`、`frontend/src/shell/shortcuts.test.ts`

**Interfaces:**
- Consumes: `GET /api/shell/status`（Task 4 的 JSON）；Task 1 的 `palette / statusColor / StatusTag / Missing`
- Produces:
  - `NAV: { group: string; pages: { key: string; label: string; legacy: string; phase: number }[] }[]`，6 组 19 页及其 `legacy`（旧页面路径）与上线期次，取值照设计文档 §4 表与 §12 分期（总览/交易＝第 1 期，运维＝第 2 期，行情/研究＝第 3 期，数据＝第 4 期）。
  - 路由：`/app/<key>`（如 `/app/orders`）；默认 `/app/today`。
  - `useShellStatus(): { data: ShellStatus | null; error: string | null; refresh(): void }`，每 5 秒轮询，可暂停。
  - `Placeholder`：显示“本页在第 N 期上线”与“打开现有页面 →”（链接 `legacy`）。

- [ ] **Step 1: 写失败测试**

```ts
test('NAV has 6 groups and 19 unique pages, each with legacy path and phase', () => {
  expect(NAV.map((g) => g.group)).toEqual(['总览','交易','行情','研究','数据','运维'])
  const pages = NAV.flatMap((g) => g.pages); expect(pages).toHaveLength(19)
  expect(new Set(pages.map((p) => p.key)).size).toBe(19)
  for (const p of pages) { expect(p.legacy).toMatch(/^\//); expect([1,2,3,4]).toContain(p.phase) }
})
test('TopBar shows red 停机状态未知 when kill state unknown, never 未触发', () => { /* mock status unknown */ })
test('TopBar shows 缺失 for qmt and alerts when null', () => {})
test('KillBanner appears only when kill.state is stopped and cannot be dismissed', () => {})
test('theme follows system by default and persists manual choice in qs-theme', () => {})
test('G then O navigates to /app/orders; ? opens help; shortcuts ignored inside inputs', () => {})
```

- [ ] **Step 2: 运行，确认失败**

Run: `cd frontend && npm test`
Expected: FAIL

- [ ] **Step 3: 实现框架组件**

顶栏顺序与窄屏规则照设计文档 §5.1：约 1560px 以下收起次要指标，约 1400px 以下搜索只留图标、时钟去掉阶段文字；顶栏 `overflow: hidden`，实盘状态、停机闸、日期、时钟始终保留。本计划不放日期选择器（第 06 份计划随按日回看一起加）。可参考原型 `.superpowers/spike-redesign/src/App.tsx` 的交互实现与 `index.css` 的响应式规则。

- [ ] **Step 4: 运行测试与类型检查**

Run: `cd frontend && npm test && npm run typecheck`
Expected: PASS

- [ ] **Step 5: 本地联调**

Run: `cd frontend && npm run build && cd .. && uv run python scripts/run_monitor.py --help >/dev/null && echo ok`，再按现有方式本地启动 monitor，打开 `http://127.0.0.1:<端口>/app`
Expected: 显示新框架；顶栏停机闸状态与 `/api/live/kill-status` 一致；点任一占位页的“打开现有页面”能进入旧页面

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(web): 新前端全局框架：分组导航、全局状态栏、停机闸横幅、主题与快捷键（#662）"
```

---

### Task 6: CI 构建前端、布局冒烟测试与路径分类

**Files:**
- Create: `frontend/playwright.config.ts`、`frontend/e2e/layout.spec.ts`
- Modify: `.github/workflows/ci.yml`（新增 `frontend` job；`package` job 在 `uv build` 前构建前端）
- Modify: `scripts/build_component_release.py:96-111`（`_validate_wheel` 对 `web` 组件额外要求 wheel 含 `qsys/web/spa_dist/index.html` 与 `qsys/web/spa_dist/build-info.json`）
- Test: `tests/scripts/test_build_component_release.py`

**Interfaces:**
- Consumes: Task 2 的 wheel artifacts 与 build-info；Task 5 的页面
- Produces: CI 中 `frontend` job（`npm ci`、`npm run typecheck`、`npm test`、`npm run build`、`npx playwright install --with-deps chromium`、`npx playwright test`）；`package` job 新增 setup-node 与 `cd frontend && npm ci && npm run build` 两步，位于 “Build wheel and source distribution” 之前。

- [ ] **Step 1: 写失败测试**

```python
# test_build_component_release.py
def test_web_release_rejects_wheel_without_spa(tmp_path): ...       # 无 spa_dist/index.html → ValueError("wheel does not contain the web frontend build")
def test_non_web_component_does_not_require_spa(tmp_path): ...
```

```ts
// e2e/layout.spec.ts：mock /api/shell/status 为 stopped，访问 /app/today
for (const width of [390, 1280, 1600]) test(`no horizontal overflow at ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 800 }); await page.goto('/app/today')
  const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth])
  expect(sw).toBe(cw)
})
```

Playwright 的 webServer 用 `vite preview`，`/api/shell/status` 用 `page.route` 拦截返回固定 JSON。

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/scripts/test_build_component_release.py -q && (cd frontend && npx playwright test)`
Expected: 新增用例 FAIL

- [ ] **Step 3: 实现校验与 CI job**

setup-node 固定到完整 SHA（用 `gh api repos/actions/setup-node/git/ref/tags/<版本>` 查实），`node-version: 24`，开启 npm 缓存（`cache-dependency-path: frontend/package-lock.json`）。

- [ ] **Step 4: 运行全部检查**

Run: `uv run pytest -q && (cd frontend && npm test && npx playwright test) && python scripts/check_repo_policy.py --head test --base main --title 'feat(web): 新前端工程与全局框架（#662）' --check-commits`
Expected: 全部 PASS，policy ok

- [ ] **Step 5: 控制面核对（不改代码，只记录）**

按 AGENTS.md §2.1：确认本次 `ci.yml` 改动不改变证据复用所依赖的 controller 哈希，或预先写明需要的人工对齐步骤；确认 Task 0 已在 main 上。把结论写进 #662 评论。

- [ ] **Step 6: Commit**

```bash
git add frontend/playwright.config.ts frontend/e2e .github/workflows/ci.yml scripts/build_component_release.py tests/scripts/test_build_component_release.py
git commit -m "ci(web): CI 构建前端并校验 web 制品包含构建产物（#662）"
```

---

## 发布与验收（本计划完成后）

- 本计划完成后留在 `feat/web-redesign`，不立即合入。整体合入时按 AGENTS.md 流程（Task 0 先行合入 main）：`feat/web-redesign` → squash 到 test → CI → 测试部署（测试环境此时 `/app` 应返回 503“前端构建产物缺失”，属预期；第 02 份计划解决）→ PR → 合并 → web 组件生产发布（收盘后）。
- 生产验收：`/app` 可打开；顶栏停机闸与 `/api/live/kill-status` 一致；旧页面全部照常；`/healthz` 正常；`qsys-monitor` 内存与 CPU 无明显变化。
- 验收层级分开记录：CI、测试部署（/app 503 预期）、生产页面人工验收。
