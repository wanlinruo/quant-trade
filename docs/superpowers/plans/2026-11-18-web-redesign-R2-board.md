# Web 改版 R2：打板看板迁到新契约与查询层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/api/app/board` 与 `/api/app/board/series` 迁到 `qsys.webapp`：读取器迁入查询层，响应用显式契约（带 `schema_version`），前端改用生成的类型。

**Architecture:**
- 采集器状态目录的只读读取器 `LimitUpBoardReader` 整体迁到 `qsys/webapp/queries/board_reader.py`，内部读模型（`BoardView` 等）保持不变，只作为查询层的内部结构。
- 新增 `contracts/board.py` 定义对外契约，`queries/board.py` 的 `BoardQuery` 负责缓存、赚钱效应派生、前一交易日晋级率和交易时段，并把内部读模型映射为契约。
- `routes/board.py` 是薄路由。删除 `web/app_board.py`、`web/board_effect.py`。
- 旧页面 `/market/limit-up-board` 与 `/api/market/limit-up-board*` 不改行为，只改导入路径；合入时随旧页一起删除。

**Tech Stack:** FastAPI、pydantic 2、pytest；前端 React + TS，不新增依赖。

**Spec:**
- `docs/superpowers/plans/2026-11-18-web-redesign-roadmap-v2.md`（第 2 节 `web/limit_up_board.py` 一行、第 3 节、第 4 节 R2）
- `docs/superpowers/plans/2026-11-18-web-redesign-R1-webapp-contracts.md`（统一约定与做法）
- `docs/superpowers/specs/2026-11-17-web-redesign-design.md`（§8.9、§10）
- `docs/superpowers/plans/2026-11-18-web-redesign-08b-limit-up-board.md`（已定口径）

## Global Constraints

- 沿用 R1 的统一约定：
  - 时间一律带时区（无时区的存储时间按 Asia/Shanghai 解释）；
  - 缺失为 `null`；比例取 0–1；
  - 错误体为 `{"error": {"code", "message"}}`；GET 一律 `Cache-Control: no-store`。
- 契约继承 `Contract`（`extra="forbid"`、`frozen=True`），每个顶层响应带 `schema_version: Literal[1]`，并登记进 `EXPORTED`，重新生成 `contracts.gen.ts`。
- 08b 已定的口径不变：
  - 赚钱效应：断板为 rush/missed，按今日涨跌幅 > 0 记红、≤ 0 记绿；大面为 ≤ −5%；无报价单独计数，不当 0。
  - 前一交易日晋级率取该日看板最终时刻，按前一交易日缓存 300 秒。
  - 最新视图走 15 秒单飞缓存；回放与回看按请求计算。
  - 交易时段取 `MarketQuery.schedule()` 的同一份缓存，不按请求读 Redis。
- 内部指纹不进契约：`meta.concept_sha` 不输出。
- 采集器 `status.json` 不进本契约（前端未使用；运维组计划 07 另行定义）。
- 读取逻辑本身（回放、归列倒推、封板判定、对账差异）不改；不新增推断。
- 不改任何生产组件。

## Review Focus

1. **浏览器不在东八区**：看板全部时刻（`at`、`times`、首封、时间轴、分时点）输出 `+08:00`，前端按市场时区显示。归 Task 2 `test_board_times_have_shanghai_offset`。
2. **回放参数带时区**（如 `at=2026-11-13T01:31:15Z`）：与无时区的 `09:31:15` 取到同一快照。归 Task 2 `test_replay_at_with_offset_matches_local_time`。
3. **非法参数**：`symbol` 不符合 `代码.SH|SZ|BJ`、日期非法，返回 422 统一错误体，不进读取器。归 Task 2 `test_series_invalid_params_are_422`。
4. **读取器内部模型新增字段而契约没跟上**：映射不得静默丢字段。只允许显式排除 `status` 与 `meta.concept_sha`，其余字段必须进契约，否则测试失败。归 Task 2 `test_contract_covers_reader_fields`。
5. **未配置、采集器尚无数据、无该交易日**：200，返回 `available=false` 的变体并带原因；未配置时分时同样返回 `available=false`。归 Task 2 `test_board_unavailable_variants`。

---

### Task 1: 读取器迁入查询层

**Files:**
- Move: `src/qsys/web/limit_up_board.py` → `src/qsys/webapp/queries/board_reader.py`（`git mv`，内容不改，只改模块文档首行注明位置）
- Move: `tests/web/test_limit_up_board.py` → `tests/webapp/test_webapp_board_reader.py`；`tests/web/board_fixtures.py` → `tests/webapp/board_fixtures.py`
- Modify 导入：`src/qsys/web/app.py`、`src/qsys/web/monitor_cli.py`、`src/qsys/web/board_effect.py`、`tests/web/test_app_board.py`、`tests/web/test_board_effect.py`、`tests/webapp/test_webapp_market.py`，以及其他 `grep -rn "qsys.web.limit_up_board\|tests.web.board_fixtures"` 命中处

- [ ] **Step 1:** 移动文件并改导入。
- [ ] **Step 2:** 运行 `uv run pytest tests/web tests/webapp -q`，全部通过；`grep -rn "qsys.web.limit_up_board\|tests.web.board_fixtures" src tests` 无结果。
- [ ] **Step 3: Commit**：`refactor(web): 打板看板读取器迁入新后端查询层（#662）`

---

### Task 2: 契约、查询、路由

**Files:**
- Create: `src/qsys/webapp/contracts/board.py`、`src/qsys/webapp/queries/board.py`、`src/qsys/webapp/queries/board_effect.py`（自 `web/board_effect.py` 改写，返回契约模型）、`src/qsys/webapp/routes/board.py`
- Modify:
  - `src/qsys/webapp/app.py`：`WebappDeps` 增加 `board: Any | None = None`（`board_reader.LimitUpBoardReader`），`mount_webapp` 挂 `board_router(BoardQuery(deps.board, deps.market))`
  - `src/qsys/webapp/contracts/registry.py`：登记 `Board`、`BoardUnavailable`、`BoardSeries`
  - `src/qsys/web/app.py`：删除 `register_app_board` 调用；`create_app` 内构造的默认 `WebappDeps` 传入 `board=limit_up_board`
  - `src/qsys/web/monitor_cli.py`：构造 `WebappDeps` 时传入同一个 `LimitUpBoardReader` 实例（旧页与新接口共用）
  - `frontend/src/api/contracts.gen.ts`：重新生成
- Delete: `src/qsys/web/app_board.py`、`src/qsys/web/board_effect.py`、`tests/web/test_app_board.py`、`tests/web/test_board_effect.py`（用例迁入下列新测试）
- Test: `tests/webapp/test_webapp_board.py`、`tests/webapp/test_webapp_board_effect.py`

**契约（`contracts/board.py`，字段即约定）：**
- 子模型与读取器内部模型同名同字段，时间改为 `Timestamp`：
  - `BoardSentiment`：对应 `Sentiment`，`promote_*` 为 `tuple[int | None, int | None]`
  - `BoardCard`：对应 `Card`，`diffs: dict[str, str]`
  - `BoardColumn`、`LadderRow`、`CompareItem`、`CompareGroup`
  - `BoardShadow`：对应 `ShadowInfo`
  - `BoardEvent`：对应 `TimelineEvent`
  - `ConceptCurve`：`points: list[tuple[Timestamp, int]]`
- `BoardMeta`：
  - `prev_day: date|None`
  - `concept_fetched_at`、`compacted_at`、`reconciled_at`：`Timestamp|None`
  - `concept_error`、`heights_error`、`compact_error`、`reconcile_error`：`str|None`
  - `st_published: bool`、`has_raw: bool`
- `EffectItem{symbol, name, pct: float|None, status}`
- `MoneyEffect{total, priced, no_quote, avg_pct, red_rate, multi_avg_pct, big_loss, top_level, top_items: list[EffectItem]}`
- `LayerRow{level, total, promoted, broken, break_up, break_down, limit_down, unknown, no_quote, rate, prev_rate, avg_pct}`
- `Board`（`available: Literal[True]`）：
  - `schema_version`、`message`、`day: date`、`days: list[date]`
  - `mode: Literal["live","final","history"]`、`provisional: bool`（= mode == "live"）
  - `at: Timestamp|None`、`times: list[Timestamp]`、`meta: BoardMeta`
  - `sentiment: BoardSentiment|None`、`columns`、`rows`、`cards: dict[str, BoardCard]`
  - `compare`、`compare_unknown: int`、`shadow: BoardShadow|None`
  - `timeline: list[BoardEvent]`、`curves`、`mismatch_count: int`
  - `effect: MoneyEffect|None`、`layers: list[LayerRow]`
  - `schedule: MarketSchedule|None`（复用 `contracts.market.MarketSchedule`）
  - `errors: dict[str, Literal["读取失败"]]`
  - 原顶层 `prev_day` 删除，统一读 `meta.prev_day`。
- `BoardUnavailable`（`available: Literal[False]`）：`schema_version`、`message`、`day: date|None`、`days: list[date]`。路由返回 `Board | BoardUnavailable`。
- `SeriesPoint{at: Timestamp, price: float, volume: int|None}`、`SeriesEvent{at: Timestamp, kind, detail}`
- `BoardSeries{schema_version, available: bool, message, symbol, name, limit_up, prev_close, first_seal: Timestamp|None, points, events}`

**查询 `BoardQuery(reader, market=None, *, latest_ttl=15.0, prev_ttl=300.0)`（`queries/board.py`）：**
- `board(day: date|None, at: datetime|None) -> Board | BoardUnavailable`：
  - `at` 带时区时先换算到 Asia/Shanghai 再去掉时区，然后交给读取器（存储里是无时区本地时间）。
  - 不带 day 与 at 的最新视图走单飞缓存；`schedule` 每次取 `market.schedule()`（market 为 None 或抛错时为 None）。
- `series(day: date, symbol: str) -> BoardSeries`
- reader 为 None 时：`BoardUnavailable(message="打板看板数据源未配置", day=None, days=[])`；分时为 `BoardSeries(available=False, message="打板看板数据源未配置", symbol=symbol, name="", …空)`。
- 映射：
  - 视图转 `Board` 用 `view.model_dump(exclude={"status", "available"})`，去掉 `meta.concept_sha` 后 `model_validate`。
  - 其余字段全部进入契约，多出的字段由 `extra="forbid"` 报错。
- `board_effect.py`：
  - `money_effect(groups) -> MoneyEffect | None`
  - `layer_rows(groups, prev_rates: dict[int, float|None]) -> list[LayerRow]`
  - `layer_rates(rows: list[LayerRow]) -> dict[int, float|None]`
  - 口径与原实现逐项相同。

**路由（`routes/board.py`）：**
- `GET /api/app/board?day=&at=`
- `GET /api/app/board/series?day=&symbol=`：`symbol: str = Query(pattern=r"^[0-9A-Z]{1,10}\.(SH|SZ|BJ)$")`

- [ ] **Step 1: 写失败测试**（迁移 `test_app_board.py`、`test_board_effect.py` 的全部用例并改为新契约断言），并新增：
  - `test_board_times_have_shanghai_offset`：`at`、`times[0]`、`cards["A.SH"].first_seal`、`timeline[0].at` 均以 `+08:00` 结尾。
  - `test_replay_at_with_offset_matches_local_time`：`at=2026-11-13T01:31:15Z` 与 `at=2026-11-13T09:31:15` 的 `sentiment.published_at` 相同，为 `2026-11-13T09:31:15+08:00`。
  - `test_series_invalid_params_are_422`：`symbol=../x` 与 `day=2026-13-01` → 422，`error.code == "invalid_request"`。
  - `test_contract_covers_reader_fields`：`set(BoardView.model_fields) - {"status", "available"} == set(Board.model_fields) - {"schema_version", "provisional", "effect", "layers", "schedule", "errors"}`；`BoardMeta` 字段 = 读取器 meta 键 − `{"concept_sha"}`。
  - `test_board_unavailable_variants`：未配置、目录无库、请求不存在的交易日，均 200、`available is False`；最后一种带 `day` 与 `days`。
  - `test_board_response_validates_against_contract`：`Board.model_validate(resp.json())` 通过。
  - `test_concept_sha_not_exposed`：响应文本不含 `concept_sha`。
- [ ] **Step 2:** 运行 `uv run pytest tests/webapp -q`，确认新增用例失败。
- [ ] **Step 3:** 实现；登记契约；运行 `uv run python scripts/gen_frontend_contracts.py` 重新生成前端类型。
- [ ] **Step 4:** 运行 `uv run pytest tests/webapp tests/web -q`，全部通过。
- [ ] **Step 5: Commit**：`refactor(web): 打板看板迁到新契约与查询层（#662）`

---

### Task 3: 前端改用生成类型

**Files:**
- Modify: `frontend/src/api/board.ts`：
  - 删除手写类型，从 `./contracts.gen` 重新导出 `Board`、`BoardUnavailable`、`BoardSeries`、`BoardCard`、`CompareItem`、`CompareGroup`、`LayerRow`、`MoneyEffect`、`BoardSentiment`；
  - `export type BoardData = Board | BoardUnavailable`；`fetchBoardSeries` 返回 `BoardSeries`。
- Modify: `frontend/src/pages/board/*.tsx`：
  - 只在 `available` 为真时渲染的组件（DayControls、LadderMatrix、CompareView、SidePanels、SentimentBar）props 改为 `Board`；
  - `BoardPage.heroOf` 用 `d.available` 判别；`meta` 直接读类型化字段，删除 `as` 断言；
  - `Card` 改名 `BoardCard`，`SeriesData` 改为 `BoardSeries`。
- Modify: 样例数据，由后端测试夹具（`tests/webapp/board_fixtures.build` + `build_prev`）经新接口导出：
  - `frontend/src/pages/board/board.sample.json`、`series.sample.json`
  - `frontend/e2e/fixtures/board.json`、`board-series.json`
  - `fixtures.ts` 注释写明来源。
- Modify: 受影响的组件测试（`prev_day` 改读 `meta.prev_day`；时间串带 `+08:00`）。

- [ ] **Step 1:** 改类型与样例，运行 `npm run typecheck`，按报错逐个修正组件。
- [ ] **Step 2:** 运行 `npm test && npm run typecheck && npm run build && npx playwright test`，全部通过。
- [ ] **Step 3: Commit**：`refactor(web): 打板看板前端改用契约生成类型（#662）`

---

### Task 4: 收尾

- [ ] `grep -rn "app_board\|board_effect\|qsys.web.limit_up_board" src tests frontend/src` 只剩新模块中的引用。
- [ ] 全量 `uv run pytest -q`；前端全套检查。
- [ ] 本地只读预览打开 `/app/board`：最新、回看、回放、分时抽屉正常，时刻显示正确。
- [ ] 推送需求分支；在 #662 记录。
